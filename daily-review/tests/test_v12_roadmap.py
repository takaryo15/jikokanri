from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _ids(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    goal = runner.invoke(app, ["goal", "add", "--title", "院試", "--level", "medium", "--root", str(root)])
    goal_id = goal.output.split("ID: ", 1)[1].splitlines()[0]
    first = runner.invoke(app, ["goal", "milestone", "add", goal_id, "--title", "一周目", "--due-date", "2026-07-20", "--root", str(root)])
    second = runner.invoke(app, ["goal", "milestone", "add", goal_id, "--title", "二周目", "--due-date", "2026-07-21", "--root", str(root)])
    return goal_id, first.output.split("ID: ", 1)[1].splitlines()[0], second.output.split("ID: ", 1)[1].splitlines()[0]


def test_roadmap_json_compact_next_and_home_actions(tmp_path):
    goal_id, first, second = _ids(tmp_path)
    doing = runner.invoke(app, ["goal", "milestone", "step", "add", goal_id, first, "--title", "2025年度", "--due-date", "2026-07-16", "--root", str(tmp_path)])
    todo = runner.invoke(app, ["goal", "milestone", "step", "add", goal_id, first, "--title", "2024年度", "--due-date", "2026-07-15", "--root", str(tmp_path)])
    doing_id = doing.output.split("ID: ", 1)[1].splitlines()[0]
    assert runner.invoke(app, ["goal", "milestone", "step", "status", goal_id, first, doing_id, "doing", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["goal", "milestone", "edit", goal_id, second, "--depends-on", first, "--root", str(tmp_path)]).exit_code == 0
    roadmap = runner.invoke(app, ["goal", "roadmap", goal_id, "--root", str(tmp_path)])
    compact = runner.invoke(app, ["goal", "roadmap", goal_id, "--compact", "--root", str(tmp_path)])
    raw = runner.invoke(app, ["goal", "roadmap", goal_id, "--json", "--root", str(tmp_path)])
    next_item = runner.invoke(app, ["goal", "next", goal_id, "--root", str(tmp_path)])
    home = runner.invoke(app, ["home", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert all(result.exit_code == 0 for result in (roadmap, compact, raw, next_item, home))
    assert json.loads(raw.output)["milestones"][0]["id"] == first
    assert "2025年度" in roadmap.output and "2025年度" not in compact.output
    assert "2025年度" in next_item.output  # doing is preferred over an earlier due todo step
    assert "目標の次アクション:" in home.output
    assert todo.exit_code == 0


def test_legacy_goal_without_milestones_and_blocked_candidates_are_safe(tmp_path):
    goal_id, first, second = _ids(tmp_path)
    assert runner.invoke(app, ["goal", "milestone", "edit", goal_id, second, "--depends-on", first, "--root", str(tmp_path)]).exit_code == 0
    blocked = runner.invoke(app, ["goal", "next", goal_id, "--root", str(tmp_path)])
    assert "一周目" in blocked.output  # first has no steps and remains actionable
    path = tmp_path / "data" / "goals" / "items" / f"{goal_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("milestones")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    legacy = runner.invoke(app, ["goal", "roadmap", goal_id, "--root", str(tmp_path)])
    assert legacy.exit_code == 0 and "ロードマップ" in legacy.output
