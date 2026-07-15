"""Structured integrity checks and conservative, backup-first repairs."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .archive import create_backup, list_backups, load_backup_config
from .date_utils import parse_date
from .models import now_iso
from .operation_lock import (
    WorkspaceLock,
    is_current_process_lock,
    is_stale_lock,
    lock_path,
)
from .storage import atomic_write_json_data_many, read_json_file


VALID_TASK_STATUSES = {
    "pending",
    "completed",
    "partial",
    "minimum_only",
    "not_started",
    "skipped",
    "cancelled",
    "archived",
    "deleted",
    "blocked",
    "someday",
}
VALID_PRIORITIES = {"high", "medium", "low", 1, 2, 3}
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def _issue(
    code: str,
    severity: str,
    message: str,
    path: str,
    *,
    fixable: bool = False,
    fix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = {
        "code": code,
        "severity": severity,
        "message": message,
        "path": path,
        "fixable": fixable,
    }
    if fix is not None:
        value["fix"] = fix
    return value


def _valid_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_date(value: Any) -> bool:
    try:
        parse_date(value)
    except (TypeError, ValueError):
        return False
    return True


def _check_plan(
    plan: dict[str, Any],
    path: str,
    issues: list[dict[str, Any]],
    task_ids: dict[str, list[str]],
) -> None:
    main = plan.get("main") or []
    if isinstance(main, list) and len(main) > 3:
        issues.append(
            _issue(
                "MAIN_LIMIT_EXCEEDED",
                "error",
                "Mainが4件以上あります",
                path,
                fixable=True,
                fix={"operation": "move_main_overflow", "plan_path": path},
            )
        )
    status = plan.get("status")
    if status == "approved" and not _valid_iso(plan.get("approved_at")):
        issues.append(
            _issue(
                "MISSING_APPROVED_AT",
                "error",
                "承認済み指示書にapproved_atがありません",
                path,
            )
        )
    if status != "approved" and plan.get("approved_at"):
        issues.append(
            _issue(
                "DRAFT_HAS_APPROVED_AT",
                "warning",
                "未承認指示書にapproved_atがあります",
                path,
            )
        )
    tasks = plan.get("tasks") or []
    local_ids: set[str] = set()
    if isinstance(tasks, list):
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                issues.append(
                    _issue(
                        "INVALID_TASK",
                        "error",
                        "taskがJSONオブジェクトではありません",
                        f"{path}.tasks[{index}]",
                    )
                )
                continue
            task_id = task.get("id")
            if isinstance(task_id, str) and task_id:
                if task_id in local_ids:
                    issues.append(
                        _issue(
                            "DUPLICATE_TASK_ID",
                            "error",
                            f"指示書内でtask_idが重複しています: {task_id}",
                            f"{path}.tasks[{index}]",
                        )
                    )
                local_ids.add(task_id)
            if not str(task.get("task") or task.get("title") or "").strip():
                issues.append(
                    _issue(
                        "EMPTY_TASK_TITLE",
                        "error",
                        "タスク名が空です",
                        f"{path}.tasks[{index}]",
                    )
                )
            if not str(task.get("minimum_line") or "").strip():
                issues.append(
                    _issue(
                        "MISSING_MINIMUM_LINE",
                        "warning",
                        "最低限ラインがありません",
                        f"{path}.tasks[{index}]",
                    )
                )


def run_integrity_check(root: Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    parsed: dict[Path, dict[str, Any]] = {}
    data = root / "data"
    for path in sorted(data.rglob("*.json")) if data.is_dir() else []:
        relative = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
            if not raw:
                raise ValueError("空ファイルです")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSONオブジェクトではありません")
            parsed[path] = value
        except UnicodeDecodeError:
            issues.append(
                _issue("INVALID_UTF8", "critical", "UTF-8で読み込めません", relative)
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            issues.append(_issue("INVALID_JSON", "critical", str(exc), relative))
    for path in sorted(root.rglob("*.tmp")):
        if ".git" not in path.parts:
            issues.append(
                _issue(
                    "TEMP_FILE_REMAINS",
                    "warning",
                    "一時ファイルが残っています",
                    path.relative_to(root).as_posix(),
                )
            )
    if lock_path(root).exists() and not is_current_process_lock(root):
        issues.append(
            _issue(
                "STALE_OPERATION_LOCK"
                if is_stale_lock(root)
                else "ACTIVE_OPERATION_LOCK",
                "warning" if is_stale_lock(root) else "info",
                "操作ロックが残っています"
                if is_stale_lock(root)
                else "別の重要操作が実行中です",
                lock_path(root).relative_to(root).as_posix(),
            )
        )
    task_ids: dict[str, list[str]] = {}
    target_dates: dict[str, list[str]] = {}
    daily_dir = root / "data" / "daily"
    for path, entry in parsed.items():
        if path.parent != daily_dir:
            continue
        relative = path.relative_to(root).as_posix()
        day = entry.get("date")
        if not _valid_date(day):
            issues.append(
                _issue(
                    "INVALID_REVIEW_DATE",
                    "error",
                    "日次レビューの日付が不正です",
                    relative,
                )
            )
        elif day != path.stem:
            issues.append(
                _issue(
                    "REVIEW_DATE_MISMATCH",
                    "error",
                    "ファイル名とdateが一致しません",
                    relative,
                )
            )
        if not entry.get("raw_log"):
            issues.append(
                _issue(
                    "MISSING_RAW_LOG",
                    "warning",
                    "生ログがありません。内容は自動生成しません",
                    relative,
                )
            )
        if entry.get("created_at") and not _valid_iso(entry.get("created_at")):
            issues.append(
                _issue("INVALID_CREATED_AT", "error", "created_atが不正です", relative)
            )
        if not entry.get("updated_at") and entry.get("created_at"):
            issues.append(
                _issue(
                    "MISSING_UPDATED_AT",
                    "warning",
                    "updated_atがありません",
                    relative,
                    fixable=True,
                    fix={"operation": "copy_created_to_updated", "path": relative},
                )
            )
        elif entry.get("updated_at") and not _valid_iso(entry.get("updated_at")):
            issues.append(
                _issue("INVALID_UPDATED_AT", "error", "updated_atが不正です", relative)
            )
        for key in ("tomorrow_plan_proposal", "tomorrow_plan_final"):
            plan = entry.get(key)
            if not isinstance(plan, dict):
                continue
            plan_path = f"{relative}#{key}"
            _check_plan(plan, plan_path, issues, task_ids)
            target = plan.get("target_date")
            if key == "tomorrow_plan_final" and isinstance(target, str):
                target_dates.setdefault(target, []).append(plan_path)
    for target, locations in target_dates.items():
        if len(locations) > 1:
            issues.append(
                _issue(
                    "DUPLICATE_APPROVED_INSTRUCTION",
                    "critical",
                    f"{target}の承認済み指示書が複数あります",
                    ", ".join(locations),
                )
            )
    api_tasks_path = root / "data" / "api" / "tasks.json"
    api_tasks = parsed.get(api_tasks_path)
    if api_tasks:
        seen: set[str] = set()
        for index, task in enumerate(api_tasks.get("tasks") or []):
            location = f"data/api/tasks.json#tasks[{index}]"
            if not isinstance(task, dict):
                issues.append(
                    _issue("INVALID_TASK", "error", "API taskが不正です", location)
                )
                continue
            task_id = task.get("id")
            if not isinstance(task_id, str) or not task_id:
                issues.append(
                    _issue("MISSING_TASK_ID", "error", "task_idがありません", location)
                )
            elif task_id in seen:
                issues.append(
                    _issue(
                        "DUPLICATE_TASK_ID",
                        "critical",
                        f"task_idが重複しています: {task_id}",
                        location,
                    )
                )
            else:
                seen.add(task_id)
            if not str(task.get("title") or "").strip():
                issues.append(
                    _issue("EMPTY_TASK_TITLE", "error", "titleが空です", location)
                )
            status = task.get("status", "pending")
            if status not in VALID_TASK_STATUSES:
                issues.append(
                    _issue(
                        "INVALID_TASK_STATUS",
                        "error",
                        f"statusが不正です: {status}",
                        location,
                    )
                )
            if task.get("priority", "medium") not in VALID_PRIORITIES:
                issues.append(
                    _issue(
                        "INVALID_TASK_PRIORITY", "error", "priorityが不正です", location
                    )
                )
            due = task.get("due_date")
            if due and not _valid_date(due):
                issues.append(
                    _issue("INVALID_DUE_DATE", "error", "due_dateが不正です", location)
                )
            if status == "completed" and not _valid_iso(task.get("completed_at")):
                issues.append(
                    _issue(
                        "MISSING_COMPLETED_AT",
                        "error",
                        "completedなのにcompleted_atがありません",
                        location,
                    )
                )
            if status != "completed" and task.get("completed_at"):
                issues.append(
                    _issue(
                        "PENDING_HAS_COMPLETED_AT",
                        "error",
                        "未完了なのにcompleted_atがあります",
                        location,
                    )
                )
            count = task.get("rollover_count", 0)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                issues.append(
                    _issue(
                        "INVALID_ROLLOVER_COUNT",
                        "error",
                        "rollover_countが不正です",
                        location,
                        fixable=True,
                        fix={"operation": "reset_rollover_count", "task_id": task_id},
                    )
                )
            if due and not task.get("original_due_date"):
                issues.append(
                    _issue(
                        "MISSING_ORIGINAL_DUE_DATE",
                        "warning",
                        "元期限が未記録です",
                        location,
                        fixable=True,
                        fix={
                            "operation": "preserve_original_due_date",
                            "task_id": task_id,
                        },
                    )
                )
    idem_keys: dict[str, set[str]] = {}
    idem_paths: dict[str, list[str]] = {}
    for path, value in parsed.items():
        if "idempotency" not in path.parts:
            continue
        key = value.get("idempotency_key")
        request_hash = value.get("request_hash")
        if isinstance(key, str) and isinstance(request_hash, str):
            idem_keys.setdefault(key, set()).add(request_hash)
            idem_paths.setdefault(key, []).append(path.relative_to(root).as_posix())
    for key, hashes in idem_keys.items():
        if len(hashes) > 1:
            issues.append(
                _issue(
                    "IDEMPOTENCY_HASH_CONFLICT",
                    "critical",
                    f"同じidempotency keyに異なるhashがあります: {key}",
                    ", ".join(idem_paths[key]),
                )
            )
    confirmation_dirs = [
        root / "data" / "api" / "confirmations",
        root / "data" / "transactions" / "restore",
        root / "data" / "transactions" / "rollover",
    ]
    for directory in confirmation_dirs:
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            value = parsed.get(path)
            if value is None or not _valid_iso(value.get("expires_at")):
                issues.append(
                    _issue(
                        "INVALID_CONFIRMATION_RECORD",
                        "error",
                        "confirmationの有効期限が不正です",
                        path.relative_to(root).as_posix(),
                    )
                )
    notification_path = root / "data" / "notifications" / "history.json"
    notification = parsed.get(notification_path)
    if notification:
        seen_dedup: set[tuple[str, str, str]] = set()
        for index, record in enumerate(notification.get("records") or []):
            if not isinstance(record, dict):
                continue
            location = f"data/notifications/history.json#records[{index}]"
            status = record.get("status")
            if status == "sent" and not record.get("sent_at"):
                issues.append(
                    _issue(
                        "NOTIFICATION_SENT_AT_MISSING",
                        "error",
                        "送信済み通知にsent_atがありません",
                        location,
                    )
                )
            if status == "failed" and not record.get("error"):
                issues.append(
                    _issue(
                        "NOTIFICATION_ERROR_MISSING",
                        "warning",
                        "失敗通知にerrorがありません",
                        location,
                        fixable=True,
                        fix={"operation": "fill_notification_error", "index": index},
                    )
                )
            identity = (
                str(record.get("deduplication_key")),
                str(record.get("destination")),
                str(record.get("attempted_at")),
            )
            if identity in seen_dedup:
                issues.append(
                    _issue(
                        "DUPLICATE_NOTIFICATION",
                        "warning",
                        "同一通知履歴が重複しています",
                        location,
                    )
                )
            seen_dedup.add(identity)
    for item in list_backups(root):
        if not item.get("verified"):
            issues.append(
                _issue(
                    "BACKUP_HASH_MISMATCH",
                    "error",
                    str(item.get("error")),
                    str(item.get("path")),
                )
            )
    issues.sort(
        key=lambda item: (-SEVERITY_ORDER[item["severity"]], item["code"], item["path"])
    )
    counts = {
        severity: sum(item["severity"] == severity for item in issues)
        for severity in SEVERITY_ORDER
    }
    return {
        "status": "critical"
        if counts["critical"]
        else "error"
        if counts["error"]
        else "warning"
        if counts["warning"]
        else "ok",
        "root": str(root.resolve()),
        "issues": issues,
        "counts": counts,
        "checked_at": now_iso(),
    }


def preview_integrity_repair(root: Path) -> dict[str, Any]:
    report = run_integrity_check(root)
    fixes = [item for item in report["issues"] if item.get("fixable")]
    manual = [item for item in report["issues"] if not item.get("fixable")]
    return {
        "status": "repair_preview",
        "fixable": fixes,
        "manual_review": manual,
        "fix_count": len(fixes),
        "manual_count": len(manual),
        "backup_required": bool(fixes),
        "changes_applied": False,
        "checked_at": report["checked_at"],
    }


def apply_integrity_repair(
    root: Path, *, idempotency_key: str | None = None
) -> dict[str, Any]:
    idempotency_path = None
    request_hash = hashlib.sha256(b'{"operation":"integrity-repair"}').hexdigest()
    if idempotency_key:
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        idempotency_path = (
            root / "data" / "repairs" / "idempotency" / f"{key_hash}.json"
        )
        if idempotency_path.exists():
            stored = read_json_file(idempotency_path)
            if stored.get("request_hash") != request_hash:
                raise ValueError("同じ冪等性キーが異なる修復操作に使用されています")
            return {**stored["result"], "status": "idempotent_replay"}
    preview = preview_integrity_repair(root)
    if not preview["fixable"]:
        return {**preview, "status": "no_changes"}
    documents: dict[Path, dict[str, Any]] = {}
    applied = []
    for issue in preview["fixable"]:
        fix = issue["fix"]
        operation = fix["operation"]
        if operation in {"copy_created_to_updated", "move_main_overflow"}:
            relative = fix.get("path") or str(fix["plan_path"]).split("#", 1)[0]
            path = root / relative
            document = documents.setdefault(path, copy.deepcopy(read_json_file(path)))
            if operation == "copy_created_to_updated":
                document["updated_at"] = document["created_at"]
            else:
                key = str(fix["plan_path"]).split("#", 1)[1]
                plan = document[key]
                overflow = list(plan.get("main") or [])[3:]
                plan["main"] = list(plan.get("main") or [])[:3]
                plan["optional"] = list(plan.get("optional") or []) + overflow
        elif operation in {"reset_rollover_count", "preserve_original_due_date"}:
            path = root / "data" / "api" / "tasks.json"
            document = documents.setdefault(path, copy.deepcopy(read_json_file(path)))
            task = next(
                item for item in document["tasks"] if item.get("id") == fix["task_id"]
            )
            if operation == "reset_rollover_count":
                task["rollover_count"] = 0
            else:
                task["original_due_date"] = task.get("due_date")
        elif operation == "fill_notification_error":
            path = root / "data" / "notifications" / "history.json"
            document = documents.setdefault(path, copy.deepcopy(read_json_file(path)))
            document["records"][fix["index"]]["error"] = "未記録（整合性修復で補完）"
        else:
            continue
        applied.append(
            {"code": issue["code"], "path": issue["path"], "operation": operation}
        )
    repair_id = f"repair_{uuid.uuid4().hex[:12]}"
    with WorkspaceLock(root, "integrity-repair"):
        config = load_backup_config(root)
        if not config["enabled"] or not config["before_repair"]:
            raise ValueError("安全のためrepair前バックアップを無効化できません")
        backup_path, backup_manifest = create_backup(
            root,
            root / str(config["directory"]) / "automatic",
            manual=False,
            acquire_lock=False,
        )
        atomic_write_json_data_many(list(documents.items()))
        after = run_integrity_check(root)
        history_path = root / "data" / "repairs" / "history.json"
        history = (
            read_json_file(history_path)
            if history_path.exists()
            else {"version": "1", "records": []}
        )
        record = {
            "repair_id": repair_id,
            "started_at": preview.get("checked_at"),
            "completed_at": now_iso(),
            "issue_count": len(preview["fixable"]) + len(preview["manual_review"]),
            "fixed_count": len(applied),
            "skipped_count": len(preview["manual_review"]),
            "failed_count": 0,
            "backup_id": backup_manifest.get("backup_id"),
            "backup_path": str(backup_path),
            "applied_fixes": applied,
            "remaining_issues": after["issues"],
            "status": "completed",
        }
        history["records"].append(record)
        writes: list[tuple[Path, dict[str, Any]]] = [(history_path, history)]
        if idempotency_path is not None:
            writes.append(
                (
                    idempotency_path,
                    {
                        "version": "1",
                        "idempotency_key_hash": key_hash,
                        "request_hash": request_hash,
                        "result": record,
                        "created_at": now_iso(),
                    },
                )
            )
        atomic_write_json_data_many(writes)
    return record


def repair_history(root: Path) -> list[dict[str, Any]]:
    path = root / "data" / "repairs" / "history.json"
    if not path.exists():
        return []
    value = read_json_file(path)
    return list(reversed(value.get("records", []))) if isinstance(value, dict) else []
