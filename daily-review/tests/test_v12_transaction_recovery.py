from __future__ import annotations

import json

from typer.testing import CliRunner

import daily_review.storage as storage
from daily_review.cli import app


runner = CliRunner()


def test_replan_rename_failure_rolls_back_every_target_and_is_retryable(tmp_path, monkeypatch):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    goal = runner.invoke(app, ["goal", "add", "--title", "研究", "--level", "medium", "--root", str(tmp_path)])
    goal_id = goal.output.split("ID: ", 1)[1].splitlines()[0]
    assert runner.invoke(app, ["goal", "evaluate", "week", "--date", "2026-07-20", "--save", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["goal", "evaluate", "apply", "--week", "2026-07-14", "--yes", "--root", str(tmp_path)]).exit_code == 0
    value = json.loads(runner.invoke(app, ["goal", "replan", "--week", "2026-07-14", "--save", "--json", "--root", str(tmp_path)]).output)
    replan_id, proposal_id = value["id"], value["proposals"][0]["id"]
    assert runner.invoke(app, ["goal", "replan", "edit", replan_id, "--approve", proposal_id, "--root", str(tmp_path)]).exit_code == 0
    goal_path = tmp_path / "data/goals/items" / f"{goal_id}.json"
    replan_path = tmp_path / "data/replans" / f"{replan_id}.json"
    before = (goal_path.read_bytes(), replan_path.read_bytes())
    original = storage.os.replace
    calls = {"count": 0}

    def fail_once(source, destination):
        calls["count"] += 1
        if calls["count"] == 3:
            raise OSError("injected rename failure")
        return original(source, destination)

    monkeypatch.setattr(storage.os, "replace", fail_once)
    failed = runner.invoke(app, ["goal", "replan", "apply", replan_id, "--yes", "--root", str(tmp_path)])
    assert failed.exit_code == 3
    assert (goal_path.read_bytes(), replan_path.read_bytes()) == before
    assert not list(tmp_path.rglob("*.tmp"))
    manifest = json.loads(next((tmp_path / "data/transactions").glob("*.json")).read_text(encoding="utf-8"))
    assert manifest["status"] == "rolled_back"
    monkeypatch.setattr(storage.os, "replace", original)
    retried = runner.invoke(app, ["goal", "replan", "apply", replan_id, "--yes", "--root", str(tmp_path)])
    assert retried.exit_code == 0, retried.output
