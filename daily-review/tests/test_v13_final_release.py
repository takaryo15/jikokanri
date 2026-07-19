from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from daily_review.archive import create_backup, verify_backup
from daily_review.cli import app
from daily_review.command_models import CommandRequest
from daily_review.operational_flows import run_operational_flow
from daily_review.recovery import apply_restore, preview_restore


runner = CliRunner()
ZONE = ZoneInfo("Asia/Tokyo")


def _init(root: Path) -> None:
    result = runner.invoke(app, ["init", "--root", str(root)])
    assert result.exit_code == 0, result.output


def _daily(root: Path, day: str, **extra: object) -> None:
    path = root / "data" / "daily" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "date": day,
        "created_at": f"{day}T21:00:00+09:00",
        "updated_at": f"{day}T21:00:00+09:00",
        **extra,
    }
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_setup_preview_apply_config_and_no_overwrite(tmp_path):
    setup_file = tmp_path / "setup.json"
    setup_file.write_text(
        json.dumps(
            {
                "app": {
                    "timezone": "Asia/Tokyo",
                    "week_start": "tuesday",
                    "main_limit": 3,
                },
                "scheduler": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    preview = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(setup_file),
            "--dry-run",
            "--root",
            str(tmp_path),
        ],
    )
    assert preview.exit_code == 0
    assert not (tmp_path / "config/app.json").exists()
    applied = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(setup_file),
            "--yes",
            "--root",
            str(tmp_path),
        ],
    )
    assert applied.exit_code == 0, applied.output
    before = (tmp_path / "config/app.json").read_bytes()
    duplicate = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(setup_file),
            "--yes",
            "--root",
            str(tmp_path),
        ],
    )
    assert duplicate.exit_code == 3
    assert (tmp_path / "config/app.json").read_bytes() == before
    validated = runner.invoke(app, ["config", "validate", "--root", str(tmp_path)])
    shown = runner.invoke(app, ["config", "show", "--json", "--root", str(tmp_path)])
    assert validated.exit_code == shown.exit_code == 0
    assert json.loads(shown.output)["app"]["main_limit"] == 3


