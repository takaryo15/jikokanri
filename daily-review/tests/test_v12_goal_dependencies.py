from __future__ import annotations

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _add_goal(root, title):
    result = runner.invoke(app, ["goal", "add", "--title", title, "--level", "medium", "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.output.split("ID: ", 1)[1].splitlines()[0]


def _add_mile(root, goal_id, title):
    result = runner.invoke(app, ["goal", "milestone", "add", goal_id, "--title", title, "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.output.split("ID: ", 1)[1].splitlines()[0]


def test_milestone_dependency_rejects_self_missing_and_cycle(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    goal_id = _add_goal(tmp_path, "院試")
    first, second = _add_mile(tmp_path, goal_id, "一周目"), _add_mile(tmp_path, goal_id, "二周目")
    self_ref = runner.invoke(app, ["goal", "milestone", "edit", goal_id, first, "--depends-on", first, "--root", str(tmp_path)])
    missing = runner.invoke(app, ["goal", "milestone", "edit", goal_id, first, "--depends-on", "mile-deadbeef", "--root", str(tmp_path)])
    assert runner.invoke(app, ["goal", "milestone", "edit", goal_id, second, "--depends-on", first, "--root", str(tmp_path)]).exit_code == 0
    cycle = runner.invoke(app, ["goal", "milestone", "edit", goal_id, first, "--depends-on", second, "--root", str(tmp_path)])
    assert self_ref.exit_code == missing.exit_code == cycle.exit_code == 3
    assert "自分自身" in self_ref.output and "存在しません" in missing.output and "循環" in cycle.output


def test_step_dependency_rejects_cross_milestone_and_completed_reverse_dependency(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    goal_id = _add_goal(tmp_path, "院試")
    first, second = _add_mile(tmp_path, goal_id, "一周目"), _add_mile(tmp_path, goal_id, "二周目")
    one = runner.invoke(app, ["goal", "milestone", "step", "add", goal_id, first, "--title", "A", "--root", str(tmp_path)])
    two = runner.invoke(app, ["goal", "milestone", "step", "add", goal_id, first, "--title", "B", "--root", str(tmp_path)])
    other = runner.invoke(app, ["goal", "milestone", "step", "add", goal_id, second, "--title", "C", "--root", str(tmp_path)])
    one_id = one.output.split("ID: ", 1)[1].splitlines()[0]
    two_id = two.output.split("ID: ", 1)[1].splitlines()[0]
    other_id = other.output.split("ID: ", 1)[1].splitlines()[0]
    cross = runner.invoke(app, ["goal", "milestone", "step", "edit", goal_id, first, two_id, "--depends-on", other_id, "--root", str(tmp_path)])
    assert cross.exit_code == 3 and "存在しません" in cross.output
    assert runner.invoke(app, ["goal", "milestone", "step", "edit", goal_id, first, two_id, "--depends-on", one_id, "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["goal", "milestone", "step", "status", goal_id, first, two_id, "done", "--root", str(tmp_path)]).exit_code == 3
