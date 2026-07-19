from __future__ import annotations

import json

from typer.testing import CliRunner

import daily_review.goals as goals
from daily_review.cli import app


runner = CliRunner()


def _goal(**overrides):
    value = goals.new_goal(title="目標", level="medium")
    value.update(overrides)
    return value


def test_progress_calculations_and_manual_fallback_are_clamped():
    increase = _goal(
        quantitative_metrics=[
            {
                "id": "metric-a",
                "name": "回数",
                "unit": "回",
                "baseline": 0,
                "target": 3,
                "current": 5,
                "direction": "increase",
            }
        ]
    )
    decrease = _goal(
        quantitative_metrics=[
            {
                "id": "metric-a",
                "name": "体脂肪",
                "unit": "%",
                "baseline": 30,
                "target": 20,
                "current": 25,
                "direction": "decrease",
            }
        ]
    )
    boolean = _goal(
        quantitative_metrics=[
            {
                "id": "metric-a",
                "name": "提出",
                "unit": "",
                "baseline": False,
                "target": True,
                "current": True,
                "direction": "boolean",
            }
        ]
    )
    qualitative = _goal(
        qualitative_criteria=[
            {"id": "qual-a", "description": "説明できる", "status": "partially_met"}
        ]
    )
    manual = _goal(manual_progress=40)
    assert goals.goal_progress(increase) == (100.0, "auto")
    assert goals.goal_progress(decrease) == (50.0, "auto")
    assert goals.goal_progress(boolean) == (100.0, "auto")
    assert goals.goal_progress(qualitative) == (50.0, "auto")
    assert goals.goal_progress(manual) == (40.0, "manual")
    assert goals.goal_progress(_goal()) == (None, "unset")


def test_invalid_dates_and_backup_failure_do_not_modify_existing_goal(
    tmp_path, monkeypatch
):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    invalid = runner.invoke(
        app,
        [
            "goal",
            "add",
            "--title",
            "bad",
            "--level",
            "short",
            "--start-date",
            "2026-07-15",
            "--due-date",
            "2026-07-14",
            "--root",
            str(tmp_path),
        ],
    )
    assert invalid.exit_code == 3
    created = runner.invoke(
        app,
        [
            "goal",
            "add",
            "--title",
            "before",
            "--level",
            "short",
            "--root",
            str(tmp_path),
        ],
    )
    goal_id = created.output.split("ID: ", 1)[1].splitlines()[0]
    path = tmp_path / "data" / "goals" / "items" / f"{goal_id}.json"
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        goals.shutil,
        "copy2",
        lambda *_args: (_ for _ in ()).throw(OSError("backup failed")),
    )
    failed = runner.invoke(
        app, ["goal", "edit", goal_id, "--title", "after", "--root", str(tmp_path)]
    )
    assert failed.exit_code == 3
    assert path.read_text(encoding="utf-8") == before


def test_migrate_creates_goal_directories_without_touching_existing_data(tmp_path):
    legacy = tmp_path / "data" / "daily" / "2026-07-14.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"date":"2026-07-14"}\n', encoding="utf-8")
    before = legacy.read_bytes()
    result = runner.invoke(app, ["migrate", "--yes", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert legacy.read_bytes() == before
    for relative in ("data/goals", "data/goals/items", "data/backups/goals"):
        assert (tmp_path / relative).is_dir()
    history = json.loads(
        (tmp_path / "data" / "migrations.json").read_text(encoding="utf-8")
    )
    assert any(item["id"] == "v1.2-goals-base" for item in history["migrations"])
