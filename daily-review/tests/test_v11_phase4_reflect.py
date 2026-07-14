from __future__ import annotations

import json

from typer.testing import CliRunner

import daily_review.cli as cli
from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()
DAY = "2026-07-14"
SAFE_TEXT = "院試の問題を解いた。明日は研究を進める。"


def _reflect_args(root, *extra):
    return ["reflect", "--date", DAY, *extra, "--root", str(root)]


def _draft(root):
    return json.loads((root / "data" / "drafts" / f"{DAY}.json").read_text(encoding="utf-8"))


def test_reflect_text_creates_inbox_and_draft_and_can_stop_unapproved(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    result = runner.invoke(app, _reflect_args(tmp_path, "--text", SAFE_TEXT), input="n\n")
    assert result.exit_code == 0, result.output
    assert "入力を保存しました" in result.output
    assert "内容を整理しました" in result.output
    assert "確定せず終了しました" in result.output
    assert (tmp_path / "data" / "inbox" / f"{DAY}.json").exists()
    assert _draft(tmp_path)["status"] == "draft"
    assert load_daily(tmp_path, DAY) is None


def test_reflect_yes_approves_and_stdin_input_preserves_japanese(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    result = runner.invoke(app, _reflect_args(tmp_path, "--text", SAFE_TEXT, "--yes"))
    assert result.exit_code == 0, result.output
    assert load_daily(tmp_path, DAY)["tomorrow_plan_proposal"]["status"] == "pending_review"
    assert _draft(tmp_path)["status"] == "approved"

    stdin_root = tmp_path / "stdin"
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: True)
    stdin = runner.invoke(app, _reflect_args(stdin_root), input="研究はO VII/O VIIIを確認した。\n明日は院試を進める。\n")
    assert stdin.exit_code == 0, stdin.output
    inbox = json.loads((stdin_root / "data" / "inbox" / f"{DAY}.json").read_text(encoding="utf-8"))
    assert inbox["entries"][0]["raw_text"] == "研究はO VII/O VIIIを確認した。\n明日は院試を進める。\n"


def test_reflect_y_and_edit_then_return_to_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    approved = runner.invoke(app, _reflect_args(tmp_path, "--text", SAFE_TEXT), input="y\n")
    assert approved.exit_code == 0, approved.output
    assert "振り返りを確定しました" in approved.output

    edit_root = tmp_path / "edit"
    calls = []

    def fake_edit_draft(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(cli, "edit_draft", fake_edit_draft)
    edited = runner.invoke(app, _reflect_args(edit_root, "--text", SAFE_TEXT), input="e\nn\n")
    assert edited.exit_code == 0, edited.output
    assert calls
    assert "編集後の内容を表示します" in edited.output
    assert "確定せず終了しました" in edited.output


def test_reflect_resume_missing_and_approved_states(tmp_path, monkeypatch):
    missing = runner.invoke(app, _reflect_args(tmp_path, "--resume"))
    assert missing.exit_code == 2
    assert "再開できるドラフトがありません" in missing.output

    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    assert runner.invoke(app, _reflect_args(tmp_path, "--text", SAFE_TEXT, "--yes")).exit_code == 0
    completed = runner.invoke(app, _reflect_args(tmp_path, "--resume"))
    assert completed.exit_code == 0
    assert "すでに確定済み" in completed.output


def test_reflect_dry_run_and_json_do_not_mix_output_or_write(tmp_path):
    dry = runner.invoke(app, _reflect_args(tmp_path, "--text", SAFE_TEXT, "--dry-run"))
    as_json = runner.invoke(app, _reflect_args(tmp_path, "--text", SAFE_TEXT, "--dry-run", "--json"))
    assert dry.exit_code == as_json.exit_code == 0
    assert "保存は行いませんでした" in dry.output
    assert json.loads(as_json.output)["dry_run"] is True
    assert not (tmp_path / "data").exists()

    saved_json = runner.invoke(app, _reflect_args(tmp_path / "saved", "--text", SAFE_TEXT, "--json"))
    payload = json.loads(saved_json.output)
    assert saved_json.exit_code == 0
    assert payload["input_saved"] is True
    assert payload["approved"] is False
    assert (tmp_path / "saved" / "data" / "drafts" / f"{DAY}.json").exists()


def test_reflect_rejects_empty_multiple_sources_and_auto_approval_risks(tmp_path, monkeypatch):
    empty = runner.invoke(app, _reflect_args(tmp_path, "--text", "   "))
    multiple = runner.invoke(app, _reflect_args(tmp_path, "--text", "a", "--clipboard"))
    assert empty.exit_code == multiple.exit_code == 2

    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    unclassified = runner.invoke(app, _reflect_args(tmp_path / "unclassified", "--text", "なんとなく過ごした。", "--yes"))
    no_today = runner.invoke(app, _reflect_args(tmp_path / "no-today", "--text", "明日は研究を進める。", "--yes"))
    no_tomorrow = runner.invoke(app, _reflect_args(tmp_path / "no-tomorrow", "--text", "院試の問題を解いた。", "--yes"))
    assert unclassified.exit_code == no_today.exit_code == no_tomorrow.exit_code == 2
    assert "自動承認できません" in unclassified.output
    assert not (tmp_path / "unclassified" / "data" / "daily").exists()


def test_reflect_keeps_input_when_organization_fails_and_never_overwrites_daily(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)

    def broken_organize(*args, **kwargs):
        raise ValueError("分類失敗")

    monkeypatch.setattr(cli, "organize_day", broken_organize)
    failed = runner.invoke(app, _reflect_args(tmp_path, "--text", SAFE_TEXT))
    assert failed.exit_code == 3
    assert (tmp_path / "data" / "inbox" / f"{DAY}.json").exists()
    assert not (tmp_path / "data" / "drafts").exists()

    daily_root = tmp_path / "daily"
    path = daily_root / "data" / "daily" / f"{DAY}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"date": DAY}), encoding="utf-8")
    blocked = runner.invoke(app, _reflect_args(daily_root, "--text", SAFE_TEXT, "--yes"))
    assert blocked.exit_code == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"date": DAY}


def test_reflect_duplicate_and_home_next_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    assert runner.invoke(app, ["input", "--date", DAY, "--text", SAFE_TEXT, "--root", str(tmp_path)]).exit_code == 0
    duplicate = runner.invoke(app, _reflect_args(tmp_path, "--text", SAFE_TEXT, "--yes"))
    assert duplicate.exit_code == 2
    assert "同じ内容が直前に保存されています" in duplicate.output

    empty_home = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path / "empty")])
    assert f"daily-review reflect --date {DAY}" in empty_home.output
    inbox_home = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    assert f"daily-review organize --date {DAY}" in inbox_home.output
    assert runner.invoke(app, ["organize", "--date", DAY, "--root", str(tmp_path)]).exit_code == 0
    draft_home = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    assert f"daily-review reflect --date {DAY} --resume" in draft_home.output
