from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.storage import load_daily


runner = CliRunner()


def _proposal_file(tmp_path, **overrides):
    payload = {
        "target_date": "2026-07-14",
        "main": ["院試", "研究", "健康"],
        "tasks": [
            {
                "area": "院試",
                "task": "過去問 大問1つ",
                "priority": 1,
                "minimum_line": "問題文だけ読む",
            }
        ],
        "one_change_tomorrow": "朝イチで過去問を開く",
    }
    payload.update(overrides)
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_save_review_saves_structured_review_and_diary(tmp_path):
    review = {
        "diary": "任意の日記",
        "structured_review": {
            "today_main": [
                {"area": "院試", "status": "一部進んだ", "note": "過去問を少し見た"}
            ],
            "minimum_line": {"院試": "達成"},
            "what_went_well": ["学校に行けた"],
            "breakdown_causes": ["眠気"],
            "one_change_tomorrow": "朝イチで過去問を開く",
        },
    }
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "save-review",
            "--date",
            "2026-07-13",
            "--file",
            str(path),
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["diary"] == "任意の日記"
    assert entry["structured_review"]["today_main"][0]["area"] == "院試"


def test_proposal_and_final_are_separate_and_proposal_only_does_not_create_final(
    tmp_path,
):
    result = runner.invoke(
        app,
        [
            "save-proposal",
            "--date",
            "2026-07-13",
            "--file",
            str(_proposal_file(tmp_path)),
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["tomorrow_plan_proposal"]["status"] == "pending_review"
    assert "tomorrow_plan_final" not in entry


def test_approve_plan_copies_proposal_to_final_with_approved_at(tmp_path):
    runner.invoke(
        app,
        [
            "save-proposal",
            "--date",
            "2026-07-13",
            "--file",
            str(_proposal_file(tmp_path)),
            "--root",
            str(tmp_path),
        ],
    )
    result = runner.invoke(
        app, ["approve-plan", "--date", "2026-07-13", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    entry = load_daily(tmp_path, "2026-07-13")
    assert entry["tomorrow_plan_proposal"]["status"] == "pending_review"
    assert entry["tomorrow_plan_final"]["status"] == "approved"
    assert entry["tomorrow_plan_final"]["approved_at"]


def test_approve_plan_without_proposal_fails(tmp_path):
    result = runner.invoke(
        app, ["approve-plan", "--date", "2026-07-13", "--root", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "提案版がない" in result.output
