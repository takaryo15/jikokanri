from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.migration import MIGRATION_ID
from daily_review.storage import resolve_root


runner = CliRunner()


def test_daily_review_root_environment_is_used_after_explicit_root(monkeypatch, tmp_path):
    environment_root = tmp_path / "environment"
    explicit_root = tmp_path / "explicit"
    monkeypatch.setenv("DAILY_REVIEW_ROOT", str(environment_root))
    assert resolve_root() == environment_root
    assert resolve_root(explicit_root) == explicit_root


def test_migrate_creates_only_missing_v11_support_files_and_keeps_daily_data(tmp_path):
    daily = tmp_path / "data" / "daily" / "2026-07-14.json"
    daily.parent.mkdir(parents=True)
    original = '{"date":"2026-07-14","raw_log":"v1 data"}\n'
    daily.write_text(original, encoding="utf-8")

    dry = runner.invoke(app, ["migrate", "--root", str(tmp_path), "--dry-run", "--json"])
    assert dry.exit_code == 0
    assert json.loads(dry.output)["dry_run"] is True
    assert not (tmp_path / "data" / "inbox").exists()

    result = runner.invoke(app, ["migrate", "--root", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    for relative in ("data/inbox", "data/drafts", "data/sessions", "data/handoffs", "data/backups/daily", "data/backups/drafts"):
        assert (tmp_path / relative).is_dir()
    assert daily.read_text(encoding="utf-8") == original
    assert (tmp_path / "config" / "priorities.json").is_file()
    assert (tmp_path / "templates" / "chat_import_prompt.md").is_file()
    history = json.loads((tmp_path / "data" / "migrations.json").read_text(encoding="utf-8"))
    assert history["migrations"][0]["id"] == MIGRATION_ID

    repeated = runner.invoke(app, ["migrate", "--root", str(tmp_path), "--yes"])
    assert repeated.exit_code == 0
    history = json.loads((tmp_path / "data" / "migrations.json").read_text(encoding="utf-8"))
    assert len(history["migrations"]) == 1


def test_migrate_never_overwrites_existing_priority_settings(tmp_path):
    config = tmp_path / "config" / "priorities.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"priorities":["custom"]}\n', encoding="utf-8")
    result = runner.invoke(app, ["migrate", "--root", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert config.read_text(encoding="utf-8") == '{"priorities":["custom"]}\n'


def test_v11_check_reports_json_and_detects_invalid_priorities(tmp_path):
    assert runner.invoke(app, ["migrate", "--root", str(tmp_path), "--yes"]).exit_code == 0
    passed = runner.invoke(app, ["v11-check", "--root", str(tmp_path), "--json"])
    assert passed.exit_code == 0, passed.output
    assert not json.loads(passed.output)["errors"]

    (tmp_path / "config" / "priorities.json").write_text('{"priorities": []}', encoding="utf-8")
    failed = runner.invoke(app, ["v11-check", "--root", str(tmp_path)])
    assert failed.exit_code == 5
    assert "priorities config" in failed.output
