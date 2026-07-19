"""Poll-based local scheduler with persistent slots, retries, and history."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .archive import create_backup, plan_backup
from .date_utils import month_range_for, parse_date, week_range_for
from .integrity import run_integrity_check
from .notifications import (
    ConsoleSender,
    FileSender,
    Notification,
    NotificationError,
    dispatch_notifications,
    evaluate_notifications,
    load_notification_config,
    make_notification,
    quiet_hours_end,
)
from .rollover import build_rollover_preview
from .storage import atomic_write_json_data, read_json_file


SCHEDULER_VERSION = "1"
HISTORY_LIMIT = 10_000
VALID_MISSED_POLICIES = {"skip", "run_once", "notify_only"}
RETRYABLE_CODES = {
    "LOCKED",
    "IO_ERROR",
    "NOTIFICATION_FAILED",
    "BACKUP_FAILED",
    "TEMPORARY_ERROR",
}
JOB_TYPES = {
    "review_reminder",
    "instruction_approval_reminder",
    "morning_instruction_check",
    "overdue_task_check",
    "incomplete_main_check",
    "minimum_check",
    "rollover_preview",
    "weekly_report_generate",
    "weekly_report_reminder",
    "monthly_report_generate",
    "monthly_report_reminder",
    "backup_create",
    "integrity_check",
    "cleanup",
}


def _job(
    at: str,
    *,
    enabled: bool = True,
    grace: int = 180,
    weekday: str | None = None,
    day: str | None = None,
    missed: str = "run_once",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "enabled": enabled,
        "time": at,
        "grace_minutes": grace,
        "missed_run_policy": missed,
        "timeout_minutes": 10,
        "retry": {
            "max_attempts": 3,
            "initial_delay_minutes": 5,
            "multiplier": 2,
            "max_delay_minutes": 60,
        },
        "payload": {},
    }
    if weekday:
        value["weekday"] = weekday
    if day:
        value["day"] = day
    return value


DEFAULT_SCHEDULER_CONFIG: dict[str, Any] = {
    "enabled": False,
    "timezone": "Asia/Tokyo",
    "poll_interval_minutes": 15,
    "lock_timeout_minutes": 30,
    "history_retention_days": 90,
    "jobs": {
        "review_reminder": _job("21:00", grace=180),
        "instruction_approval_reminder": _job("22:00", grace=180),
        "morning_instruction_check": _job("07:30", grace=240),
        "overdue_task_check": _job("08:00", grace=240),
        "incomplete_main_check": _job("20:30", grace=180),
        "minimum_check": _job("20:30", grace=180),
        "rollover_preview": _job("23:30", grace=240),
        "weekly_report_generate": _job("22:30", weekday="monday", grace=720),
        "weekly_report_reminder": _job("08:00", weekday="tuesday", grace=240),
        "monthly_report_generate": _job("23:00", day="last", grace=1440),
        "monthly_report_reminder": _job("08:00", day="first", grace=720),
        "backup_create": _job("03:00", grace=720),
        "integrity_check": _job("04:00", weekday="sunday", grace=1440),
        "cleanup": _job("04:30", weekday="sunday", grace=1440),
    },
}


class SchedulerError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class ScheduledJob:
    id: str
    job_type: str
    enabled: bool
    schedule: dict[str, Any]
    timezone: str
    payload: dict[str, Any]
    retry_policy: dict[str, Any]
    missed_run_policy: str
    timeout_minutes: int


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _merge_known(defaults: dict[str, Any], custom: dict[str, Any]) -> dict[str, Any]:
    result = _deep_copy(defaults)
    for key, value in custom.items():
        if key not in result:
            continue
        if isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_known(result[key], value)
        else:
            result[key] = value
    return result


def scheduler_config_path(root: Path) -> Path:
    return root / "config" / "scheduler.json"


def load_scheduler_config(root: Path) -> dict[str, Any]:
    path = scheduler_config_path(root)
    if not path.exists():
        return _deep_copy(DEFAULT_SCHEDULER_CONFIG)
    try:
        value = read_json_file(path)
    except (OSError, ValueError) as exc:
        raise SchedulerError(
            "INVALID_CONFIG", f"scheduler.jsonを読み込めません: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SchedulerError(
            "INVALID_CONFIG", "scheduler.jsonはJSONオブジェクトにしてください"
        )
    config = _merge_known(DEFAULT_SCHEDULER_CONFIG, value)
    if not isinstance(config["enabled"], bool):
        raise SchedulerError(
            "INVALID_CONFIG", "scheduler.enabledはtrueまたはfalseにしてください"
        )
    try:
        ZoneInfo(config["timezone"])
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise SchedulerError(
            "INVALID_TIMEZONE", "scheduler.timezoneが不正です"
        ) from exc
    for key in (
        "poll_interval_minutes",
        "lock_timeout_minutes",
        "history_retention_days",
    ):
        setting = config[key]
        if not isinstance(setting, int) or isinstance(setting, bool) or setting < 1:
            raise SchedulerError(
                "INVALID_CONFIG", f"scheduler.{key}は1以上の整数にしてください"
            )
    if not isinstance(config["jobs"], dict):
        raise SchedulerError(
            "INVALID_CONFIG", "scheduler.jobsはJSONオブジェクトにしてください"
        )
    for job_id, setting in config["jobs"].items():
        if job_id not in JOB_TYPES or not isinstance(setting, dict):
            raise SchedulerError("INVALID_CONFIG", f"不正なscheduler jobです: {job_id}")
        _validate_job(job_id, setting)
    return config


def _validate_job(job_id: str, setting: dict[str, Any]) -> None:
    if not isinstance(setting.get("enabled"), bool):
        raise SchedulerError("INVALID_CONFIG", f"{job_id}.enabledが不正です")
    try:
        datetime.strptime(str(setting.get("time")), "%H:%M")
    except ValueError as exc:
        raise SchedulerError(
            "INVALID_CONFIG", f"{job_id}.timeはHH:MM形式にしてください"
        ) from exc
    for key in ("grace_minutes", "timeout_minutes"):
        value = setting.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SchedulerError(
                "INVALID_CONFIG", f"{job_id}.{key}は1以上の整数にしてください"
            )
    if setting.get("missed_run_policy") not in VALID_MISSED_POLICIES:
        raise SchedulerError("INVALID_CONFIG", f"{job_id}.missed_run_policyが不正です")
    weekday = setting.get("weekday")
    if weekday and weekday not in WEEKDAYS:
        raise SchedulerError("INVALID_CONFIG", f"{job_id}.weekdayが不正です")
    if setting.get("day") not in {None, "first", "last"}:
        raise SchedulerError("INVALID_CONFIG", f"{job_id}.dayが不正です")
    retry = setting.get("retry")
    if not isinstance(retry, dict):
        raise SchedulerError("INVALID_CONFIG", f"{job_id}.retryが不正です")
    for key in ("max_attempts", "initial_delay_minutes", "max_delay_minutes"):
        value = retry.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SchedulerError("INVALID_CONFIG", f"{job_id}.retry.{key}が不正です")
    multiplier = retry.get("multiplier")
    if (
        not isinstance(multiplier, (int, float))
        or isinstance(multiplier, bool)
        or multiplier < 1
    ):
        raise SchedulerError("INVALID_CONFIG", f"{job_id}.retry.multiplierが不正です")


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def configured_jobs(root: Path) -> list[ScheduledJob]:
    config = load_scheduler_config(root)
    jobs = []
    for job_id, setting in sorted(config["jobs"].items()):
        jobs.append(
            ScheduledJob(
                id=job_id,
                job_type=job_id,
                enabled=setting["enabled"],
                schedule={
                    key: setting[key]
                    for key in ("time", "weekday", "day")
                    if key in setting
                },
                timezone=config["timezone"],
                payload=_deep_copy(setting.get("payload", {})),
                retry_policy=_deep_copy(setting["retry"]),
                missed_run_policy=setting["missed_run_policy"],
                timeout_minutes=setting["timeout_minutes"],
            )
        )
    return jobs


def parse_scheduler_at(value: str | None, timezone: str = "Asia/Tokyo") -> datetime:
    zone = ZoneInfo(timezone)
    if value is None:
        return datetime.now(zone)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SchedulerError(
            "INVALID_DATETIME", "--atはISO 8601形式にしてください"
        ) from exc
    return (
        parsed.replace(tzinfo=zone)
        if parsed.tzinfo is None
        else parsed.astimezone(zone)
    )


def _month_last(value: date) -> date:
    start, end = month_range_for(value.isoformat())
    del start
    return parse_date(end)


def planned_datetime(job: ScheduledJob, current: datetime) -> datetime:
    zone = ZoneInfo(job.timezone)
    current = current.astimezone(zone)
    planned_time = datetime.strptime(job.schedule["time"], "%H:%M").time()
    if "weekday" in job.schedule:
        target_weekday = WEEKDAYS[job.schedule["weekday"]]
        days_back = (current.weekday() - target_weekday) % 7
        planned_date = current.date() - timedelta(days=days_back)
        candidate = datetime.combine(planned_date, planned_time, zone)
        if candidate > current:
            candidate -= timedelta(days=7)
        return candidate
    if job.schedule.get("day") in {"first", "last"}:
        if job.schedule["day"] == "first":
            planned_date = current.date().replace(day=1)
        else:
            planned_date = _month_last(current.date())
        candidate = datetime.combine(planned_date, planned_time, zone)
        if candidate > current:
            previous = current.date().replace(day=1) - timedelta(days=1)
            planned_date = (
                previous.replace(day=1) if job.schedule["day"] == "first" else previous
            )
            candidate = datetime.combine(planned_date, planned_time, zone)
        return candidate
    candidate = datetime.combine(current.date(), planned_time, zone)
    return candidate if candidate <= current else candidate - timedelta(days=1)


def schedule_slot(job: ScheduledJob, planned: datetime) -> str:
    if "weekday" in job.schedule:
        reference = (
            planned.date() - timedelta(days=1)
            if job.id == "weekly_report_reminder"
            else planned.date()
        )
        start, end = week_range_for(reference.isoformat())
        return f"{job.id}:{start}_{end}"
    if job.schedule.get("day") in {"first", "last"}:
        reference = (
            planned.date() - timedelta(days=1)
            if job.id == "monthly_report_reminder"
            else planned.date()
        )
        return f"{job.id}:{reference.strftime('%Y-%m')}"
    return f"{job.id}:{planned.date().isoformat()}"


def history_path(root: Path) -> Path:
    return root / "data" / "scheduler" / "history.json"


def load_scheduler_history(root: Path) -> dict[str, Any]:
    path = history_path(root)
    if not path.exists():
        return {"version": SCHEDULER_VERSION, "records": []}
    try:
        value = read_json_file(path)
    except (OSError, ValueError) as exc:
        raise SchedulerError(
            "INVALID_HISTORY", f"scheduler履歴JSONを読み込めません: {exc}"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise SchedulerError("INVALID_HISTORY", "scheduler履歴JSONが不正です")
    if not all(isinstance(item, dict) for item in value["records"]):
        raise SchedulerError("INVALID_HISTORY", "scheduler履歴recordsが不正です")
    value.setdefault("version", "0")
    return value


def scheduler_history(
    root: Path,
    *,
    job_id: str | None = None,
    status: str | None = None,
    day: str | None = None,
) -> list[dict[str, Any]]:
    if day:
        parse_date(day)
    values = list(load_scheduler_history(root)["records"])
    if job_id:
        values = [item for item in values if item.get("job_id") == job_id]
    if status:
        values = [item for item in values if item.get("status") == status]
    if day:
        values = [
            item for item in values if str(item.get("started_at", "")).startswith(day)
        ]
    values.sort(
        key=lambda item: (
            str(item.get("started_at", "")),
            str(item.get("execution_id", "")),
        ),
        reverse=True,
    )
    return values


def _latest_for_slot(
    records: list[dict[str, Any]], job_id: str, slot: str
) -> dict[str, Any] | None:
    matches = [
        item
        for item in records
        if item.get("job_id") == job_id and item.get("schedule_slot") == slot
    ]
    matches.sort(key=lambda item: str(item.get("started_at", "")), reverse=True)
    return matches[0] if matches else None


def _retry_at(record: dict[str, Any]) -> datetime | None:
    value = record.get("retry_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _aligned(value: datetime | None, reference: datetime) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def due_jobs(root: Path, current: datetime) -> dict[str, Any]:
    config = load_scheduler_config(root)
    current = current.astimezone(ZoneInfo(config["timezone"]))
    records = load_scheduler_history(root)["records"]
    due: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for job in configured_jobs(root):
        planned = planned_datetime(job, current)
        slot = schedule_slot(job, planned)
        base = {
            "job_id": job.id,
            "job_type": job.job_type,
            "schedule_slot": slot,
            "planned_at": planned.isoformat(timespec="seconds"),
            "idempotency_key": hashlib.sha256(slot.encode()).hexdigest(),
            "missed_run_policy": job.missed_run_policy,
        }
        if not config["enabled"]:
            skipped.append({**base, "reason": "scheduler_disabled"})
            continue
        if not job.enabled:
            skipped.append({**base, "reason": "job_disabled"})
            continue
        latest = _latest_for_slot(records, job.id, slot)
        if latest and latest.get("status") == "success":
            skipped.append({**base, "reason": "already_succeeded"})
            continue
        if latest and latest.get("status") == "running":
            started = _aligned(_record_time(latest), current)
            if started is not None and current - started <= timedelta(
                minutes=job.timeout_minutes
            ):
                skipped.append({**base, "reason": "active_execution"})
            else:
                due.append(
                    {
                        **base,
                        "reason": "stale_execution_recovery",
                        "attempt": int(latest.get("attempt") or 1) + 1,
                    }
                )
            continue
        if latest and latest.get("status") in {"failed", "deferred"}:
            attempt = int(latest.get("attempt") or 1)
            if attempt >= job.retry_policy["max_attempts"]:
                skipped.append({**base, "reason": "retry_exhausted"})
                continue
            retry_at = _aligned(_retry_at(latest), current)
            if retry_at and current < retry_at:
                skipped.append(
                    {
                        **base,
                        "reason": "retry_pending",
                        "retry_at": retry_at.isoformat(),
                    }
                )
                continue
            if retry_at:
                due.append({**base, "reason": "retry", "attempt": attempt + 1})
                continue
            skipped.append({**base, "reason": "non_retryable_failure"})
            continue
        elapsed = int((current - planned).total_seconds() // 60)
        setting = config["jobs"][job.id]
        if elapsed < 0:
            skipped.append({**base, "reason": "not_started"})
        elif elapsed <= setting["grace_minutes"]:
            reason = (
                "scheduled" if elapsed < config["poll_interval_minutes"] else "missed"
            )
            if job.missed_run_policy == "skip" and reason == "missed":
                skipped.append({**base, "reason": "missed_policy_skip"})
            else:
                due.append({**base, "reason": reason, "attempt": 1})
        else:
            skipped.append({**base, "reason": "grace_expired"})
    return {
        "enabled": config["enabled"],
        "timezone": config["timezone"],
        "current_at": current.isoformat(timespec="seconds"),
        "due": due,
        "skipped": skipped,
        "active_locks": [
            str(path) for path in sorted((root / "data/scheduler/locks").glob("*.lock"))
        ],
    }


class SchedulerLock:
    def __init__(self, root: Path, name: str, *, current: datetime, timeout: int):
        safe = hashlib.sha256(name.encode()).hexdigest()[:24]
        self.path = root / "data" / "scheduler" / "locks" / f"{safe}.lock"
        self.name = name
        self.current = current
        self.timeout = timedelta(minutes=timeout)
        self.acquired = False

    def __enter__(self) -> "SchedulerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            owner = _read_owner(self.path)
            try:
                started = datetime.fromisoformat(str(owner["started_at"]))
            except (KeyError, ValueError):
                started = self.current - self.timeout - timedelta(seconds=1)
            started = _aligned(started, self.current) or started
            if self.current - started > self.timeout:
                shutil.rmtree(self.path)
        try:
            self.path.mkdir()
        except FileExistsError as exc:
            raise SchedulerError(
                "LOCKED", f"scheduler lockが使用中です: {self.name}", retryable=True
            ) from exc
        atomic_write_json_data(
            self.path / "owner.json",
            {
                "name": self.name,
                "pid": os.getpid(),
                "started_at": self.current.isoformat(timespec="seconds"),
            },
        )
        self.acquired = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.acquired:
            shutil.rmtree(self.path, ignore_errors=True)
            self.acquired = False


def _read_owner(path: Path) -> dict[str, Any]:
    try:
        value = read_json_file(path / "owner.json")
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _notification_candidates(
    root: Path, job_id: str, day: str, current: datetime
) -> list[Notification]:
    config = load_notification_config(root)
    values = evaluate_notifications(root, day=day, current=current, config=config)
    mapping = {
        "review_reminder": {"review_missing"},
        "instruction_approval_reminder": {"instruction_unapproved"},
        "overdue_task_check": {"task_overdue"},
        "incomplete_main_check": {"main_incomplete"},
        "minimum_check": {"minimum_incomplete"},
    }
    if job_id in mapping:
        return [item for item in values if item.notification_type in mapping[job_id]]
    if job_id == "morning_instruction_check":
        from .dashboard import build_daily_summary

        summary = build_daily_summary(root, day)
        if summary["today_final"]:
            return []
        return [
            make_notification(
                "morning_instruction_missing",
                day,
                f"instruction:{day}",
                "今日の指示書",
                f"{day}の確定指示書がありません",
            )
        ]
    if job_id in {"weekly_report_reminder", "monthly_report_reminder"}:
        if job_id.startswith("weekly"):
            previous_monday = parse_date(day) - timedelta(days=1)
            start, end = week_range_for(previous_monday.isoformat())
            path = root / "data" / "weekly" / f"{start}_{end}.json"
            kind, entity = "weekly_report_unapproved", f"weekly:{start}:{end}"
        else:
            previous = parse_date(day).replace(day=1) - timedelta(days=1)
            month = previous.strftime("%Y-%m")
            path = root / "data" / "monthly" / f"{month}.json"
            kind, entity = "monthly_report_unapproved", f"monthly:{month}"
        value = read_json_file(path) if path.exists() else {}
        if isinstance(value, dict) and value.get("status") == "approved":
            return []
        return [
            make_notification(
                kind,
                day,
                entity,
                "レポート確認",
                "生成済みレポートの確認と承認が必要です",
            )
        ]
    return []


def _run_notification_job(
    root: Path, job_id: str, day: str, current: datetime, *, dry_run: bool
) -> dict[str, Any]:
    notifications = _notification_candidates(root, job_id, day, current)
    config = load_notification_config(root)
    deferred_until = quiet_hours_end(current, config)
    if notifications and deferred_until:
        return {
            "action": "notification_deferred",
            "candidates": [asdict(item) for item in notifications],
            "deferred_until": deferred_until.isoformat(timespec="seconds"),
        }
    if dry_run:
        return {
            "action": "notification_preview",
            "candidates": [asdict(item) for item in notifications],
            "sent": 0,
        }
    console_messages: list[str] = []
    senders = []
    if config["console"]["enabled"]:
        senders.append(ConsoleSender(writer=console_messages.append))
    if config["file"]["enabled"]:
        senders.append(FileSender(root))
    result = dispatch_notifications(
        root, notifications, current=current, config=config, senders=senders
    )
    result["action"] = "notification_sent" if result["sent"] else "no_notification"
    result["console_messages"] = console_messages
    if result["failed"]:
        raise SchedulerError(
            "NOTIFICATION_FAILED",
            f"{result['failed']}件の通知送信に失敗しました",
            retryable=True,
        )
    return result


def _execute_job_action(
    root: Path,
    job: ScheduledJob,
    planned: datetime,
    current: datetime,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    day = planned.date().isoformat()
    elapsed = int((current - planned).total_seconds() // 60)
    if (
        job.missed_run_policy == "notify_only"
        and elapsed >= load_scheduler_config(root)["poll_interval_minutes"]
    ):
        candidate = make_notification(
            "scheduler_job_missed",
            day,
            f"scheduler:{job.id}:{schedule_slot(job, planned)}",
            "自動処理の未実行",
            f"{job.id}は予定時刻を過ぎたため処理せず通知だけ行います",
        )
        config = load_notification_config(root)
        deferred = quiet_hours_end(current, config)
        if deferred:
            return {
                "action": "notification_deferred",
                "candidates": [asdict(candidate)],
                "deferred_until": deferred.isoformat(timespec="seconds"),
            }
        if dry_run:
            return {
                "action": "missed_notification_preview",
                "candidates": [asdict(candidate)],
                "sent": 0,
            }
        console_messages: list[str] = []
        result = dispatch_notifications(
            root,
            [candidate],
            current=current,
            config=config,
            senders=[
                *(
                    [ConsoleSender(writer=console_messages.append)]
                    if config["console"]["enabled"]
                    else []
                ),
                *([FileSender(root)] if config["file"]["enabled"] else []),
            ],
        )
        result["action"] = "missed_notification_sent"
        result["console_messages"] = console_messages
        if result["failed"]:
            raise SchedulerError(
                "NOTIFICATION_FAILED",
                f"{result['failed']}件の通知送信に失敗しました",
                retryable=True,
            )
        return result
    if job.id.endswith("_reminder") or job.id in {
        "morning_instruction_check",
        "overdue_task_check",
        "incomplete_main_check",
        "minimum_check",
    }:
        return _run_notification_job(root, job.id, day, current, dry_run=dry_run)
    if job.id == "rollover_preview":
        target = (planned.date() + timedelta(days=1)).isoformat()
        value = build_rollover_preview(root, target)
        return {
            "action": "rollover_preview",
            "target_date": target,
            "candidate_count": len(value["candidates"]),
            "main_task_ids": value["main_task_ids"],
            "preview": value,
        }
    if job.id in {"weekly_report_generate", "monthly_report_generate"}:
        from .operational_flows import run_operational_flow

        if job.id.startswith("weekly"):
            return run_operational_flow(
                root, "weekly", day=day, current=current, dry_run=dry_run
            )
        return run_operational_flow(
            root,
            "monthly",
            month=planned.strftime("%Y-%m"),
            current=current,
            dry_run=dry_run,
        )
    if job.id == "backup_create":
        if dry_run:
            plan = plan_backup(root)
            return {
                "action": "backup_preview",
                "file_count": plan["file_count"],
                "estimated_size": plan["estimated_size"],
                "output": str(plan["output"]),
            }
        path, manifest = create_backup(root, manual=False)
        return {
            "action": "backup_created",
            "path": str(path),
            "backup_id": manifest["backup_id"],
        }
    if job.id == "integrity_check":
        report = run_integrity_check(root)
        return {
            "action": "integrity_checked",
            "status": report["status"],
            "counts": report["counts"],
        }
    if job.id == "cleanup":
        history = load_scheduler_history(root)
        cutoff = current - timedelta(
            days=load_scheduler_config(root)["history_retention_days"]
        )
        kept = [item for item in history["records"] if _record_is_recent(item, cutoff)]
        removed = len(history["records"]) - len(kept)
        notification_path = root / "data/notifications/history.json"
        notification_removed = 0
        notification_value = None
        if notification_path.exists():
            notification_value = read_json_file(notification_path)
            if isinstance(notification_value, dict) and isinstance(
                notification_value.get("records"), list
            ):
                notification_kept = [
                    item
                    for item in notification_value["records"]
                    if _record_is_recent(
                        {
                            "finished_at": item.get("sent_at")
                            or item.get("attempted_at")
                        },
                        cutoff,
                    )
                ]
                notification_removed = len(notification_value["records"]) - len(
                    notification_kept
                )
                notification_value["records"] = notification_kept
        expired_files = []
        for directory in (
            root / "data/api/confirmations",
            root / "data/api/idempotency",
        ):
            for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
                modified = datetime.fromtimestamp(path.stat().st_mtime, current.tzinfo)
                if modified < cutoff:
                    expired_files.append(path)
        if not dry_run and removed:
            atomic_write_json_data(
                history_path(root),
                {"version": SCHEDULER_VERSION, "records": kept},
            )
        if not dry_run and notification_removed and notification_value is not None:
            atomic_write_json_data(notification_path, notification_value)
        if not dry_run:
            for path in expired_files:
                path.unlink(missing_ok=True)
        return {
            "action": "cleanup",
            "history_records_to_remove": removed,
            "notification_records_to_remove": notification_removed,
            "expired_api_files_to_remove": len(expired_files),
        }
    raise SchedulerError("INVALID_JOB", f"未対応のjobです: {job.id}")


def _record_time(record: dict[str, Any]) -> datetime | None:
    value = record.get("finished_at") or record.get("started_at")
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _record_is_recent(record: dict[str, Any], cutoff: datetime) -> bool:
    value = _aligned(_record_time(record), cutoff)
    return value is None or value >= cutoff


def _next_retry(job: ScheduledJob, attempt: int, current: datetime) -> datetime | None:
    if attempt >= job.retry_policy["max_attempts"]:
        return None
    delay = min(
        job.retry_policy["initial_delay_minutes"]
        * (job.retry_policy["multiplier"] ** (attempt - 1)),
        job.retry_policy["max_delay_minutes"],
    )
    return current + timedelta(minutes=float(delay))


def _save_history_record(
    root: Path, record: dict[str, Any], *, current: datetime
) -> None:
    with SchedulerLock(root, "history-write", current=current, timeout=5):
        history = load_scheduler_history(root)
        history["version"] = SCHEDULER_VERSION
        history["records"] = (history["records"] + [record])[-HISTORY_LIMIT:]
        atomic_write_json_data(history_path(root), history)


def run_scheduled_job(
    root: Path,
    job_id: str,
    *,
    current: datetime,
    planned: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
    idempotency_key: str | None = None,
    scheduler_run_id: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    jobs = {job.id: job for job in configured_jobs(root)}
    if job_id not in jobs:
        raise SchedulerError("INVALID_JOB", f"jobが見つかりません: {job_id}")
    job = jobs[job_id]
    current = current.astimezone(ZoneInfo(job.timezone))
    planned = planned or planned_datetime(job, current)
    slot = schedule_slot(job, planned)
    records = load_scheduler_history(root)["records"]
    latest = _latest_for_slot(records, job.id, slot)
    if latest and latest.get("status") == "success" and not force:
        return {
            "job_id": job.id,
            "status": "skipped",
            "schedule_slot": slot,
            "skip_reason": "already_succeeded",
            "execution_id": latest.get("execution_id"),
        }
    attempt = (
        int(latest.get("attempt") or 1) + 1
        if latest and latest.get("status") in {"failed", "deferred", "running"}
        else 1
    )
    execution_id = (
        f"scheduler_dry_{hashlib.sha256(f'{job.id}|{slot}'.encode()).hexdigest()[:16]}"
        if dry_run
        else f"scheduler_exec_{uuid.uuid4().hex[:16]}"
    )
    stable_key = idempotency_key or hashlib.sha256(slot.encode()).hexdigest()
    if dry_run:
        result = _execute_job_action(root, job, planned, current, dry_run=True)
        return {
            "execution_id": execution_id,
            "job_id": job.id,
            "job_type": job.job_type,
            "status": "dry_run",
            "schedule_slot": slot,
            "planned_at": planned.isoformat(timespec="seconds"),
            "attempt": attempt,
            "idempotency_key": stable_key,
            "result": result,
            "dry_run": True,
            "forced": force,
        }
    with SchedulerLock(
        root,
        f"job:{job.id}:{slot}",
        current=current,
        timeout=job.timeout_minutes,
    ):
        started = current.isoformat(timespec="seconds")
        record = {
            "execution_id": execution_id,
            "scheduler_run_id": scheduler_run_id,
            "job_id": job.id,
            "job_type": job.job_type,
            "schedule_slot": slot,
            "planned_at": planned.isoformat(timespec="seconds"),
            "started_at": started,
            "finished_at": None,
            "status": "running",
            "attempt": attempt,
            "idempotency_key": stable_key,
            "result_summary": {},
            "related_entity_ids": [],
            "notification_ids": [],
            "error_code": None,
            "error_message": None,
            "retry_at": None,
            "skip_reason": None,
            "dry_run": False,
            "forced": force,
            "source": source,
        }
        try:
            result = _execute_job_action(root, job, planned, current, dry_run=False)
            if result.get("action") == "notification_deferred":
                status = "deferred"
                retry_at = result["deferred_until"]
            else:
                status = "success"
                retry_at = None
            record.update(
                {
                    "finished_at": current.isoformat(timespec="seconds"),
                    "status": status,
                    "result_summary": result,
                    "notification_ids": [
                        item["notification_id"]
                        for item in result.get("records", [])
                        if item.get("notification_id")
                    ],
                    "retry_at": retry_at,
                }
            )
        except SchedulerError as exc:
            retry_at_value = (
                _next_retry(job, attempt, current) if exc.retryable else None
            )
            record.update(
                {
                    "finished_at": current.isoformat(timespec="seconds"),
                    "status": "failed",
                    "error_code": exc.code,
                    "error_message": str(exc),
                    "retry_at": retry_at_value.isoformat(timespec="seconds")
                    if retry_at_value
                    else None,
                }
            )
        except OSError as exc:
            retry_at_value = _next_retry(job, attempt, current)
            record.update(
                {
                    "finished_at": current.isoformat(timespec="seconds"),
                    "status": "failed",
                    "error_code": "IO_ERROR",
                    "error_message": str(exc),
                    "retry_at": retry_at_value.isoformat(timespec="seconds")
                    if retry_at_value
                    else None,
                }
            )
        except ValueError as exc:
            record.update(
                {
                    "finished_at": current.isoformat(timespec="seconds"),
                    "status": "failed",
                    "error_code": "DATA_ERROR",
                    "error_message": str(exc),
                    "retry_at": None,
                }
            )
        _save_history_record(root, record, current=current)
        return record


def run_due_jobs(
    root: Path,
    current: datetime,
    *,
    dry_run: bool = False,
    source: str = "cli",
) -> dict[str, Any]:
    config = load_scheduler_config(root)
    evaluation = due_jobs(root, current)
    run_id = (
        "scheduler_dry_"
        + hashlib.sha256(
            f"{current.isoformat()}|{','.join(item['schedule_slot'] for item in evaluation['due'])}".encode()
        ).hexdigest()[:16]
        if dry_run
        else f"scheduler_run_{uuid.uuid4().hex[:16]}"
    )
    if dry_run:
        jobs = []
        by_id = {job.id: job for job in configured_jobs(root)}
        for item in evaluation["due"]:
            job = by_id[item["job_id"]]
            planned = datetime.fromisoformat(item["planned_at"])
            jobs.append(
                run_scheduled_job(
                    root,
                    job.id,
                    current=current,
                    planned=planned,
                    dry_run=True,
                    scheduler_run_id=run_id,
                    source=source,
                )
            )
        return {
            "run_id": run_id,
            "started_at": current.isoformat(timespec="seconds"),
            "status": "dry_run",
            "timezone": evaluation["timezone"],
            "jobs": jobs,
            "success_count": 0,
            "failed_count": 0,
            "skipped_count": len(evaluation["skipped"]),
            "skipped": evaluation["skipped"],
        }
    with SchedulerLock(
        root,
        "run-due",
        current=current,
        timeout=config["lock_timeout_minutes"],
    ):
        jobs = []
        for item in evaluation["due"]:
            planned = datetime.fromisoformat(item["planned_at"])
            jobs.append(
                run_scheduled_job(
                    root,
                    item["job_id"],
                    current=current,
                    planned=planned,
                    scheduler_run_id=run_id,
                    source=source,
                )
            )
        return {
            "run_id": run_id,
            "started_at": current.isoformat(timespec="seconds"),
            "finished_at": current.isoformat(timespec="seconds"),
            "status": "completed",
            "timezone": evaluation["timezone"],
            "jobs": jobs,
            "success_count": sum(item["status"] == "success" for item in jobs),
            "failed_count": sum(item["status"] == "failed" for item in jobs),
            "skipped_count": len(evaluation["skipped"])
            + sum(item["status"] == "skipped" for item in jobs),
            "skipped": evaluation["skipped"],
        }


def scheduler_status(root: Path, current: datetime) -> dict[str, Any]:
    from .scheduler_install import resolve_cli_executable

    from .scheduler_install import install_status

    config = load_scheduler_config(root)
    history = scheduler_history(root)
    evaluation = due_jobs(root, current)
    successes = [item for item in history if item.get("status") == "success"]
    latest_by_slot: dict[tuple[str, str], dict[str, Any]] = {}
    for item in history:
        identity = (str(item.get("job_id")), str(item.get("schedule_slot")))
        latest_by_slot.setdefault(identity, item)
    outstanding = list(latest_by_slot.values())
    failed = [item for item in outstanding if item.get("status") == "failed"]
    retries = [
        item
        for item in outstanding
        if item.get("status") in {"failed", "deferred"} and item.get("retry_at")
    ]
    active_locks = list((root / "data/scheduler/locks").glob("*.lock"))
    next_job = _next_job(root, current)
    return {
        "enabled": config["enabled"],
        "timezone": config["timezone"],
        "current_local_datetime": current.astimezone(
            ZoneInfo(config["timezone"])
        ).isoformat(timespec="seconds"),
        "last_successful_run": successes[0].get("finished_at") if successes else None,
        "next_due_job": next_job,
        "active_lock": [str(path) for path in active_locks],
        "failed_jobs": len(failed),
        "pending_retry": len(retries),
        "missed_jobs": sum(
            item.get("reason") == "missed" for item in evaluation["due"]
        ),
        "launchd": install_status(root),
        "config_path": str(scheduler_config_path(root)),
        "data_path": str(root / "data/scheduler"),
        "executable_path": resolve_cli_executable(),
        "python_path": os.path.realpath(os.sys.executable),
    }


def _next_job(root: Path, current: datetime) -> dict[str, Any] | None:
    if not load_scheduler_config(root)["enabled"]:
        return None
    candidates = []
    for job in configured_jobs(root):
        if not job.enabled:
            continue
        planned = planned_datetime(job, current)
        if planned <= current:
            if "weekday" in job.schedule:
                planned += timedelta(days=7)
            elif job.schedule.get("day"):
                first = (planned.date().replace(day=28) + timedelta(days=4)).replace(
                    day=1
                )
                target = first if job.schedule["day"] == "first" else _month_last(first)
                planned = datetime.combine(target, planned.timetz())
            else:
                planned += timedelta(days=1)
        candidates.append((planned, job))
    if not candidates:
        return None
    planned, job = min(candidates, key=lambda item: item[0])
    return {
        "job_id": job.id,
        "planned_at": planned.isoformat(timespec="seconds"),
        "schedule_slot": schedule_slot(job, planned),
    }


def scheduler_doctor(
    root: Path, current: datetime, *, repair: bool = False, dry_run: bool = False
) -> dict[str, Any]:
    from .scheduler_install import resolve_cli_executable

    from .scheduler_install import install_status

    issues: list[dict[str, Any]] = []
    try:
        config = load_scheduler_config(root)
    except SchedulerError as exc:
        return {
            "status": "error",
            "issues": [
                {
                    "code": exc.code,
                    "severity": "error",
                    "message": str(exc),
                    "fixable": False,
                }
            ],
            "repairs": [],
        }
    try:
        executable = resolve_cli_executable()
    except ValueError:
        executable = None
    if not config["enabled"]:
        issues.append(
            _scheduler_issue("SCHEDULER_DISABLED", "warning", "schedulerが無効です")
        )
    if not executable:
        issues.append(
            _scheduler_issue(
                "EXECUTABLE_NOT_FOUND",
                "error",
                "daily-review実行ファイルが見つかりません",
            )
        )
    data_dir = root / "data/scheduler"
    if not data_dir.exists():
        issues.append(
            _scheduler_issue(
                "DATA_DIRECTORY_MISSING",
                "info",
                "data/schedulerは初回実行時に作成されます",
            )
        )
    elif not os.access(data_dir, os.W_OK):
        issues.append(
            _scheduler_issue(
                "DATA_DIRECTORY_UNWRITABLE", "error", "scheduler dataへ書き込めません"
            )
        )
    log_dir = root / "logs"
    if log_dir.exists() and not os.access(log_dir, os.W_OK):
        issues.append(
            _scheduler_issue(
                "LOG_DIRECTORY_UNWRITABLE", "error", "logsへ書き込めません"
            )
        )
    try:
        records = load_scheduler_history(root)["records"]
    except SchedulerError as exc:
        issues.append(_scheduler_issue(exc.code, "error", str(exc)))
        records = []
    latest_by_slot: dict[tuple[str, str], dict[str, Any]] = {}
    for item in reversed(records):
        identity = (str(item.get("job_id")), str(item.get("schedule_slot")))
        latest_by_slot.setdefault(identity, item)
    seen: set[tuple[str, str]] = set()
    for item in records:
        identity = (str(item.get("job_id")), str(item.get("schedule_slot")))
        if item.get("status") == "success" and not item.get("forced"):
            if identity in seen:
                issues.append(
                    _scheduler_issue(
                        "DUPLICATE_SCHEDULE_SLOT",
                        "critical",
                        f"成功slotが重複しています: {identity[0]} {identity[1]}",
                    )
                )
            seen.add(identity)
        if item is latest_by_slot.get(identity) and item.get("status") == "failed":
            retry_at = _aligned(_retry_at(item), current)
            code = (
                "RETRY_OVERDUE"
                if retry_at and retry_at < current
                else "FAILED_JOB_PENDING"
            )
            issues.append(
                _scheduler_issue(
                    code, "warning", f"失敗jobがあります: {item.get('job_id')}"
                )
            )
        if item is latest_by_slot.get(identity) and item.get("status") == "running":
            started = _aligned(_record_time(item), current)
            job = next(
                (
                    candidate
                    for candidate in configured_jobs(root)
                    if candidate.id == item.get("job_id")
                ),
                None,
            )
            timeout = timedelta(
                minutes=job.timeout_minutes if job else config["lock_timeout_minutes"]
            )
            if started and current - started > timeout:
                issues.append(
                    _scheduler_issue(
                        "STALE_EXECUTION",
                        "warning",
                        f"停止した可能性があるjobがあります: {item.get('job_id')}",
                    )
                )
    repairs = []
    lock_timeout = timedelta(minutes=config["lock_timeout_minutes"])
    for lock in sorted((root / "data/scheduler/locks").glob("*.lock")):
        owner = _read_owner(lock)
        try:
            started = datetime.fromisoformat(str(owner["started_at"]))
        except (KeyError, ValueError):
            started = current - lock_timeout - timedelta(seconds=1)
        started = _aligned(started, current) or started
        if current - started > lock_timeout:
            issues.append(
                _scheduler_issue(
                    "STALE_SCHEDULER_LOCK",
                    "warning",
                    f"stale lock: {lock}",
                    fixable=True,
                )
            )
            repairs.append({"action": "remove_stale_lock", "path": str(lock)})
    try:
        notification_config = load_notification_config(root)
        if (
            not notification_config["console"]["enabled"]
            and not notification_config["file"]["enabled"]
        ):
            issues.append(
                _scheduler_issue(
                    "DISABLED_SENDER", "warning", "通知senderがすべて無効です"
                )
            )
    except NotificationError as exc:
        issues.append(
            _scheduler_issue("INVALID_NOTIFICATION_CONFIG", "error", str(exc))
        )
    launchd = install_status(root)
    if not launchd["plist_exists"]:
        issues.append(
            _scheduler_issue("LAUNCHD_NOT_INSTALLED", "info", "launchdは未導入です")
        )
    elif not launchd["matches_expected"]:
        issues.append(
            _scheduler_issue(
                "PLIST_MISMATCH", "warning", "launchd plistが現在設定と一致しません"
            )
        )
    if repair and not dry_run:
        for item in repairs:
            shutil.rmtree(Path(item["path"]), ignore_errors=True)
    counts = {
        level: sum(item["severity"] == level for item in issues)
        for level in ("info", "warning", "error", "critical")
    }
    return {
        "status": "critical"
        if counts["critical"]
        else "error"
        if counts["error"]
        else "warning"
        if counts["warning"]
        else "ok",
        "issues": issues,
        "counts": counts,
        "repairs": repairs,
        "repair_applied": bool(repair and not dry_run and repairs),
        "root": str(root.resolve()),
        "checked_at": current.isoformat(timespec="seconds"),
    }


def _scheduler_issue(
    code: str,
    severity: str,
    message: str,
    *,
    fixable: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "fixable": fixable,
    }
