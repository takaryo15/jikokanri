from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_review.operational_flows import run_operational_flow


ZONE = ZoneInfo("Asia/Tokyo")


def _daily(root: Path, day: str, raw: str = "振り返り") -> None:
    path = root / "data/daily" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "date": day,
                "created_at": f"{day}T21:00:00+09:00",
                "updated_at": f"{day}T21:00:00+09:00",
                "raw_log": raw,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_all_flow_dry_runs_do_not_write(tmp_path):
    current = datetime(2026, 7, 20, 22, 30, tzinfo=ZONE)
    for kind, options in (
        ("morning", {"day": "2026-07-20"}),
        ("nightly", {"day": "2026-07-20"}),
        ("weekly", {"day": "2026-07-20"}),
        ("monthly", {"month": "2026-07"}),
    ):
        before = {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        result = run_operational_flow(
            tmp_path, kind, current=current, dry_run=True, **options
        )
        after = {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        assert result["status"] == "dry_run"
        assert after == before


def test_morning_missing_and_main_is_limited_to_three(tmp_path):
    result = run_operational_flow(
        tmp_path,
        "morning",
        day="2026-07-20",
        current=datetime(2026, 7, 20, 7, 30, tzinfo=ZONE),
        dry_run=True,
    )
    assert result["details"]["instruction_status"] == "missing"
    assert len(result["details"]["main"]) <= 3


def test_weekly_monday_and_tuesday_missed_use_tuesday_monday(tmp_path):
    monday = run_operational_flow(
        tmp_path,
        "weekly",
        day="2026-07-20",
        current=datetime(2026, 7, 20, 22, 30, tzinfo=ZONE),
        dry_run=True,
    )
    tuesday = run_operational_flow(
        tmp_path,
        "weekly",
        current=datetime(2026, 7, 21, 8, 0, tzinfo=ZONE),
        dry_run=True,
    )
    expected = {"start_date": "2026-07-14", "end_date": "2026-07-20"}
    assert monday["details"]["period"] == expected
    assert tuesday["details"]["period"] == expected


def test_weekly_save_is_idempotent_and_audited(tmp_path):
    _daily(tmp_path, "2026-07-20")
    current = datetime(2026, 7, 20, 22, 30, tzinfo=ZONE)
    first = run_operational_flow(tmp_path, "weekly", day="2026-07-20", current=current)
    second = run_operational_flow(tmp_path, "weekly", day="2026-07-20", current=current)
    assert first["details"]["period"]["start_date"] == "2026-07-14"
    assert second["idempotent_replay"] is True
    assert (tmp_path / "data/weekly/2026-07-14_2026-07-20.json").is_file()
    assert list((tmp_path / "data/scheduler/audit").glob("*.json"))


def test_monthly_first_day_targets_previous_month_and_compares(tmp_path):
    result = run_operational_flow(
        tmp_path,
        "monthly",
        current=datetime(2026, 8, 1, 8, 0, tzinfo=ZONE),
        dry_run=True,
    )
    assert result["target"] == "2026-07"
    assert result["details"]["period"] == {
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
    }
    assert "previous_month_comparison" in result["details"]["report"]


def test_nightly_never_approves_or_applies_rollover(tmp_path):
    _daily(tmp_path, "2026-07-20")
    result = run_operational_flow(
        tmp_path,
        "nightly",
        day="2026-07-20",
        current=datetime(2026, 7, 20, 22, 30, tzinfo=ZONE),
        dry_run=True,
    )
    assert result["details"]["instruction_draft"]["status"] == "planned"
    assert result["details"]["rollover"]["status"] == "preview_ready"
    value = json.loads(
        (tmp_path / "data/daily/2026-07-20.json").read_text(encoding="utf-8")
    )
    assert "tomorrow_plan_final" not in value


def test_unwell_nightly_flow_suggests_smaller_minimum_without_applying(tmp_path):
    _daily(tmp_path, "2026-07-19")
    path = tmp_path / "data/daily/2026-07-19.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["structured_review"] = {"breakdown_causes": ["体調不良と眠気"]}
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    previous = tmp_path / "data/daily/2026-07-18.json"
    previous.write_text(
        json.dumps(
            {
                "date": "2026-07-18",
                "tomorrow_plan_final": {
                    "status": "approved",
                    "target_date": "2026-07-19",
                    "main": ["休養"],
                    "tasks": [
                        {
                            "id": "rest",
                            "area": "休養",
                            "task": "休む",
                            "minimum_line": "水を飲む",
                        }
                    ],
                },
                "task_results": [
                    {
                        "task_id": "rest",
                        "status": "minimum_only",
                        "minimum_line_achieved": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()

    result = run_operational_flow(
        tmp_path,
        "nightly",
        day="2026-07-19",
        current=datetime(2026, 7, 19, 22, 30, tzinfo=ZONE),
        dry_run=True,
    )

    suggestion = result["details"]["minimum_adjustment"]
    assert suggestion["recommended"] is True
    assert suggestion["automatic_change"] is False
    assert "縮小" in suggestion["proposal"]
    assert path.read_bytes() == before
