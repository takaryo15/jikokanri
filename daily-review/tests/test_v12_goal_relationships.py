from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _init(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0


def _add(root, title, level, *extra):
    result = runner.invoke(app, ["goal", "add", "--title", title, "--level", level, *extra, "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.output.split("ID: ", 1)[1].splitlines()[0]


def test_parent_child_and_cycle_relationship_protection(tmp_path):
    _init(tmp_path)
    vision = _add(tmp_path, "研究人生の方向性", "vision")
    medium = _add(tmp_path, "解析を完了する", "medium", "--parent", vision)
    shown = runner.invoke(app, ["goal", "show", vision, "--root", str(tmp_path)])
    self_parent = runner.invoke(app, ["goal", "edit", vision, "--parent", vision, "--root", str(tmp_path)])
    cycle = runner.invoke(app, ["goal", "edit", vision, "--parent", medium, "--root", str(tmp_path)])
    missing = runner.invoke(app, ["goal", "add", "--title", "missing", "--level", "short", "--parent", "goal-deadbeef", "--root", str(tmp_path)])
    assert "- 解析を完了する" in shown.output
    assert self_parent.exit_code == cycle.exit_code == missing.exit_code == 3
    assert "自分自身" in self_parent.output and "循環" in cycle.output and "存在しません" in missing.output


def test_invalid_level_relation_archived_parent_and_path_traversal_are_rejected(tmp_path):
    _init(tmp_path)
    short = _add(tmp_path, "今日の作業", "short")
    invalid_relation = runner.invoke(app, ["goal", "add", "--title", "上位の目標", "--level", "vision", "--parent", short, "--root", str(tmp_path)])
    archived = _add(tmp_path, "保管する目標", "medium")
    assert runner.invoke(app, ["goal", "archive", archived, "--yes", "--root", str(tmp_path)]).exit_code == 0
    bad_parent = runner.invoke(app, ["goal", "add", "--title", "子", "--level", "short", "--parent", archived, "--root", str(tmp_path)])
    traversal = runner.invoke(app, ["goal", "show", "../outside", "--root", str(tmp_path)])
    assert invalid_relation.exit_code == bad_parent.exit_code == traversal.exit_code == 3
    assert "level関係" in invalid_relation.output and "archived" in bad_parent.output and "ID" in traversal.output


def test_doctor_reports_invalid_goal_relationship(tmp_path):
    _init(tmp_path)
    path = tmp_path / "data" / "goals" / "items" / "goal-deadbeef.json"
    path.write_text(json.dumps({"id": "goal-deadbeef", "title": "bad", "level": "invalid", "status": "active"}), encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "goal goal-deadbeef" in result.output
