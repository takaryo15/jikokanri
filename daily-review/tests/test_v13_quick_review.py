from __future__ import annotations

import json

from typer.testing import CliRunner

import daily_review.quick_review as quick_module
from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()
DAY = "2026-07-15"


def _args(root, *extra):
    return ["review", "quick", "--date", DAY, *extra, "--root", str(root)]


def test_noninteractive_quick_review_preserves_raw_and_builds_proposal(tmp_path):
    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "--done",
            "開発を進めた\n改行を保持",
            "--not-done",
            "院試",
            "--cause",
            "眠気",
            "--tomorrow",
            "A",
            "--tomorrow",
            "B",
            "--tomorrow",
            "C",
            "--tomorrow",
            "D",
            "--minimum",
            "1問",
            "--journal",
            "日本語の日記",
        ),
    )
    assert result.exit_code == 0, result.output
    entry = load_daily(tmp_path, DAY)
    assert entry["structured_review"]["what_went_well"] == ["開発を進めた\n改行を保持"]
    assert entry["tomorrow_plan_proposal"]["main"] == ["A", "B", "C"]
    assert entry["quick_review"]["backlog_candidates"] == ["D"]
    assert entry.get("tomorrow_plan_final") is None
    inbox = json.loads(
        (tmp_path / f"data/inbox/{DAY}.json").read_text(encoding="utf-8")
    )
    assert inbox["entries"][0]["source"] == "quick_review"
    assert "\n" in inbox["entries"][0]["quick_review_payload"]["done"][0]
    assert (tmp_path / f"logs/{DAY}.md").is_file()


def test_json_stdin_optional_fields_and_interactive_input(tmp_path):
    payload = {
        "date": DAY,
        "done": ["完了"],
        "tomorrow": ["明日"],
        "minimum": ["最低限"],
    }
    stdin_result = runner.invoke(
        app,
        _args(tmp_path / "stdin", "--stdin"),
        input=json.dumps(payload, ensure_ascii=False),
    )
    interactive = runner.invoke(
        app, _args(tmp_path / "interactive"), input="完了\n\n\n明日\n最低限\n\n"
    )
    assert stdin_result.exit_code == interactive.exit_code == 0
    assert load_daily(tmp_path / "stdin", DAY)["diary"] is None
    assert load_daily(tmp_path / "interactive", DAY)["tomorrow_plan_proposal"][
        "main"
    ] == ["明日"]


def test_dry_run_duplicate_force_and_backup(tmp_path):
    dry = runner.invoke(
        app,
        _args(
            tmp_path, "--done", "A", "--tomorrow", "B", "--minimum", "C", "--dry-run"
        ),
    )
    assert dry.exit_code == 0 and not (tmp_path / "data").exists()
    first = runner.invoke(
        app, _args(tmp_path, "--done", "A", "--tomorrow", "B", "--minimum", "C")
    )
    duplicate = runner.invoke(
        app, _args(tmp_path, "--done", "X", "--tomorrow", "Y", "--minimum", "Z")
    )
    forced = runner.invoke(
        app,
        _args(tmp_path, "--done", "X", "--tomorrow", "Y", "--minimum", "Z", "--force"),
    )
    assert first.exit_code == forced.exit_code == 0 and duplicate.exit_code == 4
    assert load_daily(tmp_path, DAY)["quick_review"]["revision"] == 2
    assert list((tmp_path / "data/backups/daily").glob(f"{DAY}_*.json"))


def test_invalid_date_empty_and_conflicting_stdin_do_not_write(tmp_path):
    invalid = runner.invoke(
        app,
        ["review", "quick", "--date", "bad", "--done", "x", "--root", str(tmp_path)],
    )
    empty = runner.invoke(app, _args(tmp_path))
    conflict = runner.invoke(app, _args(tmp_path, "--stdin", "--done", "x"), input="{}")
    assert invalid.exit_code != 0 and empty.exit_code != 0 and conflict.exit_code != 0
    assert not (tmp_path / "data").exists()


def test_daily_save_failure_keeps_raw_inbox(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("injected")

    monkeypatch.setattr(quick_module, "save_daily", fail)
    result = runner.invoke(
        app,
        _args(tmp_path, "--done", "原文", "--tomorrow", "明日", "--minimum", "最低限"),
    )
    assert result.exit_code == 4 and "inboxに保存済み" in result.output
    assert (tmp_path / f"data/inbox/{DAY}.json").is_file()
    assert not (tmp_path / f"data/daily/{DAY}.json").exists()


def test_interruption_before_completion_leaves_no_files(tmp_path, monkeypatch):
    import daily_review.cli as cli

    monkeypatch.setattr(
        cli.typer,
        "prompt",
        lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    result = runner.invoke(app, _args(tmp_path))
    assert result.exit_code != 0 and not (tmp_path / "data").exists()
