from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.date_utils import month_range_for
from daily_review.weekly import build_weekly_summary


runner = CliRunner()


def _save_entry(root, source_day: str, target_day: str) -> None:
    path = root / "data" / "daily" / f"{source_day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "date": source_day,
                "created_at": f"{source_day}T22:00:00+09:00",
                "updated_at": f"{source_day}T22:00:00+09:00",
                "structured_review": {
                    "minimum_line": {"院試": "達成"},
                    "breakdown_causes": ["睡眠不足"],
                    "what_went_well": ["早起きできた"],
                },
                "tomorrow_plan_final": {
                    "status": "approved",
                    "target_date": target_day,
                    "main": ["院試"],
                    "tasks": [
                        {
                            "id": "task-1",
                            "area": "院試",
                            "task": "過去問",
                            "priority": 1,
                            "minimum_line": "開く",
                        }
                    ],
                    "one_change_tomorrow": "続ける",
                },
                "task_results": [
                    {
                        "task_id": "task-1",
                        "status": "partial",
                        "minimum_line_achieved": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_phase7_weekly_keeps_unrecorded_separate_and_ranks_causes(tmp_path):
    _save_entry(tmp_path, "2026-07-14", "2026-07-14")
    summary = build_weekly_summary(tmp_path, "2026-07-14")
    assert summary["main_summary"]["completed"] == 0
    assert summary["main_summary"]["partial"] == 1
    assert summary["main_summary"]["unrecorded"] == 0
    assert summary["failure_reasons"] == [{"cause": "睡眠不足", "count": 1}]
    assert summary["minimum_line_summary"]["percent"] == 100.0


def test_phase7_monthly_saves_both_formats_and_handles_leap_year(tmp_path):
    assert month_range_for("2024-02-14") == ("2024-02-01", "2024-02-29")
    result = runner.invoke(
        app, ["monthly", "--date", "2024-02-14", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert (tmp_path / "data" / "monthly" / "2024-02.json").is_file()
    assert (tmp_path / "logs" / "monthly_2024-02.md").is_file()
