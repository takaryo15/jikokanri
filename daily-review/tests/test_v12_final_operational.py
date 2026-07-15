from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def test_goal_design_is_reviewable_atomic_and_single_use(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    created = runner.invoke(app, ["goal", "design", "--text", "大学院入試に合格したい", "--root", str(tmp_path)])
    assert created.exit_code == 0, created.output
    design_id = created.output.split("design_id: ", 1)[1].splitlines()[0]
    assert runner.invoke(app, ["goal", "design", "answer", design_id, "--answer", "8月末まで", "--root", str(tmp_path)]).exit_code == 0
    proposal = {
        "goal": {"title": "院試合格", "level": "medium", "category": "院試", "due_date": "2026-08-31"},
        "milestones": [{"title": "過去問一周", "due_date": "2026-07-31", "steps": [{"title": "2025年度", "minimum": "問題文を読む"}]}],
    }
    received = runner.invoke(app, ["goal", "design", "receive", design_id, "--json-text", json.dumps(proposal, ensure_ascii=False), "--root", str(tmp_path)])
    assert received.exit_code == 0
    review = runner.invoke(app, ["goal", "design", "review", design_id, "--json", "--root", str(tmp_path)])
    assert json.loads(review.output)["status"] == "proposed"
    applied = runner.invoke(app, ["goal", "design", "apply", design_id, "--yes", "--root", str(tmp_path)])
    duplicate = runner.invoke(app, ["goal", "design", "apply", design_id, "--yes", "--root", str(tmp_path)])
    assert applied.exit_code == 0 and duplicate.exit_code == 3
    assert len(list((tmp_path / "data/goals/items").glob("goal-*.json"))) == 1


def test_v12_check_text_json_and_corruption_detection(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["migrate", "--yes", "--root", str(tmp_path)]).exit_code == 0
    text = runner.invoke(app, ["v12-check", "--root", str(tmp_path)])
    machine = runner.invoke(app, ["v12-check", "--json", "--root", str(tmp_path)])
    assert text.exit_code == machine.exit_code == 0
    assert "daily-review v12-check: OK" in text.output
    assert json.loads(machine.output)["errors"] == []
    transaction = tmp_path / "data/transactions/transaction-broken.json"
    transaction.write_text(json.dumps({"status": "prepared"}), encoding="utf-8")
    broken = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert broken.exit_code == 1 and "transaction不整合" in broken.output


def test_formal_version_and_release_check():
    version = runner.invoke(app, ["--version"])
    release = runner.invoke(app, ["release-check"])
    assert version.output.strip() == "daily-review 1.2.0"
    assert release.exit_code == 0, release.output
    assert "v1.2.0 is ready" in release.output


def test_home_resumes_pending_goal_design_before_starting_another_flow(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    created = runner.invoke(app, ["goal", "design", "--text", "研究目標を整理したい", "--root", str(tmp_path)])
    design_id = created.output.split("design_id: ", 1)[1].splitlines()[0]
    home = runner.invoke(app, ["home", "--date", "2026-07-15", "--root", str(tmp_path)])
    assert home.exit_code == 0
    assert f"次の操作: daily-review goal design prompt {design_id}" in home.output
