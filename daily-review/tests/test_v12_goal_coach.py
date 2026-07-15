from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _evaluation(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    assert runner.invoke(app, ["goal", "evaluate", "week", "--date", "2026-07-20", "--save", "--root", str(root)]).exit_code == 0


def _payload(**overrides):
    value = {"schema_version": "1.0", "workflow": "goal_coach", "period_type": "week", "period_id": "2026-07-14_2026-07-20", "analysis": {"strengths": ["記録を続けた"], "problems": [], "root_causes": [], "patterns": [], "recommendations": ["Mainを減らす"], "evidence": ["予定過多2日"]}}
    value.update(overrides); return value


def test_coach_prompt_and_receive_store_only_auxiliary_analysis(tmp_path):
    _evaluation(tmp_path)
    prompt = runner.invoke(app, ["goal", "coach", "--week", "2026-07-14", "--root", str(tmp_path)])
    received = runner.invoke(app, ["goal", "coach-receive", "--week", "2026-07-14", "--json-text", json.dumps(_payload(), ensure_ascii=False), "--root", str(tmp_path)])
    assert prompt.exit_code == received.exit_code == 0
    assert "医療・心理診断をしない" in prompt.output and "自動適用は行っていません" in received.output
    evaluation = json.loads((tmp_path / "data" / "evaluations" / "weekly" / "2026-07-14_2026-07-20.json").read_text(encoding="utf-8"))
    assert evaluation["coach_analysis"]["recommendations"] == ["Mainを減らす"]


def test_coach_rejects_wrong_period_and_unknown_fields(tmp_path):
    _evaluation(tmp_path)
    wrong = _payload(period_id="2026-07-21_2026-07-27")
    unknown = _payload(extra="bad")
    first = runner.invoke(app, ["goal", "coach-receive", "--week", "2026-07-14", "--json-text", json.dumps(wrong), "--root", str(tmp_path)])
    second = runner.invoke(app, ["goal", "coach-receive", "--week", "2026-07-14", "--json-text", json.dumps(unknown), "--root", str(tmp_path)])
    assert first.exit_code == second.exit_code == 2
