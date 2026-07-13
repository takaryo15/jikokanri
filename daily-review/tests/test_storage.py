from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()


def test_init_creates_required_directories(tmp_path):
    result = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "data" / "daily").is_dir()
    assert (tmp_path / "data" / "weekly").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "templates" / "night_review_prompt.md").is_file()


def test_init_is_idempotent_and_does_not_overwrite_existing_data(tmp_path):
    runner.invoke(app, ["init", "--root", str(tmp_path)])
    marker = tmp_path / "templates" / "night_review_prompt.md"
    marker.write_text("custom", encoding="utf-8")
    result = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert marker.read_text(encoding="utf-8") == "custom"


def test_save_raw_creates_daily_json_and_markdown(tmp_path):
    raw_file = tmp_path / "raw.txt"
    raw_file.write_text("今日の生ログ\nそのまま保存", encoding="utf-8")
    result = runner.invoke(
        app,
        ["save-raw", "--date", "2026-07-13", "--file", str(raw_file), "--root", str(tmp_path)],
    )
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["raw_log"] == "今日の生ログ\nそのまま保存"
    assert (tmp_path / "logs" / "2026-07-13.md").is_file()


def test_save_raw_update_keeps_other_fields(tmp_path):
    raw_file = tmp_path / "raw.txt"
    raw_file.write_text("old", encoding="utf-8")
    runner.invoke(app, ["save-raw", "--date", "2026-07-13", "--file", str(raw_file), "--root", str(tmp_path)])
    entry_path = tmp_path / "data" / "daily" / "2026-07-13.json"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    entry["diary"] = "消えない日記"
    entry_path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    raw_file.write_text("new", encoding="utf-8")
    result = runner.invoke(app, ["save-raw", "--date", "2026-07-13", "--file", str(raw_file), "--root", str(tmp_path)])
    assert result.exit_code == 0
    updated = load_daily(tmp_path, "2026-07-13")
    assert updated["raw_log"] == "new"
    assert updated["diary"] == "消えない日記"


def test_atomic_json_write_leaves_no_temp_file(tmp_path):
    raw_file = tmp_path / "raw.txt"
    raw_file.write_text("raw", encoding="utf-8")
    result = runner.invoke(app, ["save-raw", "--date", "2026-07-13", "--file", str(raw_file), "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert not list((tmp_path / "data" / "daily").glob("*.tmp"))


def test_list_shows_saved_daily_statuses(tmp_path):
    raw_file = tmp_path / "raw.txt"
    raw_file.write_text("raw", encoding="utf-8")
    runner.invoke(app, ["save-raw", "--date", "2026-07-13", "--file", str(raw_file), "--root", str(tmp_path)])
    result = runner.invoke(app, ["list", "--root", str(tmp_path), "--limit", "7"])
    assert result.exit_code == 0
    assert "2026-07-13" in result.output
    assert "保存済み" in result.output
