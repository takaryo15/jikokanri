from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from daily_review.cli import app
from daily_review.notifications import load_notification_config, quiet_hours_end
from daily_review.scheduler import (
    DEFAULT_SCHEDULER_CONFIG,
    SchedulerError,
    due_jobs,
    load_scheduler_history,
    run_due_jobs,
    run_scheduled_job,
    schedule_slot,
    configured_jobs,
    scheduler_doctor,
)


ZONE = ZoneInfo("Asia/Tokyo")
runner = CliRunner()


def _configure(root: Path, **overrides) -> None:
    value = json.loads(json.dumps(DEFAULT_SCHEDULER_CONFIG))
    value["enabled"] = True
    for job in value["jobs"].values():
        job["enabled"] = False
    for job_id, settings in overrides.items():
        value["jobs"][job_id]["enabled"] = True
        value["jobs"][job_id].update(settings)
    path = root / "config" / "scheduler.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_daily_due_before_exact_grace_and_expired(tmp_path):
    _configure(tmp_path, review_reminder={})
    before = due_jobs(tmp_path, datetime(2026, 7, 20, 20, 59, tzinfo=ZONE))
    exact = due_jobs(tmp_path, datetime(2026, 7, 20, 21, 0, tzinfo=ZONE))
    grace = due_jobs(tmp_path, datetime(2026, 7, 20, 23, 0, tzinfo=ZONE))
    expired = due_jobs(tmp_path, datetime(2026, 7, 21, 7, 30, tzinfo=ZONE))
    assert not before["due"]
    assert exact["due"][0]["reason"] == "scheduled"
    assert grace["due"][0]["reason"] == "missed"
    assert not expired["due"]
    assert any(item["reason"] == "grace_expired" for item in expired["skipped"])


def test_daily_weekly_monthly_slots_and_calendar_edges(tmp_path):
    _configure(
        tmp_path,
        review_reminder={},
        weekly_report_generate={},
        monthly_report_generate={},
        monthly_report_reminder={},
    )
    jobs = {item.id: item for item in configured_jobs(tmp_path)}
    monday = datetime(2026, 7, 20, 22, 30, tzinfo=ZONE)
    assert schedule_slot(jobs["review_reminder"], monday).endswith("2026-07-20")
    assert schedule_slot(jobs["weekly_report_generate"], monday).endswith(
        "2026-07-14_2026-07-20"
    )
    leap = datetime(2028, 2, 29, 23, 0, tzinfo=ZONE)
    month_due = due_jobs(tmp_path, leap)
    assert any(
        item["schedule_slot"] == "monthly_report_generate:2028-02"
        for item in month_due["due"]
    )
    year_start = due_jobs(tmp_path, datetime(2027, 1, 1, 8, 0, tzinfo=ZONE))
    assert any(
        item["schedule_slot"] == "monthly_report_reminder:2026-12"
        for item in year_start["due"]
    )


def test_disabled_default_is_backward_compatible_and_writes_nothing(tmp_path):
    result = run_due_jobs(
        tmp_path,
        datetime(2026, 7, 20, 21, 0, tzinfo=ZONE),
        dry_run=True,
    )
    assert result["jobs"] == []
    assert not (tmp_path / "data").exists()


def test_success_history_prevents_duplicate_slot_but_force_runs(tmp_path):
    _configure(tmp_path, review_reminder={})
    current = datetime(2026, 7, 20, 21, 0, tzinfo=ZONE)
    first = run_scheduled_job(tmp_path, "review_reminder", current=current)
    duplicate = run_scheduled_job(tmp_path, "review_reminder", current=current)
    forced = run_scheduled_job(tmp_path, "review_reminder", current=current, force=True)
    assert first["status"] == "success"
    assert duplicate["status"] == "skipped"
    assert forced["status"] == "success"
    assert len(load_scheduler_history(tmp_path)["records"]) == 2


def test_retry_backoff_and_success_on_next_poll(tmp_path, monkeypatch):
    _configure(tmp_path, review_reminder={})
    import daily_review.scheduler as scheduler

    current = datetime(2026, 7, 20, 21, 0, tzinfo=ZONE)

    def fail(*args, **kwargs):
        raise OSError("temporary")

    monkeypatch.setattr(scheduler, "_execute_job_action", fail)
    failed = run_scheduled_job(tmp_path, "review_reminder", current=current)
    assert failed["status"] == "failed"
    assert failed["attempt"] == 1
    assert failed["retry_at"] == (current + timedelta(minutes=5)).isoformat(
        timespec="seconds"
    )
    pending = due_jobs(tmp_path, current + timedelta(minutes=4))
    assert any(item["reason"] == "retry_pending" for item in pending["skipped"])

    monkeypatch.setattr(
        scheduler, "_execute_job_action", lambda *args, **kwargs: {"action": "ok"}
    )
    retry = run_due_jobs(tmp_path, current + timedelta(minutes=5))
    assert retry["jobs"][0]["status"] == "success"
    assert retry["jobs"][0]["attempt"] == 2