def test_setup_launchd_is_previewed_but_not_installed_in_dry_run(tmp_path):
    setup_file = tmp_path / "setup-launchd.json"
    setup_file.write_text(
        json.dumps(
            {
                "scheduler": {"enabled": True},
                "launchd": {"install": True},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(setup_file),
            "--dry-run",
            "--root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "launchd: 導入予定" in result.output
    assert not (tmp_path / "config/scheduler.json").exists()


def test_setup_rejects_invalid_nested_configuration_before_write(tmp_path):
    setup_file = tmp_path / "invalid-setup.json"
    setup_file.write_text(
        json.dumps(
            {
                "scheduler": {
                    "enabled": True,
                    "jobs": {"review_reminder": {"time": "99:99"}},
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "setup",
            "--config",
            str(setup_file),
            "--yes",
            "--root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "time" in result.output
    assert not (tmp_path / "config/scheduler.json").exists()


def test_scheduler_rejects_unimplemented_run_all_policy(tmp_path):
    _init(tmp_path)
    (tmp_path / "config/scheduler.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "jobs": {
                    "review_reminder": {
                        "missed_run_policy": "run_all",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "validate", "--root", str(tmp_path)])

    assert result.exit_code == 3
    assert "missed_run_policy" in result.output


def test_backup_group_root_is_honored_by_create_subcommand(tmp_path):
    source = tmp_path / "source"
    outside = tmp_path / "outside"
    _init(source)
    _init(outside)
    _daily(source, "2026-07-14", raw_log="source only")
    output = tmp_path / "source.zip"

    result = runner.invoke(
        app,
        [
            "backup",
            "--root",
            str(source),
            "create",
            "--output",
            str(output),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    manifest = verify_backup(output)["manifest"]
    assert "data/daily/2026-07-14.json" in {item["path"] for item in manifest["files"]}


def test_migration_check_dry_run_apply_backup_and_idempotency(tmp_path):
    _init(tmp_path)
    check = runner.invoke(app, ["migrate", "check", "--json", "--root", str(tmp_path)])
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    dry_run = runner.invoke(
        app, ["migrate", "apply", "--dry-run", "--root", str(tmp_path)]
    )
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert check.exit_code == dry_run.exit_code == 0
    assert json.loads(check.output)["target_schema_version"] == "1.3"
    assert before == after
    applied = runner.invoke(
        app, ["migrate", "apply", "--yes", "--json", "--root", str(tmp_path)]
    )
    assert applied.exit_code == 0, applied.output
    result = json.loads(applied.output)
    assert result["backup_verified"] is True
    assert verify_backup(Path(result["backup_path"]))["valid"] is True
    duplicate = runner.invoke(
        app, ["migrate", "apply", "--yes", "--root", str(tmp_path)]
    )
    assert duplicate.exit_code == 0
    history = json.loads(
        (tmp_path / "data/migrations.json").read_text(encoding="utf-8")
    )
    assert sum(item["id"] == "v1.3-final" for item in history["migrations"]) == 1
    missing = tmp_path / "data/api/audit"
    missing.rmdir()
    repaired = runner.invoke(
        app, ["migrate", "apply", "--yes", "--json", "--root", str(tmp_path)]
    )
    assert repaired.exit_code == 0, repaired.output
    assert missing.is_dir()


def test_weekly_monthly_reports_require_fresh_explicit_approval(tmp_path):
    _init(tmp_path)
    _daily(tmp_path, "2026-07-14", raw_log="記録")
    weekly = runner.invoke(
        app, ["weekly", "--date", "2026-07-20", "--root", str(tmp_path)]
    )
    assert weekly.exit_code == 0, weekly.output
    path = tmp_path / "data/weekly/2026-07-14_2026-07-20.json"
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "draft"
    approved = runner.invoke(
        app,
        [
            "weekly",
            "--date",
            "2026-07-20",
            "--approve",
            "--yes",
            "--root",
            str(tmp_path),
        ],
    )
    assert approved.exit_code == 0, approved.output
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "approved"
    _daily(tmp_path, "2026-07-15", raw_log="後から追加")
    stale = runner.invoke(
        app,
        [
            "weekly",
            "--date",
            "2026-07-20",
            "--approve",
            "--yes",
            "--root",
            str(tmp_path),
        ],
    )
    assert stale.exit_code == 3
    assert "stale" in stale.output
    monthly = runner.invoke(
        app,
        [
            "monthly",
            "--date",
            "2026-07-31",
            "--dry-run",
            "--format",
            "json",
            "--root",
            str(tmp_path),
        ],
    )
    assert monthly.exit_code == 0
    assert json.loads(monthly.output)["status"] == "dry_run"


def test_home_json_and_unapproved_nightly_flow(tmp_path):
    _init(tmp_path)
    _daily(tmp_path, "2026-07-19", raw_log="原文を保持")
    nightly = run_operational_flow(
        tmp_path,
        "nightly",
        day="2026-07-19",
        current=datetime(2026, 7, 19, 22, 0, tzinfo=ZONE),
        dry_run=True,
    )
    assert nightly["details"]["instruction_status"] != "approved"
    home = runner.invoke(
        app,
        [
            "home",
            "--date",
            "2026-07-19",
            "--format",
            "json",
            "--root",
            str(tmp_path),
        ],
    )
    assert home.exit_code == 0, home.output
    value = json.loads(home.output)
    assert value["date"] == "2026-07-19"
    assert len(value["main"]) <= 3
    assert "scheduler" in value and "last_backup" in value


def test_restore_rollback_returns_original_hashes(tmp_path):
    _init(tmp_path)
    _daily(tmp_path, "2026-07-19", raw_log="original")
    backup, _ = create_backup(tmp_path)
    original = (tmp_path / "data/daily/2026-07-19.json").read_bytes()
    _daily(tmp_path, "2026-07-19", raw_log="changed")
    preview = preview_restore(tmp_path, backup, mode="replace")
    result = apply_restore(
        tmp_path,
        backup,
        mode="replace",
        confirmation_token=preview["confirmation_token"],
        idempotency_key="final-rollback-test",
    )
    assert result["status"] == "applied"
    assert (tmp_path / "data/daily/2026-07-19.json").read_bytes() == original
    replay = apply_restore(
        tmp_path,
        backup,
        mode="replace",
        confirmation_token=preview["confirmation_token"],
        idempotency_key="final-rollback-test",
    )
    assert replay["status"] == "idempotent_replay"


@pytest.mark.parametrize(
    "unsafe",
    [
        "\x00",
        "\x01",
        "\ud800",
    ],
)
def test_command_api_rejects_unsafe_unicode_and_controls(unsafe):
    with pytest.raises(ValidationError):
        CommandRequest.model_validate(
            {
                "effective_date": "2026-07-19",
                "raw_input": unsafe,
                "commands": [],
            }
        )


def test_command_api_rejects_excessive_json_depth():
    nested: object = "value"
    for _ in range(25):
        nested = {"value": nested}
    with pytest.raises(ValidationError):
        CommandRequest.model_validate(
            {
                "effective_date": "2026-07-19",
                "commands": [],
                "extra": nested,
            }
        )
