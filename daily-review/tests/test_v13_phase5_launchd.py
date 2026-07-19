from __future__ import annotations

import json
from pathlib import Path

from daily_review.scheduler_install import (
    cron_example,
    expected_plist,
    install_scheduler,
    plist_path,
    uninstall_scheduler,
)


def _config(root: Path) -> None:
    path = root / "config/scheduler.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"enabled": True, "poll_interval_minutes": 15}),
        encoding="utf-8",
    )


def test_plist_uses_arguments_and_supports_japanese_space_path(tmp_path):
    root = tmp_path / "自己 管理" / "daily-review"
    root.mkdir(parents=True)
    _config(root)
    value = expected_plist(root, executable="/opt/tools/daily-review")
    assert value["ProgramArguments"][0] == "/opt/tools/daily-review"
    assert str(root) in value["ProgramArguments"]
    assert value["StartInterval"] == 900
    assert value["WorkingDirectory"] == str(root)
    assert "PATH" in value["EnvironmentVariables"]


def test_launchd_install_and_uninstall_dry_run_write_nothing(tmp_path):
    root = tmp_path / "daily-review"
    root.mkdir()
    _config(root)
    home = tmp_path / "home"
    result = install_scheduler(
        root,
        dry_run=True,
        home=home,
        executable="/opt/tools/daily-review",
    )
    assert result["status"] == "dry_run"
    assert not plist_path(home).exists()
    removed = uninstall_scheduler(dry_run=True, home=home)
    assert removed["data_removed"] is False
    assert root.exists()


def test_cron_backend_only_returns_example(tmp_path):
    _config(tmp_path)
    result = install_scheduler(
        tmp_path,
        backend="cron",
        executable="/path with space/daily-review",
    )
    assert result["status"] == "preview"
    assert "'/path with space/daily-review'" in result["cron"]
    assert cron_example(
        tmp_path, executable="/path with space/daily-review"
    ).startswith("*/15")
