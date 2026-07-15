from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _goal(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    result = runner.invoke(app, ["goal", "add", "--title", "院試合格", "--level", "medium", "--category", "院試", "--due-date", "2026-08-31", "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.output.split("ID: ", 1)[1].splitlines()[0]


def test_weekly_evaluation_save_review_and_approval_are_separate_from_goals(tmp_path):
    goal_id = _goal(tmp_path)
    before = (tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_bytes()
    generated = runner.invoke(app, ["goal", "evaluate", "week", "--date", "2026-07-20", "--save", "--json", "--root", str(tmp_path)])
    reviewed = runner.invoke(app, ["goal", "evaluate", "review", "--week", "2026-07-14", "--json", "--root", str(tmp_path)])
    approved = runner.invoke(app, ["goal", "evaluate", "apply", "--week", "2026-07-14", "--yes", "--root", str(tmp_path)])
    assert generated.exit_code == reviewed.exit_code == approved.exit_code == 0
    value = json.loads(generated.output)
    assert value["week_start"] == "2026-07-14" and value["week_end"] == "2026-07-20"
    assert value["goal_evaluations"][0]["status"] == "inactive"
    stored = json.loads((tmp_path / "data" / "evaluations" / "weekly" / "2026-07-14_2026-07-20.json").read_text(encoding="utf-8"))
    assert stored["status"] == "approved" and stored["approved_at"]
    assert (tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_bytes() == before


def test_monthly_evaluation_json_and_trends(tmp_path):
    _goal(tmp_path)
    result = runner.invoke(app, ["goal", "evaluate", "month", "--month", "2026-07", "--save", "--json", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["month"] == "2026-07" and value["status"] == "draft"
    assert value["goal_evaluations"][0]["trend"] in {"improving", "stable", "declining", "stalled", "volatile"}


def test_doctor_rejects_broken_evaluation(tmp_path):
    _goal(tmp_path)
    path = tmp_path / "data" / "evaluations" / "weekly" / "bad.json"
    path.write_text('{"status":"approved"}', encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "goal評価を読み込めません" in result.output
