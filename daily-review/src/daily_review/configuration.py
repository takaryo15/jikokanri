"""Unified, backward-compatible configuration for daily-review v1.3."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .archive import load_backup_config
from .command_api import load_api_config
from .notifications import load_notification_config
from .scheduler import load_scheduler_config
from .storage import DEFAULT_PRIORITIES, read_json_file


CONFIG_VERSION = "1"
APP_CONFIG_NAME = "app.json"
DEFAULT_APP_CONFIG: dict[str, Any] = {
    "schema_version": CONFIG_VERSION,
    "timezone": "Asia/Tokyo",
    "week_start": "tuesday",
    "main_limit": 3,
    "data_root": ".",
}


class ConfigurationError(ValueError):
    """Raised when a user configuration cannot be used safely."""


def config_directory(root: Path) -> Path:
    return root / "config"


def app_config_path(root: Path) -> Path:
    return config_directory(root) / APP_CONFIG_NAME


def _known(defaults: dict[str, Any], custom: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(defaults))
    for key, value in custom.items():
        if key in result:
            result[key] = value
    return result


def load_app_config(root: Path) -> dict[str, Any]:
    path = app_config_path(root)
    if not path.exists():
        return dict(DEFAULT_APP_CONFIG)
    value = read_json_file(path)
    if not isinstance(value, dict):
        raise ConfigurationError("config/app.jsonはJSONオブジェクトにしてください")
    config = _known(DEFAULT_APP_CONFIG, value)
    if config["schema_version"] != CONFIG_VERSION:
        raise ConfigurationError(f"app.schema_versionは{CONFIG_VERSION}にしてください")
    try:
        ZoneInfo(config["timezone"])
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ConfigurationError("app.timezoneが不正です") from exc
    if config["week_start"] != "tuesday":
        raise ConfigurationError("app.week_startはtuesdayにしてください")
    if (
        not isinstance(config["main_limit"], int)
        or isinstance(config["main_limit"], bool)
        or not 1 <= config["main_limit"] <= 3
    ):
        raise ConfigurationError("app.main_limitは1〜3にしてください")
    if not isinstance(config["data_root"], str) or not config["data_root"].strip():
        raise ConfigurationError("app.data_rootは空でない文字列にしてください")
    return config


def load_priorities_config(root: Path) -> dict[str, Any]:
    path = config_directory(root) / "priorities.json"
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_PRIORITIES))
    value = read_json_file(path)
    priorities = value.get("priorities") if isinstance(value, dict) else None
    if (
        not isinstance(priorities, list)
        or not priorities
        or not all(isinstance(item, str) and item.strip() for item in priorities)
        or len(priorities) != len(set(priorities))
    ):
        raise ConfigurationError(
            "priorities.prioritiesは重複のない空でない文字列の配列にしてください"
        )
    return {"priorities": priorities}


def load_effective_config(root: Path) -> dict[str, Any]:
    """Load and validate every supported configuration section."""
    return {
        "schema_version": CONFIG_VERSION,
        "app": load_app_config(root),
        "priorities": load_priorities_config(root),
        "notifications": load_notification_config(root),
        "scheduler": load_scheduler_config(root),
        "backup": load_backup_config(root),
        "api": load_api_config(root),
        "safety": {
            "explicit_confirmation": True,
            "backup_before_restore": True,
            "atomic_writes": True,
            "automatic_instruction_approval": False,
        },
    }


def validate_config(root: Path) -> dict[str, Any]:
    try:
        config = load_effective_config(root)
    except (OSError, ValueError) as exc:
        return {"valid": False, "errors": [str(exc)], "config": None}
    return {"valid": True, "errors": [], "config": config}


def normalize_setup_payload(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ConfigurationError("setup設定はJSONオブジェクトにしてください")
    allowed = {
        "app",
        "priorities",
        "notifications",
        "scheduler",
        "recovery",
        "api",
        "launchd",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigurationError("未対応のsetup設定です: " + ", ".join(unknown))
    payload: dict[str, dict[str, Any]] = {}
    for section, settings in value.items():
        if not isinstance(settings, dict):
            raise ConfigurationError(f"{section}はJSONオブジェクトにしてください")
        payload[section] = settings
    launchd = payload.get("launchd", {})
    if set(launchd) - {"install"} or not isinstance(
        launchd.get("install", False), bool
    ):
        raise ConfigurationError("launchd.installはtrueまたはfalseにしてください")
    if launchd.get("install") and not payload.get("scheduler", {}).get(
        "enabled", False
    ):
        raise ConfigurationError(
            "launchdを導入する場合はscheduler.enabledをtrueにしてください"
        )
    app = _known(DEFAULT_APP_CONFIG, payload.get("app", {}))
    # Validate without touching the real workspace.
    try:
        ZoneInfo(app["timezone"])
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ConfigurationError("app.timezoneが不正です") from exc
    if app["week_start"] != "tuesday":
        raise ConfigurationError("v1.3の週開始日はtuesdayです")
    if (
        not isinstance(app["main_limit"], int)
        or isinstance(app["main_limit"], bool)
        or not 1 <= app["main_limit"] <= 3
    ):
        raise ConfigurationError("app.main_limitは1〜3にしてください")
    payload["app"] = app
    # Reuse the real loaders so setup and ordinary runtime validation cannot
    # drift apart. The temporary directory is discarded without touching the
    # target workspace.
    with tempfile.TemporaryDirectory(prefix="daily-review-setup-validate-") as name:
        directory = Path(name) / "config"
        directory.mkdir()
        for section, settings in payload.items():
            if section == "launchd":
                continue
            filename = "recovery.json" if section == "recovery" else f"{section}.json"
            (directory / filename).write_text(
                json.dumps(settings, ensure_ascii=False),
                encoding="utf-8",
            )
        load_effective_config(Path(name))
    return payload


def setup_plan(root: Path, value: Any) -> dict[str, Any]:
    payload = normalize_setup_payload(value)
    files = []
    for section, settings in sorted(payload.items()):
        if section == "launchd":
            continue
        name = "recovery.json" if section == "recovery" else f"{section}.json"
        path = config_directory(root) / name
        files.append(
            {
                "section": section,
                "path": str(path),
                "action": "conflict" if path.exists() else "create",
                "settings": settings,
            }
        )
    return {
        "root": str(root),
        "files": files,
        "conflicts": [item["path"] for item in files if item["action"] == "conflict"],
        "scheduler_enabled": bool(payload.get("scheduler", {}).get("enabled", False)),
        "launchd_install": bool(payload.get("launchd", {}).get("install", False)),
        "launchd_changed": False,
    }
