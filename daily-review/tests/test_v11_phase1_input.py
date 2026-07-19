from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

import daily_review.cli as cli
from daily_review.cli import app


runner = CliRunner()


def _inbox(root, day="2026-07-14"):
    return json.loads(
        (root / "data" / "inbox" / f"{day}.json").read_text(encoding="utf-8")
    )


def test_input_saves_text_multiline_japanese_and_preserves_source(tmp_path):
    raw = "今日は院試を2問進めた。\n研究はO VIIとO VIIIを確認した。"
    result = runner.invoke(
        app, ["input", "--date", "2026-07-14", "--text", raw, "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    saved = _inbox(tmp_path)
    assert saved["date"] == "2026-07-14"
    assert saved["entries"][0]["raw_text"] == raw
    assert saved["entries"][0]["source"] == "text"
    assert saved["entries"][0]["id"].startswith("20260714-")
    assert "保存先: data/inbox/2026-07-14.json" in result.output


def test_input_appends_without_destroying_existing_entries_or_fields(tmp_path):
    first = runner.invoke(
        app,
        ["input", "--date", "2026-07-14", "--text", "最初", "--root", str(tmp_path)],
    )
    second = runner.invoke(
        app, ["input", "--date", "2026-07-14", "--text", "次", "--root", str(tmp_path)]
    )
    assert first.exit_code == second.exit_code == 0
    saved = _inbox(tmp_path)
    assert [item["raw_text"] for item in saved["entries"]] == ["最初", "次"]
    assert saved["entries"][0]["id"] != saved["entries"][1]["id"]


def test_input_dry_run_and_empty_input_do_not_write(tmp_path):
    dry_run = runner.invoke(
        app,
        [
            "input",
            "--date",
            "2026-07-14",
            "--text",
            "確認",
            "--dry-run",
            "--root",
            str(tmp_path),
        ],
    )
    empty = runner.invoke(
        app, ["input", "--date", "2026-07-14", "--text", "   ", "--root", str(tmp_path)]
    )
    assert dry_run.exit_code == 0
    assert "保存は行いませんでした" in dry_run.output
    assert empty.exit_code == 2
    assert "ERROR: 入力内容が空です" in empty.output
    assert not (tmp_path / "data" / "inbox").exists()


def test_input_rejects_multiple_sources_and_reads_stdin(tmp_path):
    multiple = runner.invoke(
        app, ["input", "--text", "a", "--clipboard", "--root", str(tmp_path)]
    )
    stdin = runner.invoke(
        app,
        ["input", "--date", "2026-07-14", "--root", str(tmp_path)],
        input="標準入力の原文\n",
    )
    assert multiple.exit_code == 2
    assert "同時に使用できません" in multiple.output
    assert stdin.exit_code == 0
    assert _inbox(tmp_path)["entries"][0]["raw_text"] == "標準入力の原文\n"
    assert _inbox(tmp_path)["entries"][0]["source"] == "stdin"


def test_input_reads_clipboard_without_modifying_raw_text(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="クリップボード\n原文"),
    )
    result = runner.invoke(
        app, ["input", "--date", "2026-07-14", "--clipboard", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert _inbox(tmp_path)["entries"][0]["source"] == "clipboard"
    assert _inbox(tmp_path)["entries"][0]["raw_text"] == "クリップボード\n原文"


def test_input_reports_corrupt_existing_inbox_without_overwriting(tmp_path):
    path = tmp_path / "data" / "inbox" / "2026-07-14.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    result = runner.invoke(
        app,
        ["input", "--date", "2026-07-14", "--text", "新規", "--root", str(tmp_path)],
    )
    assert result.exit_code == 3
    assert "ERROR:" in result.output
    assert path.read_text(encoding="utf-8") == "{"


def test_doctor_checks_inbox_after_init(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "OK   data/inbox" in result.output
