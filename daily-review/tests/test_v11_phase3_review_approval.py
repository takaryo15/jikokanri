from __future__ import annotations

import json

from typer.testing import CliRunner

import daily_review.cli as cli
from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()
DAY = "2026-07-14"


def _prepare_draft(root) -> None:
    text = (
        "院試の過去問を解いた。研究は少し進めた。筋トレはできなかった。"
        "集中できなかった原因はスマホ。今日は先生と話せてよかった。"
        "明日は院試を進める。明日は研究を進める。"
    )
    saved = runner.invoke(app, ["input", "--date", DAY, "--text", text, "--root", str(root)])
    organized = runner.invoke(app, ["organize", "--date", DAY, "--root", str(root)])
    assert saved.exit_code == organized.exit_code == 0


def _draft(root):
    return json.loads((root / "data" / "drafts" / f"{DAY}.json").read_text(encoding="utf-8"))


def test_review_renders_draft_json_and_missing_draft_error(tmp_path):
    missing = runner.invoke(app, ["review", "--date", DAY, "--root", str(tmp_path)])
    assert missing.exit_code == 2
    assert "整理ドラフトがありません" in missing.output

    _prepare_draft(tmp_path)
    review = runner.invoke(app, ["review", "--date", DAY, "--dry-run", "--root", str(tmp_path)])
    as_json = runner.invoke(app, ["review", "--date", DAY, "--json", "--root", str(tmp_path)])
    assert review.exit_code == as_json.exit_code == 0
    assert "今日のMain候補:" in review.output
    assert "状態: 未承認" in review.output
    assert "dry-runのため" in review.output
    assert json.loads(as_json.output)["status"] == "draft"


def test_approve_yes_saves_compatible_daily_content_and_marks_draft(tmp_path):
    _prepare_draft(tmp_path)
    result = runner.invoke(app, ["approve", "--date", DAY, "--yes", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    daily = load_daily(tmp_path, DAY)
    assert daily["structured_review"]["today_main"]
    assert daily["draft_approval"]["today_main"]
    statuses = {item["status"] for item in daily["draft_approval"]["task_results"]}
    assert {"completed", "partial", "not_started"} <= statuses
    assert daily["draft_approval"]["reflection"]["causes"] == ["スマホ"]
    assert daily["tomorrow_plan_proposal"]["status"] == "pending_review"
    assert daily["tomorrow_plan_final"] if "tomorrow_plan_final" in daily else None is None
    draft = _draft(tmp_path)
    assert draft["status"] == "approved"
    assert draft["approved_at"]
    assert draft["approved_daily_path"] == f"data/daily/{DAY}.json"


def test_approve_cancel_and_noninteractive_mode_do_not_save(tmp_path, monkeypatch):
    _prepare_draft(tmp_path)
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    cancelled = runner.invoke(app, ["approve", "--date", DAY, "--root", str(tmp_path)], input="n\n")
    assert cancelled.exit_code == 0
    assert "承認をキャンセルしました" in cancelled.output
    assert load_daily(tmp_path, DAY) is None

    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: True)
    rejected = runner.invoke(app, ["approve", "--date", DAY, "--root", str(tmp_path)])
    assert rejected.exit_code == 2
    assert "--yes" in rejected.output
    assert load_daily(tmp_path, DAY) is None


def test_reapproval_requires_force_and_creates_daily_backup(tmp_path):
    _prepare_draft(tmp_path)
    assert runner.invoke(app, ["approve", "--date", DAY, "--yes", "--root", str(tmp_path)]).exit_code == 0
    repeated = runner.invoke(app, ["approve", "--date", DAY, "--yes", "--root", str(tmp_path)])
    assert repeated.exit_code == 0
    assert "すでに承認済み" in repeated.output
    forced = runner.invoke(app, ["approve", "--date", DAY, "--force", "--yes", "--root", str(tmp_path)])
    assert forced.exit_code == 0, forced.output
    backups = list((tmp_path / "data" / "backups" / "daily").glob(f"{DAY}_*.json"))
    assert len(backups) == 1
    assert "再承認前バックアップ" in forced.output


def test_edit_draft_set_replaces_lists_deduplicates_and_tracks_history(tmp_path):
    _prepare_draft(tmp_path)
    result = runner.invoke(
        app,
        [
            "edit-draft", "--date", DAY,
            "--set", "tomorrow.main_candidates=院試を2問解く",
            "--set", "tomorrow.main_candidates=研究を進める",
            "--set", "today.completed=同じ内容",
            "--set", "today.completed=同じ内容",
            "--root", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    draft = _draft(tmp_path)
    assert draft["tomorrow"]["main_candidates"] == ["院試を2問解く", "研究を進める"]
    assert draft["today"]["completed"] == ["同じ内容"]
    assert draft["revision"] == 2
    assert draft["edit_history"][-1]["changed_fields"] == ["tomorrow.main_candidates", "today.completed"]

    clear = runner.invoke(app, ["edit-draft", "--date", DAY, "--set", "reflection.causes=", "--root", str(tmp_path)])
    assert clear.exit_code == 0
    assert _draft(tmp_path)["reflection"]["causes"] == []


def test_edit_draft_rejects_invalid_edits_and_force_edit_resets_approval(tmp_path):
    _prepare_draft(tmp_path)
    unknown = runner.invoke(app, ["edit-draft", "--date", DAY, "--set", "status=approved", "--root", str(tmp_path)])
    too_many = runner.invoke(
        app,
        ["edit-draft", "--date", DAY, "--set", "today.main_candidates=a", "--set", "today.main_candidates=b", "--set", "today.main_candidates=c", "--set", "today.main_candidates=d", "--root", str(tmp_path)],
    )
    assert unknown.exit_code == too_many.exit_code == 2
    assert runner.invoke(app, ["approve", "--date", DAY, "--yes", "--root", str(tmp_path)]).exit_code == 0
    blocked = runner.invoke(app, ["edit-draft", "--date", DAY, "--set", "journal=手動追記", "--root", str(tmp_path)])
    forced = runner.invoke(app, ["edit-draft", "--date", DAY, "--force", "--set", "journal=手動追記", "--root", str(tmp_path)])
    assert blocked.exit_code == 2
    assert forced.exit_code == 0
    draft = _draft(tmp_path)
    assert draft["status"] == "draft"
    assert draft["approved_at"] is None
    assert draft["approved_daily_path"] is None
    assert (tmp_path / "data" / "daily" / f"{DAY}.json").exists()


def test_home_summary_and_doctor_reflect_approval_and_detect_bad_draft(tmp_path):
    _prepare_draft(tmp_path)
    before = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    assert "整理ドラフト: 未承認" in before.output
    assert f"daily-review reflect --date {DAY} --resume" in before.output
    assert runner.invoke(app, ["approve", "--date", DAY, "--yes", "--root", str(tmp_path)]).exit_code == 0
    home = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    summary = runner.invoke(app, ["summary", "--date", DAY, "--root", str(tmp_path)])
    assert "整理ドラフト: 承認済み" in home.output
    assert "確定日次: 作成済み" in home.output
    assert "未完了タスク:\nなし" in home.output
    assert "今日のMain:" in summary.output and "タスク結果:" in summary.output
    assert "明日の提案版: 記録済み" in summary.output

    path = tmp_path / "data" / "drafts" / f"{DAY}.json"
    draft = _draft(tmp_path)
    draft["approved_at"] = None
    draft["today"]["main_candidates"] = ["a", "b", "c", "d"]
    path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert doctor.exit_code == 1
    assert "approvedなのにapproved_atがありません" in doctor.output
    assert "最大3件" in doctor.output
