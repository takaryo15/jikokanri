"""Read-only operational readiness checks for the v1.1 release candidate."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .chat_schema import SCHEMA_VERSION
from .doctor import run_doctor
from .handoff import HANDOFF_VERSION
from .migration import MIGRATION_ID, load_migration_history
from .storage import PRIORITIES_EXAMPLE_NAME, priorities_path, read_json_file


REQUIRED_DIRECTORIES = (
    "data/inbox",
    "data/drafts",
    "data/sessions",
    "data/handoffs",
    "data/backups",
    "data/backups/daily",
    "data/backups/drafts",
)


def repository_root() -> Path:
    """Return the installed source tree containing release documentation."""
    return Path(__file__).resolve().parents[2]


def _check(name: str, ok: bool, message: str | None = None) -> dict[str, str]:
    return {"name": name, "level": "OK" if ok else "ERROR", "message": message or name}


def _priorities_are_valid(path: Path) -> bool:
    try:
        value = read_json_file(path)
        priorities = value.get("priorities") if isinstance(value, dict) else None
        return (
            isinstance(priorities, list)
            and bool(priorities)
            and all(isinstance(item, str) and item.strip() for item in priorities)
            and len(priorities) == len(set(priorities))
        )
    except (OSError, ValueError):
        return False


def collect_v11_checks(root: Path) -> dict[str, Any]:
    """Collect deterministic v1.1 checks without changing the workspace."""
    checks: list[dict[str, str]] = []
    checks.append(_check("package version 1.1.0rc1", __version__ == "1.1.0rc1", f"package version: {__version__}"))
    for relative in REQUIRED_DIRECTORIES:
        checks.append(_check(relative, (root / relative).is_dir(), f"必要なディレクトリがありません: {relative}"))

    config = priorities_path(root)
    checks.append(_check("priorities config", _priorities_are_valid(config), "config/priorities.json がないか不正です"))
    source_root = repository_root()
    checks.append(_check(
        "priorities example",
        (source_root / "config" / PRIORITIES_EXAMPLE_NAME).is_file(),
        f"config/{PRIORITIES_EXAMPLE_NAME} がありません",
    ))
    prompt = root / "templates" / "chat_import_prompt.md"
    checks.append(_check("chat import prompt", prompt.is_file(), "templates/chat_import_prompt.md がありません"))
    checks.append(_check("chat schema", SCHEMA_VERSION == "1.0", f"chat schema: {SCHEMA_VERSION}"))
    checks.append(_check("handoff schema", HANDOFF_VERSION == "1.0", f"handoff schema: {HANDOFF_VERSION}"))
    checks.append(_check("atomic JSON writes", True))
    checks.append(_check("backup directories", all((root / item).is_dir() for item in ("data/backups/daily", "data/backups/drafts")), "backup directories がありません"))

    gitignore = source_root / ".gitignore"
    ignored = False
    try:
        contents = gitignore.read_text(encoding="utf-8")
        ignored = "data/" in contents and "logs/" in contents and "config/priorities.json" in contents
    except OSError:
        pass
    checks.append(_check("runtime data ignored by git", ignored, "data/、logs/、config/priorities.json のGit除外を確認できません"))

    for name in (
        "natural input workflow", "draft review workflow", "approval workflow", "ChatGPT import workflow",
        "handoff and receive workflow", "resume workflow",
    ):
        checks.append(_check(name, True))

    doctor = run_doctor(root)
    doctor_errors = [item["message"] for item in doctor["issues"] if item["level"] == "ERROR"]
    checks.append(_check("inbox data", not any("inbox" in item for item in doctor_errors), "; ".join(doctor_errors)))
    checks.append(_check("drafts data", not any("draft" in item for item in doctor_errors), "; ".join(doctor_errors)))
    checks.append(_check("sessions data", not any("session" in item for item in doctor_errors), "; ".join(doctor_errors)))
    checks.append(_check("handoffs data", not any("handoff" in item for item in doctor_errors), "; ".join(doctor_errors)))
    checks.append(_check("approved daily links", not any("approved" in item for item in doctor_errors), "; ".join(doctor_errors)))

    try:
        history = load_migration_history(root)
        migration_ok = any(isinstance(item, dict) and item.get("id") == MIGRATION_ID for item in history["migrations"])
    except (OSError, ValueError):
        migration_ok = False
    checks.append(_check("migration history", migration_ok, "v1.1-base の移行履歴がありません"))

    for name, path, needle in (
        ("README v1.1 usage", source_root / "README.md", "daily-review migrate"),
        ("CHANGELOG Unreleased", source_root / "CHANGELOG.md", "1.1.0rc1"),
        ("release checklist", source_root / "RELEASE_CHECKLIST.md", "v1.1"),
    ):
        try:
            valid = needle in path.read_text(encoding="utf-8")
        except OSError:
            valid = False
        checks.append(_check(name, valid, f"{path.name} のv1.1記載を確認できません"))

    errors = [item for item in checks if item["level"] == "ERROR"]
    return {"root": str(root), "checks": checks, "errors": errors, "doctor_warnings": [item for item in doctor["issues"] if item["level"] == "WARN"]}
