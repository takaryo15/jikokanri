from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _setup(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    goal = runner.invoke(app, ["goal", "add", "--title", "院試", "--level", "medium", "--category", "院試", "--root", str(root)])
    goal_id = goal.output.split("ID: ", 1)[1].splitlines()[0]
    mile = runner.invoke(app, ["goal", "milestone", "add", goal_id, "--title", "過去問一周目", "--due-date", "2026-07-16", "--root", str(root)])
    milestone_id = mile.output.split("ID: ", 1)[1].splitlines()[0]
    step = runner.invoke(app, ["goal", "milestone", "step", "add", goal_id, milestone_id, "--title", "2025年度", "--minimum", "問題文を読む", "--root", str(root)])
    return goal_id, milestone_id, step.output.split("ID: ", 1)[1].splitlines()[0]


def test_weekly_and_daily_goal_plans_are_drafts_then_explicitly_approved(tmp_path):
    goal_id, milestone_id, step_id = _setup(tmp_path)
    preview = runner.invoke(app, ["plan", "week", "--date", "2026-07-14", "--json", "--root", str(tmp_path)])
    saved = runner.invoke(app, ["plan", "week", "--date", "2026-07-14", "--save", "--root", str(tmp_path)])
    applied = runner.invoke(app, ["plan", "apply", "--week", "2026-07-14", "--yes", "--root", str(tmp_path)])
    daily = runner.invoke(app, ["plan", "today", "--date", "2026-07-14", "--save", "--root", str(tmp_path)])
    daily_applied = runner.invoke(app, ["plan", "apply", "--date", "2026-07-14", "--yes", "--root", str(tmp_path)])
    assert all(result.exit_code == 0 for result in (preview, saved, applied, daily, daily_applied))
    weekly = json.loads(preview.output)
    assert weekly["week_start"] == "2026-07-14" and weekly["focus_items"][0]["step_id"] == step_id
    stored = json.loads((tmp_path / "data" / "plans" / "daily" / "2026-07-14.json").read_text(encoding="utf-8"))
    assert stored["status"] == "approved" and len(stored["main_candidates"]) <= 3
    assert goal_id and milestone_id


def test_review_link_progress_and_doctor(tmp_path):
    goal_id, milestone_id, step_id = _setup(tmp_path)
    assert runner.invoke(app, ["plan", "week", "--date", "2026-07-14", "--save", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["plan", "apply", "--week", "2026-07-14", "--yes", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["plan", "today", "--date", "2026-07-14", "--save", "--root", str(tmp_path)]).exit_code == 0
    link = runner.invoke(app, ["goal", "link", "--date", "2026-07-14", "--main-index", "1", "--goal", goal_id, "--milestone", milestone_id, "--step", step_id, "--root", str(tmp_path)])
    assert link.exit_code == 0, link.output
    daily_path = tmp_path / "data" / "daily" / "2026-07-14.json"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_path.write_text(json.dumps({"date": "2026-07-14", "created_at": "2026-07-14T00:00:00+09:00", "updated_at": "2026-07-14T00:00:00+09:00", "structured_review": {"today_main": [{"area": "院試", "status": "完了"}]}}, ensure_ascii=False), encoding="utf-8")
    progress = runner.invoke(app, ["goal", "progress", "--date", "2026-07-14", "--apply", "--yes", "--root", str(tmp_path)])
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert progress.exit_code == doctor.exit_code == 0
    assert "step status: done" in progress.output and "OK   goal links" in doctor.output
    assert runner.invoke(app, ["goal", "unlink", "--date", "2026-07-14", "--main-index", "1", "--root", str(tmp_path)]).exit_code == 0


def test_doctor_rejects_invalid_goal_plan_without_changing_daily_data(tmp_path):
    _setup(tmp_path)
    path = tmp_path / "data" / "plans" / "daily" / "2026-07-14.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": "2026-07-14", "status": "invalid", "main_candidates": []}, ensure_ascii=False), encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "日次目標計画を読み込めません" in result.output
