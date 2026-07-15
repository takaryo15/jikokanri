from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _id(output: str) -> str:
    return output.split("ID: ", 1)[1].splitlines()[0]


def test_goal_plan_result_evaluation_replan_rc1_flow(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    goal = runner.invoke(app, ["goal", "add", "--title", "院試合格", "--level", "medium", "--category", "院試", "--due-date", "2026-08-31", "--root", str(tmp_path)])
    goal_id = _id(goal.output)
    milestone = runner.invoke(app, ["goal", "milestone", "add", goal_id, "--title", "過去問一周", "--due-date", "2026-07-31", "--root", str(tmp_path)])
    milestone_id = _id(milestone.output)
    step = runner.invoke(app, ["goal", "milestone", "step", "add", goal_id, milestone_id, "--title", "2025年度を解く", "--minimum", "問題文を読む", "--root", str(tmp_path)])
    step_id = _id(step.output)

    commands = [
        ["plan", "week", "--date", "2026-07-14", "--save"],
        ["plan", "apply", "--week", "2026-07-14", "--yes"],
        ["plan", "today", "--date", "2026-07-14", "--save"],
        ["plan", "apply", "--date", "2026-07-14", "--yes"],
        ["goal", "link", "--date", "2026-07-14", "--main-index", "1", "--goal", goal_id, "--milestone", milestone_id, "--step", step_id],
    ]
    for command in commands:
        result = runner.invoke(app, [*command, "--root", str(tmp_path)])
        assert result.exit_code == 0, result.output

    daily = tmp_path / "data" / "daily" / "2026-07-14.json"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(json.dumps({"date": "2026-07-14", "created_at": "2026-07-14T21:00:00+09:00", "updated_at": "2026-07-14T21:00:00+09:00", "structured_review": {"today_main": [{"area": "院試", "status": "完了"}], "minimum_line": {"院試": "達成"}, "what_went_well": ["過去問を進めた"], "breakdown_causes": []}}, ensure_ascii=False), encoding="utf-8")
    progress = runner.invoke(app, ["goal", "progress", "--date", "2026-07-14", "--apply", "--yes", "--root", str(tmp_path)])
    assert progress.exit_code == 0, progress.output
    goal_payload = json.loads((tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_text(encoding="utf-8"))
    assert goal_payload["milestones"][0]["steps"][0]["status"] == "done"

    assert runner.invoke(app, ["goal", "evaluate", "week", "--date", "2026-07-20", "--save", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["goal", "evaluate", "apply", "--week", "2026-07-14", "--yes", "--root", str(tmp_path)]).exit_code == 0
    replan = runner.invoke(app, ["goal", "replan", "--week", "2026-07-14", "--save", "--json", "--root", str(tmp_path)])
    assert replan.exit_code == 0, replan.output
    replan_payload = json.loads(replan.output); replan_id = replan_payload["id"]; proposal_id = replan_payload["proposals"][0]["id"]
    assert runner.invoke(app, ["goal", "replan", "edit", replan_id, "--approve", proposal_id, "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["goal", "replan", "apply", replan_id, "--yes", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["doctor", "--root", str(tmp_path)]).exit_code == 0