def test_non_retryable_failure_and_lock_release(tmp_path, monkeypatch):
    _configure(tmp_path, review_reminder={})
    import daily_review.scheduler as scheduler

    def fail(*args, **kwargs):
        raise SchedulerError("INVALID_PAYLOAD", "bad", retryable=False)

    monkeypatch.setattr(scheduler, "_execute_job_action", fail)
    current = datetime(2026, 7, 20, 21, 0, tzinfo=ZONE)
    record = run_scheduled_job(tmp_path, "review_reminder", current=current)
    assert record["status"] == "failed"
    assert record["retry_at"] is None
    assert not list((tmp_path / "data/scheduler/locks").glob("*.lock"))


def test_quiet_hours_cross_midnight_and_old_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "notifications.json").write_text(
        json.dumps(
            {
                "quiet_hours": {
                    "enabled": True,
                    "start": "23:00",
                    "end": "07:00",
                }
            }
        ),
        encoding="utf-8",
    )
    config = load_notification_config(tmp_path)
    current = datetime(2026, 7, 20, 23, 30, tzinfo=ZONE)
    assert quiet_hours_end(current, config) == datetime(2026, 7, 21, 7, 0, tzinfo=ZONE)
    assert quiet_hours_end(datetime(2026, 7, 20, 12, 0, tzinfo=ZONE), config) is None


def test_scheduler_history_reads_legacy_versionless_file(tmp_path):
    path = tmp_path / "data/scheduler/history.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"records": []}', encoding="utf-8")
    assert load_scheduler_history(tmp_path)["version"] == "0"


def test_missed_skip_policy_does_not_run(tmp_path):
    _configure(
        tmp_path,
        review_reminder={"missed_run_policy": "skip"},
    )
    result = due_jobs(tmp_path, datetime(2026, 7, 20, 23, 0, tzinfo=ZONE))
    assert not result["due"]
    assert any(item["reason"] == "missed_policy_skip" for item in result["skipped"])


def test_quiet_notification_is_deferred_until_next_allowed_poll(tmp_path):
    _configure(tmp_path, review_reminder={})
    notification = tmp_path / "config/notifications.json"
    notification.write_text(
        json.dumps(
            {
                "quiet_hours": {
                    "enabled": True,
                    "start": "20:00",
                    "end": "22:00",
                }
            }
        ),
        encoding="utf-8",
    )
    current = datetime(2026, 7, 20, 21, 0, tzinfo=ZONE)
    result = run_scheduled_job(tmp_path, "review_reminder", current=current)
    assert result["status"] == "deferred"
    assert result["retry_at"] == datetime(2026, 7, 20, 22, 0, tzinfo=ZONE).isoformat(
        timespec="seconds"
    )
    assert due_jobs(tmp_path, current + timedelta(minutes=30))["due"] == []


def test_scheduler_doctor_normal_corrupt_history_and_stale_lock(tmp_path, monkeypatch):
    _configure(tmp_path, review_reminder={})
    import daily_review.scheduler as scheduler

    monkeypatch.setattr(scheduler.shutil, "which", lambda command: "/bin/daily-review")
    current = datetime(2026, 7, 20, 21, 0, tzinfo=ZONE)
    normal = scheduler_doctor(tmp_path, current)
    assert normal["status"] == "ok"

    history = tmp_path / "data/scheduler/history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("{", encoding="utf-8")
    corrupt = scheduler_doctor(tmp_path, current)
    assert corrupt["status"] == "error"
    assert any(item["code"] == "INVALID_HISTORY" for item in corrupt["issues"])

    history.write_text('{"records": []}', encoding="utf-8")
    lock = tmp_path / "data/scheduler/locks/stale.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(
        '{"started_at":"2026-07-20T19:00:00+09:00"}',
        encoding="utf-8",
    )
    preview = scheduler_doctor(tmp_path, current, repair=True, dry_run=True)
    assert any(item["code"] == "STALE_SCHEDULER_LOCK" for item in preview["issues"])
    assert lock.exists()
    scheduler_doctor(tmp_path, current, repair=True)
    assert not lock.exists()


def test_scheduler_cli_invalid_config_is_structured_without_traceback(tmp_path):
    config = tmp_path / "config/scheduler.json"
    config.parent.mkdir(parents=True)
    config.write_text("{", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "scheduler",
            "due",
            "--root",
            str(tmp_path),
            "--at",
            "2026-07-20T21:00:00+09:00",
        ],
    )
    assert result.exit_code == 3
    assert "ERROR [INVALID_CONFIG]" in result.output
    assert "Traceback" not in result.output
