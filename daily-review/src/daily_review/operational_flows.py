"""Safe morning, nightly, weekly, and monthly operational flows."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .archive import create_backup, plan_backup
from .dashboard import build_daily_summary, next_command
from .date_utils import month_range_for, parse_date, tomorrow_of, week_range_for
from .integrity import run_integrity_check
from .markdown import render_monthly, render_weekly
from .notifications import (
    ConsoleSender,
    FileSender,
    dispatch_notifications,
    evaluate_notifications,
    load_notification_config,
    quiet_hours_end,
)
from .reporting import build_report, weekly_trends
from .rollover import build_rollover_preview
from .storage import (
    atomic_write_text_many,
    monthly_log_path,
    monthly_path,
    read_json_file,
    weekly_log_path,
    weekly_path,
)
from .task_service import query_tasks


FLOW_TYPES = {"morning", "nightly", "weekly", "monthly"}


def _flow_key(kind: str, target: str) -> str:
    return hashlib.sha256(f"{kind}|{target}".encode()).hexdigest()


def _flow_record_path(root: Path, kind: str, target: str) -> Path:
    return (
        root / "data" / "scheduler" / "idempotency" / f"{_flow_key(kind, target)}.json"
    )


def _audit_path(root: Path, execution_id: str) -> Path:
    return root / "data" / "scheduler" / "audit" / f"{execution_id}.json"


def _flow_target(
    kind: str,
    *,
    day: str | None,
    month: str | None,
    current: datetime,
) -> tuple[str, str | None, str | None]:
    local_day = current.date().isoformat()
    if kind == "monthly":
        if month:
            try:
                parsed = datetime.strptime(month, "%Y-%m")
            except ValueError as exc:
                raise ValueError("--monthはYYYY-MM形式にしてください") from exc
            target_month = parsed.strftime("%Y-%m")
        elif current.day == 1:
            target_month = (current.date().replace(day=1) - timedelta(days=1)).strftime(
                "%Y-%m"
            )
        else:
            target_month = current.strftime("%Y-%m")
        return target_month, None, target_month
    target_day = day or local_day
    parse_date(target_day)
    if kind == "weekly" and current.weekday() == 1 and day is None:
        target_day = (current.date() - timedelta(days=1)).isoformat()
    return target_day, target_day, None


def _notifications(
    root: Path, day: str, current: datetime, *, dry_run: bool
) -> dict[str, Any]:
    config = load_notification_config(root)
    candidates = evaluate_notifications(root, day=day, current=current, config=config)
    deferred = quiet_hours_end(current, config)
    if deferred and candidates:
        return {
            "status": "deferred",
            "deferred_until": deferred.isoformat(timespec="seconds"),
            "candidates": [item.__dict__ for item in candidates],
        }
    if dry_run:
        return {
            "status": "preview",
            "candidates": [item.__dict__ for item in candidates],
            "sent": 0,
        }
    console_messages: list[str] = []
    senders = []
    if config["console"]["enabled"]:
        senders.append(ConsoleSender(writer=console_messages.append))
    if config["file"]["enabled"]:
        senders.append(FileSender(root))
    result = dispatch_notifications(
        root, candidates, current=current, config=config, senders=senders
    )
    result["console_messages"] = console_messages
    return result


def _generate_instruction_draft(
    root: Path, day: str, current: datetime, *, dry_run: bool
) -> dict[str, Any]:
    summary = build_daily_summary(root, day)
    if summary["tomorrow_proposal"] or summary["tomorrow_final"]:
        return {"status": "exists", "target_date": tomorrow_of(day)}
    if not summary["night_review_exists"]:
        return {"status": "not_ready", "reason": "review_missing"}
    if dry_run:
        return {
            "status": "planned",
            "command": "generate_instruction",
            "target_date": tomorrow_of(day),
        }
    from .command_api import CommandExecutor

    payload = {
        "version": "1",
        "request_id": f"flow-nightly-{day}",
        "idempotency_key": f"flow-nightly-instruction-{day}",
        "mode": "preview",
        "timezone": str(current.tzinfo or "Asia/Tokyo"),
        "effective_date": day,
        "source": "scheduler_flow",
        "commands": [
            {
                "type": "generate_instruction",
                "payload": {"target_date": tomorrow_of(day)},
            }
        ],
    }
    executor = CommandExecutor(root, clock=lambda: current)
    preview = executor.execute(payload)
    if preview.status not in {"preview_ready", "preview_replay"}:
        return {"status": "failed", "response": preview.model_dump(mode="json")}
    payload["mode"] = "commit"
    payload["confirmation_token"] = preview.confirmation_token
    committed = executor.execute(payload)
    return {
        "status": committed.status,
        "target_date": tomorrow_of(day),
        "response": committed.model_dump(mode="json"),
    }


def _morning(root: Path, day: str) -> dict[str, Any]:
    summary = build_daily_summary(root, day)
    final = summary["today_final"] or {}
    tasks = final.get("tasks") or []
    main_names = set(final.get("main") or [])
    main = [
        task
        for task in tasks
        if task.get("area") in main_names or task.get("task") in main_names
    ][:3]
    minimum = [
        {"task_id": task.get("id"), "minimum_line": task.get("minimum_line")}
        for task in tasks
        if task.get("minimum_line")
    ]
    previous = (parse_date(day) - timedelta(days=1)).isoformat()
    previous_summary = build_daily_summary(root, previous)
    return {
        "instruction_status": "confirmed" if final else "missing",
        "main": main,
        "minimum": minimum,
        "overdue": query_tasks(root, today=day, due="overdue"),
        "previous_incomplete": previous_summary["incomplete_tasks"],
        "next_action": next_command(summary),
        "warnings": summary["errors"],
    }


def _nightly(
    root: Path, day: str, current: datetime, *, dry_run: bool
) -> dict[str, Any]:
    summary = build_daily_summary(root, day)
    draft = _generate_instruction_draft(root, day, current, dry_run=dry_run)
    refreshed = build_daily_summary(root, day) if not dry_run else summary
    integrity = run_integrity_check(root)
    return {
        "review_status": "recorded" if summary["night_review_exists"] else "missing",
        "instruction_draft": draft,
        "instruction_status": (
            "approved"
            if refreshed["tomorrow_final"]
            else "unapproved"
            if refreshed["tomorrow_proposal"]
            else "missing"
        ),
        "notifications": _notifications(root, day, current, dry_run=dry_run),
        "rollover": build_rollover_preview(root, tomorrow_of(day)),
        "incomplete_main": refreshed["incomplete_tasks"],
        "integrity": {
            "status": integrity["status"],
            "counts": integrity["counts"],
        },
        "backup": {
            "status": "not_required",
            "preview": {
                "file_count": plan_backup(root)["file_count"],
            },
        },
        "summary": refreshed,
        "next_action": next_command(refreshed),
    }


def _weekly(
    root: Path, day: str, current: datetime, *, dry_run: bool, force: bool
) -> dict[str, Any]:
    start, end = week_range_for(day)
    report = build_report(root, start, end, period_type="weekly")
    report.update(
        {
            "status": "draft",
            "source_revision": _source_revision(root, start, end),
            "generated_at": current.isoformat(timespec="seconds"),
        }
    )
    path = weekly_path(root, start, end)
    existing = read_json_file(path) if path.exists() else None
    unchanged = (
        isinstance(existing, dict)
        and existing.get("source_revision") == report["source_revision"]
    )
    stale = isinstance(existing, dict) and not unchanged
    if not dry_run and not unchanged and (not stale or force):
        atomic_write_text_many(
            [
                (path, _json_text(report)),
                (weekly_log_path(root, start, end), render_weekly(report)),
            ]
        )
    return {
        "period": {"start_date": start, "end_date": end},
        "report_status": (
            "unchanged"
            if unchanged
            else "stale"
            if stale and not force
            else "planned"
            if dry_run
            else "saved"
        ),
        "report": report,
        "chatgpt_summary": report["improvement_suggestion"]["text"],
        "next_week_change_candidate": report["next_week_change_candidate"],
        "notification_required": not (
            isinstance(existing, dict) and existing.get("status") == "approved"
        ),
    }


def _monthly(
    root: Path, month: str, current: datetime, *, dry_run: bool, force: bool
) -> dict[str, Any]:
    start, end = month_range_for(f"{month}-01")
    today = current.date().isoformat()
    if start > today:
        raise ValueError("未来の月は月次flowの対象にできません")
    effective_end = min(end, today)
    report = build_report(root, start, effective_end, period_type="monthly")
    report["weekly_trends"] = weekly_trends(root, start, effective_end)
    previous_last = parse_date(start) - timedelta(days=1)
    previous_start, previous_end = month_range_for(previous_last.isoformat())
    previous = build_report(root, previous_start, previous_end, period_type="monthly")
    report.update(
        {
            "status": "draft",
            "source_revision": _source_revision(root, start, effective_end),
            "generated_at": current.isoformat(timespec="seconds"),
            "previous_month_comparison": {
                "month": previous_start[:7],
                "main_completion_percent": previous["main_summary"]["percent"],
                "minimum_percent": previous["minimum_line_summary"]["percent"],
            },
            "next_month_focus_candidate": report["improvement_suggestion"]["text"],
        }
    )
    path = monthly_path(root, month)
    existing = read_json_file(path) if path.exists() else None
    unchanged = (
        isinstance(existing, dict)
        and existing.get("source_revision") == report["source_revision"]
    )
    stale = isinstance(existing, dict) and not unchanged
    backup: dict[str, Any]
    if dry_run:
        backup = {
            "status": "planned",
            "file_count": plan_backup(root)["file_count"],
        }
    elif unchanged or stale and not force:
        backup = {"status": "not_required"}
    else:
        atomic_write_text_many(
            [
                (path, _json_text(report)),
                (monthly_log_path(root, month), render_monthly(report)),
            ]
        )
        backup_path, manifest = create_backup(root, manual=False)
        backup = {
            "status": "created",
            "path": str(backup_path),
            "backup_id": manifest["backup_id"],
        }
    return {
        "period": {"start_date": start, "end_date": effective_end},
        "report_status": (
            "unchanged"
            if unchanged
            else "stale"
            if stale and not force
            else "planned"
            if dry_run
            else "saved"
        ),
        "report": report,
        "backup": backup,
        "notification_required": not (
            isinstance(existing, dict) and existing.get("status") == "approved"
        ),
    }


def _source_revision(root: Path, start: str, end: str) -> str:
    digest = hashlib.sha256()
    directory = root / "data" / "daily"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        if start <= path.stem <= end:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _flow_source_revision(root: Path, kind: str, target: str) -> str:
    if kind == "weekly":
        start, end = week_range_for(target)
        return _source_revision(root, start, end)
    if kind == "monthly":
        start, end = month_range_for(f"{target}-01")
        return _source_revision(root, start, end)
    if kind == "nightly":
        return _source_revision(root, target, target)
    return "read-only"


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def run_operational_flow(
    root: Path,
    kind: str,
    *,
    day: str | None = None,
    month: str | None = None,
    current: datetime,
    dry_run: bool = False,
    force: bool = False,
    source: str = "cli",
) -> dict[str, Any]:
    if kind not in FLOW_TYPES:
        raise ValueError(f"未対応のflowです: {kind}")
    target, target_day, target_month = _flow_target(
        kind, day=day, month=month, current=current
    )
    source_revision = _flow_source_revision(root, kind, target)
    record_path = _flow_record_path(root, kind, target)
    if record_path.exists() and not force and not dry_run:
        record = read_json_file(record_path)
        if (
            isinstance(record, dict)
            and record.get("source_revision") == source_revision
        ):
            return {**record["result"], "idempotent_replay": True}
    execution_id = (
        f"flow-{kind}-"
        f"{hashlib.sha256(f'{kind}|{target}|{source_revision}'.encode()).hexdigest()[:12]}"
    )
    if kind == "morning":
        details = _morning(root, target_day or target)
    elif kind == "nightly":
        details = _nightly(root, target_day or target, current, dry_run=dry_run)
    elif kind == "weekly":
        details = _weekly(
            root, target_day or target, current, dry_run=dry_run, force=force
        )
    else:
        details = _monthly(
            root, target_month or target, current, dry_run=dry_run, force=force
        )
    result = {
        "execution_id": execution_id,
        "flow": kind,
        "target": target,
        "status": "dry_run" if dry_run else "success",
        "dry_run": dry_run,
        "idempotency_key": _flow_key(kind, target),
        "details": details,
    }
    if not dry_run:
        record = {
            "version": "1",
            "flow": kind,
            "target": target,
            "source": source,
            "executed_at": current.isoformat(timespec="seconds"),
            "source_revision": _flow_source_revision(root, kind, target),
            "result": result,
        }
        atomic_write_text_many(
            [
                (record_path, _json_text(record)),
                (_audit_path(root, execution_id), _json_text(record)),
            ]
        )
    return result
