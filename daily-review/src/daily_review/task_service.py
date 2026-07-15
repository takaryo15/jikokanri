"""Read-only normalization and filtering of tasks stored by older workflows."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from .date_utils import parse_date
from .storage import read_json_file


TASK_STATUSES = {
    "pending",
    "completed",
    "partial",
    "minimum_only",
    "not_started",
    "skipped",
}
PRIORITY_LABELS = {1: "high", 2: "medium"}


class TaskQueryError(ValueError):
    pass


def _text(value: Any) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


def _stable_id(*parts: str) -> str:
    value = "\x1f".join(parts)
    return "task-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _is_main(task: dict[str, Any], plan: dict[str, Any]) -> bool:
    main = {str(item).strip() for item in plan.get("main") or []}
    return (
        _text(task.get("area")).strip() in main
        or _text(task.get("task")).strip() in main
    )


def _priority(value: Any, *, is_main: bool) -> str:
    if isinstance(value, str) and value.lower() in {"high", "medium", "low"}:
        return value.lower()
    if isinstance(value, int) and not isinstance(value, bool):
        return PRIORITY_LABELS.get(value, "low")
    return "high" if is_main else "medium"


def _daily_tasks(root: Path) -> list[dict[str, Any]]:
    directory = root / "data" / "daily"
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        entry = read_json_file(path)
        if not isinstance(entry, dict):
            raise TaskQueryError(f"日次JSONの形式が不正です: {path.name}")
        source_day = _text(entry.get("date")) or path.stem
        plan = (
            entry.get("tomorrow_plan_final")
            or entry.get("tomorrow_plan_proposal")
            or {}
        )
        if not isinstance(plan, dict):
            continue
        result_map = {
            item.get("task_id"): item
            for item in entry.get("task_results") or []
            if isinstance(item, dict) and isinstance(item.get("task_id"), str)
        }
        for index, task in enumerate(plan.get("tasks") or [], start=1):
            if not isinstance(task, dict):
                continue
            raw_id = _text(task.get("id")) or _stable_id(
                source_day, str(index), _text(task.get("task"))
            )
            task_result = result_map.get(raw_id) or {}
            status = (
                _text(task_result.get("status"))
                or _text(task.get("status"))
                or "pending"
            )
            main = _is_main(task, plan)
            title = _text(task.get("task")) or _text(task.get("title")) or "未設定"
            minimum = _text(task.get("minimum_line"))
            result.append(
                {
                    "id": raw_id,
                    "short_id": raw_id[-8:],
                    "title": title,
                    "description": _text(task.get("description")),
                    "status": status,
                    "priority": _priority(task.get("priority"), is_main=main),
                    "category": _text(task.get("category")) or _text(task.get("area")),
                    "due_date": _text(task.get("due_date"))
                    or _text(plan.get("target_date")),
                    "is_main": main,
                    "is_minimum": bool(minimum.strip()),
                    "minimum_line": minimum,
                    "minimum_achieved": task_result.get("minimum_line_achieved"),
                    "source_review_date": source_day,
                    "created_at": _text(task.get("created_at"))
                    or _text(entry.get("created_at")),
                    "updated_at": _text(task_result.get("recorded_at"))
                    or _text(task.get("updated_at"))
                    or _text(entry.get("updated_at")),
                    "completed_at": _text(task_result.get("recorded_at"))
                    if status == "completed"
                    else "",
                    "source": "daily_instruction",
                }
            )
    return result


def _goal_plan_tasks(root: Path, known: set[tuple[str, str]]) -> list[dict[str, Any]]:
    directory = root / "data" / "plans" / "daily"
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        plan = read_json_file(path)
        if not isinstance(plan, dict):
            raise TaskQueryError(f"日次目標計画の形式が不正です: {path.name}")
        day = _text(plan.get("date")) or path.stem
        main_ids = {
            item.get("id")
            for item in plan.get("main_candidates") or []
            if isinstance(item, dict)
        }
        for item in (plan.get("main_candidates") or []) + (
            plan.get("other_tasks") or []
        ):
            if not isinstance(item, dict):
                continue
            title = _text(item.get("title")) or "未設定"
            if (day, title) in known:
                continue
            item_id = _text(item.get("id")) or _stable_id(
                day, title, _text(item.get("goal_id"))
            )
            raw_status = _text(item.get("status"))
            status = (
                "completed"
                if raw_status in {"done", "completed"}
                else "partial"
                if raw_status == "doing"
                else raw_status
                if raw_status
                in {"cancelled", "archived", "deleted", "blocked", "someday"}
                else "pending"
            )
            main = item_id in main_ids
            result.append(
                {
                    "id": item_id,
                    "short_id": item_id[-8:],
                    "title": title,
                    "description": _text(item.get("reason")),
                    "status": status,
                    "priority": _priority(item.get("priority"), is_main=main),
                    "category": _text(item.get("category")),
                    "due_date": _text(item.get("due_date")) or day,
                    "is_main": main,
                    "is_minimum": bool(_text(item.get("minimum")).strip()),
                    "minimum_line": _text(item.get("minimum")),
                    "minimum_achieved": None,
                    "source_review_date": day,
                    "created_at": _text(plan.get("created_at")),
                    "updated_at": _text(plan.get("updated_at")),
                    "completed_at": "",
                    "source": "goal_daily_plan",
                }
            )
    return result


def _api_tasks(root: Path) -> list[dict[str, Any]]:
    path = root / "data" / "api" / "tasks.json"
    if not path.exists():
        return []
    value = read_json_file(path)
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
        raise TaskQueryError("APIタスクJSONの形式が不正です")
    result = []
    for item in value["tasks"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise TaskQueryError("APIタスクの形式が不正です")
        task_id = item["id"]
        result.append(
            {
                "id": task_id,
                "short_id": task_id[-8:],
                "title": _text(item.get("title")) or "未設定",
                "description": _text(item.get("description")),
                "status": _text(item.get("status")) or "pending",
                "priority": _priority(item.get("priority"), is_main=bool(item.get("is_main"))),
                "category": _text(item.get("category")),
                "due_date": _text(item.get("due_date")),
                "is_main": bool(item.get("is_main")),
                "is_minimum": bool(_text(item.get("minimum_line")).strip()),
                "minimum_line": _text(item.get("minimum_line")),
                "minimum_achieved": item.get("minimum_achieved"),
                "source_review_date": _text(item.get("source_review_date")),
                "created_at": _text(item.get("created_at")),
                "updated_at": _text(item.get("updated_at")),
                "completed_at": _text(item.get("completed_at")),
                "source": "command_api",
            }
        )
    return result


def collect_tasks(root: Path) -> list[dict[str, Any]]:
    tasks = _daily_tasks(root)
    known = {(item["due_date"], item["title"]) for item in tasks}
    tasks.extend(_goal_plan_tasks(root, known))
    tasks.extend(_api_tasks(root))
    # Keep stable source identity.  The same legacy ID on different days is
    # valid, so only collapse exact source/date/ID duplicates.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in tasks:
        unique[(item["source"], item["source_review_date"], item["id"])] = item
    return list(unique.values())


def query_tasks(
    root: Path,
    *,
    today: str,
    status: str | None = None,
    priority: str | None = None,
    category: str | None = None,
    due: str | None = None,
    main_only: bool = False,
    minimum_only: bool = False,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    parse_date(today)
    if status is not None and status not in TASK_STATUSES:
        raise TaskQueryError(
            "statusはpending、completed、partial、minimum_only、not_started、skippedのいずれかにしてください"
        )
    if priority is not None and priority not in {"high", "medium", "low"}:
        raise TaskQueryError("priorityはhigh、medium、lowのいずれかにしてください")
    if due is not None and due not in {"today", "tomorrow", "overdue"}:
        raise TaskQueryError("dueはtoday、tomorrow、overdueのいずれかにしてください")
    tomorrow = (parse_date(today) + timedelta(days=1)).isoformat()
    values = collect_tasks(root)
    if status is None and not include_all:
        values = [item for item in values if item["status"] != "completed"]
    if status is not None:
        values = [item for item in values if item["status"] == status]
    if priority is not None:
        values = [item for item in values if item["priority"] == priority]
    if category is not None:
        values = [item for item in values if item["category"] == category]
    if due == "today":
        values = [item for item in values if item["due_date"] == today]
    elif due == "tomorrow":
        values = [item for item in values if item["due_date"] == tomorrow]
    elif due == "overdue":
        values = [
            item
            for item in values
            if item["due_date"]
            and item["due_date"] < today
            and item["status"] != "completed"
        ]
    if main_only:
        values = [item for item in values if item["is_main"]]
    if minimum_only:
        values = [item for item in values if item["is_minimum"]]

    values.sort(
        key=lambda item: (item["source"], item["source_review_date"], item["id"])
    )
    values.sort(key=lambda item: item["updated_at"], reverse=True)
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    values.sort(
        key=lambda item: (
            0
            if item["due_date"]
            and item["due_date"] < today
            and item["status"] != "completed"
            else 1,
            0 if item["due_date"] == today else 1,
            0 if item["is_main"] else 1,
            priority_rank[item["priority"]],
            item["due_date"] or "9999-12-31",
        )
    )
    return values


def task_fingerprint(task: dict[str, Any]) -> str:
    content = json.dumps(task, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
