from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_review.notifications import load_history
from daily_review.scheduler import (
    DEFAULT_SCHEDULER_CONFIG,
    load_scheduler_history,
    run_scheduled_job,
)


ZONE = ZoneInfo("Asia/Tokyo")


def test_one_week_scheduler_operation_is_safe_and_idempotent(tmp_path):
    config = json.loads(json.dumps(DEFAULT_SCHEDULER_CONFIG))
    config["enabled"] = True
    enabled = {
        "review_reminder",
        "rollover_preview",
        "backup_create",
        "integrity_check",
        "weekly_report_generate",
        "weekly_report_reminder",
    }
    for job_id, job in config["jobs"].items():
        job["enabled"] = job_id in enabled
    config_path = tmp_path / "config/scheduler.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")

    daily = tmp_path / "data/daily"
    daily.mkdir(parents=True)
    for day in ("2026-07-14", "2026-07-15", "2026-07-20"):
        (daily / f"{day}.json").write_text(
            json.dumps(
                {
                    "date": day,
                    "created_at": f"{day}T20:00:00+09:00",
                    "updated_at": f"{day}T20:00:00+09:00",
                    "raw_log": "記録済み" if day != "2026-07-15" else "",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    day1 = datetime(2026, 7, 14, 21, 0, tzinfo=ZONE)
    first = run_scheduled_job(tmp_path, "review_reminder", current=day1)
    duplicate = run_scheduled_job(tmp_path, "review_reminder", current=day1)
    assert first["status"] == "success"
    assert duplicate["status"] == "skipped"

    late = run_scheduled_job(
        tmp_path,
        "review_reminder",
        current=datetime(2026, 7, 15, 23, 0, tzinfo=ZONE),
    )
    assert late["result_summary"]["action"] == "notification_sent"

    rollover = run_scheduled_job(
        tmp_path,
        "rollover_preview",
        current=datetime(2026, 7, 16, 23, 30, tzinfo=ZONE),
    )
    assert rollover["result_summary"]["action"] == "rollover_preview"
    assert not (tmp_path / "data/rollover/history.json").exists()

    backup = run_scheduled_job(
        tmp_path,
        "backup_create",
        current=datetime(2026, 7, 19, 3, 0, tzinfo=ZONE),
    )
    integrity = run_scheduled_job(
        tmp_path,
        "integrity_check",
        current=datetime(2026, 7, 19, 4, 0, tzinfo=ZONE),
    )
    assert backup["result_summary"]["action"] == "backup_created"
    assert integrity["result_summary"]["action"] == "integrity_checked"

    weekly = run_scheduled_job(
        tmp_path,
        "weekly_report_generate",
        current=datetime(2026, 7, 20, 22, 30, tzinfo=ZONE),
    )
    report_path = tmp_path / "data/weekly/2026-07-14_2026-07-20.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert weekly["status"] == "success"
    assert report["period"] == {
        "start_date": "2026-07-14",
        "end_date": "2026-07-20",
    }
    assert report["status"] == "draft"
    assert "source_revision" in report

    reminder = run_scheduled_job(
        tmp_path,
        "weekly_report_reminder",
        current=datetime(2026, 7, 21, 8, 0, tzinfo=ZONE),
    )
    assert reminder["result_summary"]["action"] == "notification_sent"
    assert any(
        item["notification_type"] == "weekly_report_unapproved"
        for item in load_history(tmp_path)["records"]
    )

    successful_slots = [
        (item["job_id"], item["schedule_slot"])
        for item in load_scheduler_history(tmp_path)["records"]
        if item["status"] == "success"
    ]
    assert len(successful_slots) == len(set(successful_slots))
    assert list((tmp_path / "data/scheduler/audit").glob("*.json"))
    assert list((tmp_path / "backups").glob("*.zip"))
