from __future__ import annotations


from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.goals import goal_progress, load_goal, milestone_progress


runner = CliRunner()


def _setup(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    goal = runner.invoke(
        app,
        ["goal", "add", "--title", "院試", "--level", "medium", "--root", str(root)],
    )
    goal_id = goal.output.split("ID: ", 1)[1].splitlines()[0]
    mile = runner.invoke(
        app,
        ["goal", "milestone", "add", goal_id, "--title", "一周目", "--root", str(root)],
    )
    return goal_id, mile.output.split("ID: ", 1)[1].splitlines()[0]


def test_steps_minimum_status_reorder_and_progress(tmp_path):
    goal_id, milestone_id = _setup(tmp_path)
    first = runner.invoke(
        app,
        [
            "goal",
            "milestone",
            "step",
            "add",
            goal_id,
            milestone_id,
            "--title",
            "2025年度",
            "--minimum",
            "方針だけ書く",
            "--root",
            str(tmp_path),
        ],
    )
    second = runner.invoke(
        app,
        [
            "goal",
            "milestone",
            "step",
            "add",
            goal_id,
            milestone_id,
            "--title",
            "2024年度",
            "--root",
            str(tmp_path),
        ],
    )
    first_id = first.output.split("ID: ", 1)[1].splitlines()[0]
    second_id = second.output.split("ID: ", 1)[1].splitlines()[0]
    assert (
        runner.invoke(
            app,
            [
                "goal",
                "milestone",
                "step",
                "status",
                goal_id,
                milestone_id,
                first_id,
                "done",
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "goal",
                "milestone",
                "step",
                "status",
                goal_id,
                milestone_id,
                second_id,
                "doing",
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "goal",
                "milestone",
                "step",
                "reorder",
                goal_id,
                milestone_id,
                second_id,
                "--before",
                first_id,
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    listed = runner.invoke(
        app,
        [
            "goal",
            "milestone",
            "step",
            "list",
            goal_id,
            milestone_id,
            "--root",
            str(tmp_path),
        ],
    )
    assert listed.exit_code == 0 and "[doing] 2024年度" in listed.output
    value = load_goal(tmp_path, goal_id)
    milestone = value["milestones"][0]
    assert [step["order"] for step in milestone["steps"]] == [1, 2]
    assert milestone_progress(milestone) == (75.0, "steps")
    assert goal_progress(value) == (75.0, "milestones")
    assert (
        next(step for step in milestone["steps"] if step["id"] == first_id)["minimum"]
        == "方針だけ書く"
    )
