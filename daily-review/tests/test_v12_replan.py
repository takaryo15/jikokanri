from __future__ import annotations

import json

from typer.testing import CliRunner

import daily_review.replan as replan_module
from daily_review.cli import app


runner = CliRunner()


def _setup(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    goal = runner.invoke(app, ["goal", "add", "--title", "研究", "--level", "medium", "--due-date", "2026-07-31", "--root", str(root)])
    goal_id = goal.output.split("ID: ", 1)[1].splitlines()[0]
    assert runner.invoke(app, ["goal", "evaluate", "week", "--date", "2026-07-20", "--save", "--root", str(root)]).exit_code == 0
    assert runner.invoke(app, ["goal", "evaluate", "apply", "--week", "2026-07-14", "--yes", "--root", str(root)]).exit_code == 0
    return goal_id


def test_replan_requires_selected_proposal_and_applies_transactionally(tmp_path):
    goal_id = _setup(tmp_path)
    created = runner.invoke(app, ["goal", "replan", "--week", "2026-07-14", "--save", "--json", "--root", str(tmp_path)])
    assert created.exit_code == 0, created.output
    value = json.loads(created.output); replan_id = value["id"]; proposal_id = value["proposals"][0]["id"]
    before = (tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_bytes()
    rejected = runner.invoke(app, ["goal", "replan", "apply", replan_id, "--yes", "--root", str(tmp_path)])
    assert rejected.exit_code == 3 and (tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_bytes() == before
    selected = runner.invoke(app, ["goal", "replan", "edit", replan_id, "--approve", proposal_id, "--root", str(tmp_path)])
    applied = runner.invoke(app, ["goal", "replan", "apply", replan_id, "--yes", "--root", str(tmp_path)])
    assert selected.exit_code == applied.exit_code == 0, applied.output
    goal = json.loads((tmp_path / "data" / "goals" / "items" / f"{goal_id}.json").read_text(encoding="utf-8"))
    assert goal["status"] == "paused"
    assert goal["history"][-1]["source"] == "replan" and goal["history"][-1]["replan_id"] == replan_id
    assert json.loads((tmp_path / "data" / "replans" / f"{replan_id}.json").read_text(encoding="utf-8"))["status"] == "applied"
    assert list((tmp_path / "data" / "backups" / "goals").glob(f"{goal_id}_*.json"))


def test_replan_rejects_unknown_edit_field_and_can_cancel(tmp_path):
    _setup(tmp_path)
    created = runner.invoke(app, ["goal", "replan", "--week", "2026-07-14", "--save", "--json", "--root", str(tmp_path)])
    value = json.loads(created.output); replan_id = value["id"]; proposal_id = value["proposals"][0]["id"]
    rejected = runner.invoke(app, ["goal", "replan", "edit", replan_id, "--set", f"{proposal_id}.after.unknown=x", "--root", str(tmp_path)])
    cancelled = runner.invoke(app, ["goal", "replan", "cancel", replan_id, "--yes", "--root", str(tmp_path)])
    assert rejected.exit_code == 3 and cancelled.exit_code == 0


def test_replan_backup_failure_leaves_goal_and_replan_unchanged(tmp_path, monkeypatch):
    goal_id = _setup(tmp_path)
    created = runner.invoke(app, ["goal", "replan", "--week", "2026-07-14", "--save", "--json", "--root", str(tmp_path)])
    value = json.loads(created.output); replan_id = value["id"]; proposal_id = value["proposals"][0]["id"]
    assert runner.invoke(app, ["goal", "replan", "edit", replan_id, "--approve", proposal_id, "--root", str(tmp_path)]).exit_code == 0
    goal_path = tmp_path / "data" / "goals" / "items" / f"{goal_id}.json"
    replan_path = tmp_path / "data" / "replans" / f"{replan_id}.json"
    before_goal, before_replan = goal_path.read_bytes(), replan_path.read_bytes()
    monkeypatch.setattr(replan_module.shutil, "copy2", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("backup failed")))
    failed = runner.invoke(app, ["goal", "replan", "apply", replan_id, "--yes", "--root", str(tmp_path)])
    assert failed.exit_code == 3
    assert goal_path.read_bytes() == before_goal and replan_path.read_bytes() == before_replan


def test_reduce_daily_load_updates_separate_planning_config(tmp_path):
    _setup(tmp_path)
    evaluation_path = tmp_path / "data" / "evaluations" / "weekly" / "2026-07-14_2026-07-20.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["diagnostics"] = [{"code": "overloaded", "severity": "warning", "message": "予定過多です", "goal_id": None, "evidence": [2]}]
    evaluation_path.write_text(json.dumps(evaluation, ensure_ascii=False), encoding="utf-8")
    created = runner.invoke(app, ["goal", "replan", "--week", "2026-07-14", "--save", "--json", "--root", str(tmp_path)])
    value = json.loads(created.output); replan_id = value["id"]; proposal_id = value["proposals"][0]["id"]
    assert runner.invoke(app, ["goal", "replan", "edit", replan_id, "--approve", proposal_id, "--root", str(tmp_path)]).exit_code == 0
    applied = runner.invoke(app, ["goal", "replan", "apply", replan_id, "--yes", "--root", str(tmp_path)])
    assert applied.exit_code == 0, applied.output
    assert json.loads((tmp_path / "config" / "planning.json").read_text(encoding="utf-8"))["max_daily_main"] == 2
