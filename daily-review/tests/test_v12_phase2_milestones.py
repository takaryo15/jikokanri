from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _goal(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    result = runner.invoke(app, ["goal", "add", "--title", "院試", "--level", "medium", "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.output.split("ID: ", 1)[1].splitlines()[0]


def _milestone(root, goal_id, title, *extra):
    result = runner.invoke(app, ["goal", "milestone", "add", goal_id, "--title", title, *extra, "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.output.split("ID: ", 1)[1].splitlines()[0]


def test_milestone_add_list_show_json_edit_status_and_reorder(tmp_path):
    goal_id = _goal(tmp_path)
    first = _milestone(tmp_path, goal_id, "一周目", "--qualitative", "説明できる", "--metric-name", "年度", "--metric-target", "5")
    second = _milestone(tmp_path, goal_id, "二周目")
    listed = runner.invoke(app, ["goal", "milestone", "list", goal_id, "--root", str(tmp_path)])
    shown = runner.invoke(app, ["goal", "milestone", "show", goal_id, first, "--json", "--root", str(tmp_path)])
    edited = runner.invoke(app, ["goal", "milestone", "edit", goal_id, first, "--title", "一周目完了", "--due-date", "2026-07-31", "--root", str(tmp_path)])
    completed = runner.invoke(app, ["goal", "milestone", "status", goal_id, first, "completed", "--root", str(tmp_path)])
    reordered = runner.invoke(app, ["goal", "milestone", "reorder", goal_id, second, "--before", first, "--root", str(tmp_path)])
    assert all(result.exit_code == 0 for result in (listed, shown, edited, completed, reordered))
    assert "一周目" in listed.output
    value = json.loads(shown.output)
    assert value["qualitative_criteria"] and value["quantitative_metrics"]
    stored = json.loads((tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_text(encoding="utf-8"))
    assert [item["order"] for item in stored["milestones"]] == [1, 2]
    assert next(item for item in stored["milestones"] if item["id"] == first)["completed_at"]
    assert list((tmp_path / "data" / "backups" / "goals").glob(f"{goal_id}_*.json"))


def test_completed_milestone_with_pending_steps_warns_but_can_be_confirmed(tmp_path):
    goal_id = _goal(tmp_path)
    milestone_id = _milestone(tmp_path, goal_id, "一周目")
    added = runner.invoke(app, ["goal", "milestone", "step", "add", goal_id, milestone_id, "--title", "問題を解く", "--root", str(tmp_path)])
    confirmed = runner.invoke(app, ["goal", "milestone", "status", goal_id, milestone_id, "completed", "--yes", "--root", str(tmp_path)])
    assert added.exit_code == confirmed.exit_code == 0
    assert "WARNING: 未完了ステップが1件あります" in confirmed.output
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert doctor.exit_code == 0
    assert "completedマイルストーンに未完了ステップ" in doctor.output
