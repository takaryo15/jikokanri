from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.date_utils import week_range_for
from daily_review.weekly import build_weekly_summary


runner = CliRunner()


def _write_daily(tmp_path, day, review, final=False, proposal=False):
    daily_dir = tmp_path / "data" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day,
        "structured_review": review,
        "created_at": f"{day}T22:00:00+09:00",
        "updated_at": f"{day}T22:00:00+09:00",
    }
    plan = {
        "status": "approved",
        "target_date": "2026-07-14",
        "main": ["院試"],
        "tasks": [
            {"area": "院試", "task": "過去問", "priority": 1, "minimum_line": "読む"}
        ],
        "one_change_tomorrow": "朝イチ",
        "approved_at": f"{day}T22:30:00+09:00",
    }
    if final:
        payload["tomorrow_plan_final"] = plan
    if proposal:
        payload["tomorrow_plan_proposal"] = {
            **plan,
            "status": "pending_review",
            "approved_at": None,
        }
    (daily_dir / f"{day}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_tuesday_to_monday_week_range():
    assert week_range_for("2026-07-08") == ("2026-07-07", "2026-07-13")


def test_monday_uses_previous_tuesday_to_same_monday():
    assert week_range_for("2026-07-13") == ("2026-07-07", "2026-07-13")


def test_minimum_line_rate_and_breakdown_ranking(tmp_path):
    _write_daily(
        tmp_path,
        "2026-07-07",
        {
            "today_main": [{"area": "院試", "status": "一部進んだ"}],
            "minimum_line": {"院試": "達成", "研究": "未達"},
            "what_went_well": ["学校に行けた"],
            "breakdown_causes": ["眠気", "スマホ", "眠気"],
            "one_change_tomorrow": "朝イチ",
        },
        final=True,
    )
    summary = build_weekly_summary(tmp_path, "2026-07-13")
    assert summary["minimum_line_rate"]["achieved"] == 1
    assert summary["minimum_line_rate"]["total"] == 2
    assert summary["breakdown_ranking"][0] == {"cause": "眠気", "count": 2}


def test_weekly_command_writes_json_and_markdown(tmp_path):
    _write_daily(
        tmp_path,
        "2026-07-13",
        {
            "today_main": [{"area": "院試", "status": "完了"}],
            "minimum_line": {"院試": "達成"},
            "what_went_well": ["できた"],
            "breakdown_causes": [],
            "one_change_tomorrow": "続ける",
        },
        proposal=True,
    )
    result = runner.invoke(
        app, ["weekly", "--date", "2026-07-13", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert (tmp_path / "data" / "weekly" / "2026-07-07_2026-07-13.json").is_file()
    assert (tmp_path / "logs" / "weekly_2026-07-07_2026-07-13.md").is_file()


def test_weekly_collects_pending_proposal_days(tmp_path):
    _write_daily(
        tmp_path,
        "2026-07-13",
        {
            "today_main": [],
            "minimum_line": {},
            "what_went_well": [],
            "breakdown_causes": [],
            "one_change_tomorrow": "続ける",
        },
        proposal=True,
    )
    summary = build_weekly_summary(tmp_path, "2026-07-13")
    assert summary["pending_proposal_days"] == ["2026-07-13"]
