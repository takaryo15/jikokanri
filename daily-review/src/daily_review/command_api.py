"""Unified, versioned application API for safe AI-driven operations."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .chat_workflow import load_priorities
from .command_models import (
    API_VERSION,
    ApiIssue,
    CommandRequest,
    CommandResponse,
)
from .date_utils import parse_date, tomorrow_of
from .markdown import render_daily
from .models import DailyEntry
from .quick_review import build_quick_entry
from .storage import atomic_write_json_data, atomic_write_text_many, read_json_file
from .task_service import collect_tasks


DEFAULT_API_CONFIG = {
    "confirmation_ttl_minutes": 30,
    "max_commands_per_request": 20,
    "max_raw_input_length": 20_000,
    "max_items_per_field": 100,
    "audit_log_enabled": True,
    "idempotency_retention_days": 90,
}
TOKEN_PATTERN = re.compile(r"^confirm_[a-f0-9]{32}$")


class CommandApiError(ValueError):
    pass


class CommandProblem(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
        recoverable: bool = True,
    ):
        self.issue = ApiIssue(
            code=code,
            message=message,
            field=field,
            details=details or {},
            recoverable=recoverable,
        )
        super().__init__(message)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_hash(request: CommandRequest) -> str:
    value = request.model_dump(
        mode="json", exclude={"request_id", "mode", "confirmation_token"}
    )
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Tokyo"))


def load_api_config(root: Path) -> dict[str, Any]:
    path = root / "config" / "api.json"
    if not path.exists():
        return dict(DEFAULT_API_CONFIG)
    value = read_json_file(path)
    if not isinstance(value, dict):
        raise CommandApiError("config/api.jsonはJSONオブジェクトにしてください")
    result = dict(DEFAULT_API_CONFIG)
    for key, setting in value.items():
        if key in result:
            result[key] = setting
    integer_keys = (
        "confirmation_ttl_minutes",
        "max_commands_per_request",
        "max_raw_input_length",
        "max_items_per_field",
        "idempotency_retention_days",
    )
    if any(
        not isinstance(result[key], int)
        or isinstance(result[key], bool)
        or result[key] <= 0
        for key in integer_keys
    ):
        raise CommandApiError("API設定の件数・時間上限は1以上の整数にしてください")
    if not isinstance(result["audit_log_enabled"], bool):
        raise CommandApiError("audit_log_enabledはtrueまたはfalseにしてください")
    return result


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _idempotency_path(root: Path, key: str) -> Path:
    name = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return root / "data" / "api" / "idempotency" / f"{name}.json"


def _confirmation_path(root: Path, token: str) -> Path:
    if not TOKEN_PATTERN.fullmatch(token):
        raise CommandProblem(
            "CONFIRMATION_INVALID", "confirmation tokenの形式が不正です"
        )
    return root / "data" / "api" / "confirmations" / f"{token}.json"


def _state_hash(root: Path) -> str:
    files: list[tuple[str, str]] = []
    candidates = [root / "data" / "api" / "tasks.json"]
    for directory in (
        root / "data" / "daily",
        root / "data" / "plans" / "daily",
        root / "data" / "weekly",
        root / "data" / "monthly",
        root / "data" / "scheduler",
    ):
        if directory.is_dir():
            candidates.extend(sorted(directory.glob("*.json")))
    for path in sorted(set(candidates)):
        if path.is_file():
            files.append(
                (
                    str(path.relative_to(root)),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return _content_hash(files)


def _entity_id(
    prefix: str, operation_hash: str, command_index: int, item_index: int = 0
) -> str:
    digest = hashlib.sha256(
        f"{operation_hash}:{command_index}:{item_index}".encode()
    ).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _read_document(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(default)
    value = read_json_file(path)
    if not isinstance(value, dict):
        raise CommandProblem(
            "STORAGE_ERROR",
            f"JSONオブジェクトではありません: {path.name}",
            recoverable=False,
        )
    return value


@dataclass
class WorkspaceState:
    root: Path
    operation_hash: str
    now: datetime
    daily: dict[str, dict[str, Any]] = field(default_factory=dict)
    api_tasks: dict[str, Any] = field(default_factory=dict)
    inbox: dict[str, dict[str, Any]] = field(default_factory=dict)
    dirty_daily: set[str] = field(default_factory=set)
    dirty_inbox: set[str] = field(default_factory=set)
    tasks_dirty: bool = False
    backups: dict[Path, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        directory = self.root / "data" / "daily"
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            value = read_json_file(path)
            if isinstance(value, dict):
                self.daily[str(value.get("date") or path.stem)] = value
        self.api_tasks = _read_document(
            self.root / "data" / "api" / "tasks.json", {"version": "1", "tasks": []}
        )
        if not isinstance(self.api_tasks.get("tasks"), list):
            raise CommandProblem(
                "STORAGE_ERROR", "APIタスクJSONが不正です", recoverable=False
            )

    def daily_for(self, day: str) -> dict[str, Any] | None:
        return self.daily.get(day)

    def inbox_for(self, day: str) -> dict[str, Any]:
        if day not in self.inbox:
            self.inbox[day] = _read_document(
                self.root / "data" / "inbox" / f"{day}.json",
                {"date": day, "entries": []},
            )
        if not isinstance(self.inbox[day].get("entries"), list):
            raise CommandProblem(
                "STORAGE_ERROR", "inbox JSONが不正です", recoverable=False
            )
        return self.inbox[day]

    def normalized_api_tasks(self) -> list[dict[str, Any]]:
        values = []
        for item in self.api_tasks["tasks"]:
            if not isinstance(item, dict):
                continue
            values.append(
                {
                    "id": item.get("id", ""),
                    "short_id": str(item.get("id", ""))[-8:],
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                    "status": item.get("status", "pending"),
                    "priority": item.get("priority", "medium"),
                    "category": item.get("category", ""),
                    "due_date": item.get("due_date", ""),
                    "is_main": bool(item.get("is_main")),
                    "is_minimum": bool(item.get("minimum_line")),
                    "minimum_line": item.get("minimum_line", ""),
                    "minimum_achieved": item.get("minimum_achieved"),
                    "source_review_date": item.get("source_review_date", ""),
                    "created_at": item.get("created_at", ""),
                    "updated_at": item.get("updated_at", ""),
                    "completed_at": item.get("completed_at", ""),
                    "source": "command_api",
                }
            )
        return values

    def all_tasks(self) -> list[dict[str, Any]]:
        disk = [
            item
            for item in collect_tasks(self.root)
            if item.get("source") != "command_api"
        ]
        return disk + self.normalized_api_tasks()

    def writes(self) -> list[tuple[Path, str]]:
        writes: list[tuple[Path, str]] = []
        for day in sorted(self.dirty_daily):
            entry = self.daily[day]
            DailyEntry.model_validate(entry)
            writes.append(
                (self.root / "data" / "daily" / f"{day}.json", _json_text(entry))
            )
            writes.append((self.root / "logs" / f"{day}.md", render_daily(entry)))
        for day in sorted(self.dirty_inbox):
            writes.append(
                (
                    self.root / "data" / "inbox" / f"{day}.json",
                    _json_text(self.inbox[day]),
                )
            )
        if self.tasks_dirty:
            writes.append(
                (self.root / "data" / "api" / "tasks.json", _json_text(self.api_tasks))
            )
        writes.extend(
            (path, _json_text(value))
            for path, value in sorted(
                self.backups.items(), key=lambda pair: str(pair[0])
            )
        )
        return writes


@dataclass
class ExecutionPlan:
    state: WorkspaceState
    changes: list[dict[str, Any]] = field(default_factory=list)
    command_results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[ApiIssue] = field(default_factory=list)
    errors: list[ApiIssue] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    external_actions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_writes(self) -> bool:
        return bool(self.state.writes() or self.external_actions)


def _resolve_references(value: Any, previous: list[dict[str, Any]]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_references(item, previous) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_references(item, previous) for item in value]
    if isinstance(value, str) and value.startswith("$commands."):
        parts = value.split(".")
        if len(parts) < 4 or parts[2] != "result":
            raise CommandProblem("INVALID_PAYLOAD", f"不正な結果参照です: {value}")
        try:
            current: Any = previous[int(parts[1])]["result"]
            for key in parts[3:]:
                current = current[key]
            return current
        except (IndexError, KeyError, ValueError, TypeError) as exc:
            raise CommandProblem(
                "INVALID_PAYLOAD", f"結果参照を解決できません: {value}"
            ) from exc
    return value


def _find_task(
    state: WorkspaceState, *, task_id: str | None, title: str | None
) -> dict[str, Any]:
    tasks = state.all_tasks()
    if task_id:
        candidates = [item for item in tasks if item["id"] == task_id]
    else:
        query = (title or "").casefold()
        exact = [item for item in tasks if item["title"].casefold() == query]
        candidates = exact or [
            item
            for item in tasks
            if query in item["title"].casefold() or query in item["category"].casefold()
        ]
    details = {
        "candidates": [
            {
                "task_id": item["id"],
                "title": item["title"],
                "due_date": item["due_date"],
                "status": item["status"],
            }
            for item in candidates[:20]
        ]
    }
    if not candidates:
        raise CommandProblem(
            "TASK_NOT_FOUND", "対象タスクが見つかりません", details=details
        )
    if len(candidates) != 1:
        raise CommandProblem(
            "TASK_AMBIGUOUS", "対象タスクを一意に特定できません", details=details
        )
    return candidates[0]


def _mutate_task(
    state: WorkspaceState, task: dict[str, Any], changes: dict[str, Any]
) -> None:
    if task["source"] == "command_api":
        target = next(
            item for item in state.api_tasks["tasks"] if item.get("id") == task["id"]
        )
        target.update(changes)
        target["updated_at"] = state.now.isoformat(timespec="seconds")
        state.tasks_dirty = True
        return
    if task["source"] == "daily_instruction":
        entry = state.daily.get(task["source_review_date"])
        if not entry:
            raise CommandProblem(
                "TASK_NOT_FOUND", "タスクの元日次データが見つかりません"
            )
        plan = (
            entry.get("tomorrow_plan_final")
            or entry.get("tomorrow_plan_proposal")
            or {}
        )
        target = next(
            (item for item in plan.get("tasks") or [] if item.get("id") == task["id"]),
            None,
        )
        if target is None:
            raise CommandProblem("TASK_NOT_FOUND", "タスクの元データが見つかりません")
        mapped = dict(changes)
        if "title" in mapped:
            mapped["task"] = mapped.pop("title")
        if "priority" in mapped:
            mapped["priority"] = {"high": 1, "medium": 2, "low": 3}[mapped["priority"]]
        target.update(mapped)
        entry["updated_at"] = state.now.isoformat(timespec="seconds")
        state.dirty_daily.add(task["source_review_date"])
        return
    raise CommandProblem(
        "INVALID_PAYLOAD", "目標計画由来タスクはこのAPIでは更新できません"
    )


def _complete_task(
    state: WorkspaceState, task: dict[str, Any], completed_at: str
) -> str:
    if task["status"] == "completed":
        return "already_completed"
    if task["source"] == "daily_instruction":
        entry = state.daily[task["source_review_date"]]
        results = [
            item
            for item in entry.get("task_results") or []
            if item.get("task_id") != task["id"]
        ]
        results.append(
            {
                "task_id": task["id"],
                "status": "completed",
                "minimum_line_achieved": True,
                "recorded_at": completed_at,
            }
        )
        entry["task_results"] = results
        entry["updated_at"] = completed_at
        state.dirty_daily.add(task["source_review_date"])
    else:
        _mutate_task(
            state,
            task,
            {
                "status": "completed",
                "completed_at": completed_at,
                "minimum_achieved": True,
            },
        )
    return "completed"


def _instruction_identity(day: str, key: str, plan: dict[str, Any]) -> str:
    if isinstance(plan.get("id"), str):
        return plan["id"]
    return (
        "instruction-"
        + hashlib.sha256(f"{day}:{key}:{plan.get('target_date')}".encode()).hexdigest()[
            :12
        ]
    )


def _find_instruction(
    state: WorkspaceState, instruction_id: str
) -> tuple[str, str, dict[str, Any]]:
    matches = []
    for day, entry in state.daily.items():
        for key in ("tomorrow_plan_proposal", "tomorrow_plan_final"):
            plan = entry.get(key)
            if (
                isinstance(plan, dict)
                and _instruction_identity(day, key, plan) == instruction_id
            ):
                matches.append((day, key, plan))
    if not matches:
        raise CommandProblem("INSTRUCTION_NOT_FOUND", "指示書が見つかりません")
    if len(matches) > 1:
        finals = [item for item in matches if item[1] == "tomorrow_plan_final"]
        if len(finals) == 1:
            return finals[0]
        raise CommandProblem("INVALID_PAYLOAD", "指示書IDが一意ではありません")
    return matches[0]


def _handle_command(
    plan: ExecutionPlan, command: dict[str, Any], index: int, request: CommandRequest
) -> dict[str, Any]:
    state = plan.state
    kind = command["type"]
    payload = command["payload"]
    timestamp = state.now.isoformat(timespec="seconds")
    if kind == "create_daily_review":
        day = payload.get("date") or request.effective_date
        try:
            parse_date(day)
        except ValueError as exc:
            raise CommandProblem(
                "INVALID_DATE", "日付はYYYY-MM-DD形式にしてください"
            ) from exc
        existing = state.daily_for(day)
        if existing and not payload.get("replace"):
            raise CommandProblem(
                "DUPLICATE_REVIEW", f"{day}の日次レビューは既に存在します"
            )
        quick_payload = {
            key: payload.get(key, [] if key != "journal" else "")
            for key in ("done", "not_done", "causes", "tomorrow", "minimum", "journal")
        }
        if (
            payload.get("unclassified")
            and not any(
                quick_payload[key]
                for key in ("done", "not_done", "causes", "tomorrow", "minimum")
            )
            and not quick_payload["journal"]
        ):
            quick_payload["journal"] = request.raw_input or "\n".join(
                payload["unclassified"]
            )
        entry = build_quick_entry(day, quick_payload, existing)
        for task_index, task in enumerate(entry["tomorrow_plan_proposal"]["tasks"]):
            task["id"] = _entity_id("task", state.operation_hash, index, task_index)
        instruction_id = _entity_id("instruction", state.operation_hash, index)
        entry["tomorrow_plan_proposal"]["id"] = instruction_id
        entry["raw_log"] = request.raw_input or entry["raw_log"]
        entry["api_review"] = {
            "unclassified": payload.get("unclassified", []),
            "request_id": request.request_id,
        }
        entry["updated_at"] = timestamp
        if existing:
            backup = (
                state.root
                / "data"
                / "backups"
                / "daily"
                / f"{day}_api_{state.operation_hash[:12]}.json"
            )
            state.backups[backup] = copy.deepcopy(existing)
        state.daily[day] = entry
        state.dirty_daily.add(day)
        inbox = state.inbox_for(day)
        input_id = _entity_id("api-input", state.operation_hash, index)
        inbox["entries"].append(
            {
                "id": input_id,
                "created_at": timestamp,
                "source": "command_api",
                "raw_text": request.raw_input or _canonical(payload),
                "request_id": request.request_id,
            }
        )
        state.dirty_inbox.add(day)
        plan.entity_ids.extend([input_id, instruction_id])
        plan.changes.append(
            {
                "command_index": index,
                "type": kind,
                "action": "create" if not existing else "replace",
                "entity": f"daily:{day}",
                "main": entry["tomorrow_plan_proposal"]["main"],
                "backlog": entry["quick_review"]["backlog_candidates"],
            }
        )
        return {
            "review_id": f"daily:{day}",
            "input_id": input_id,
            "instruction_id": instruction_id,
            "main": entry["tomorrow_plan_proposal"]["main"],
        }
    if kind == "create_task":
        if payload.get("due_date"):
            try:
                parse_date(payload["due_date"])
            except ValueError as exc:
                raise CommandProblem(
                    "INVALID_DATE", "due_dateはYYYY-MM-DD形式にしてください"
                ) from exc
        task_id = _entity_id("task", state.operation_hash, index)
        minimum = payload.get("minimum_action") or "着手する"
        if not payload.get("minimum_action"):
            plan.warnings.append(
                ApiIssue(
                    code="MINIMUM_SUGGESTED",
                    message="最低限がないため「着手する」を提案しました",
                    field=f"commands[{index}].payload.minimum_action",
                )
            )
        task = {
            "id": task_id,
            "title": payload["title"],
            "description": payload.get("description", ""),
            "category": payload.get("category", ""),
            "priority": payload.get("priority", "medium"),
            "due_date": payload.get("due_date"),
            "is_main": bool(payload.get("is_main_candidate")),
            "minimum_line": minimum,
            "status": "pending",
            "created_at": timestamp,
            "updated_at": timestamp,
            "completed_at": None,
            "source_review_date": request.effective_date,
        }
        state.api_tasks["tasks"].append(task)
        state.tasks_dirty = True
        plan.entity_ids.append(task_id)
        plan.changes.append(
            {
                "command_index": index,
                "type": kind,
                "action": "create",
                "entity": task_id,
                "title": task["title"],
            }
        )
        return {"task_id": task_id, "task": task}
    if kind in {"complete_task", "reschedule_task", "update_task"}:
        task = _find_task(
            state, task_id=payload.get("task_id"), title=payload.get("title")
        )
        if kind == "complete_task":
            completed_at = payload.get("completed_at") or timestamp
            try:
                datetime.fromisoformat(completed_at)
            except ValueError as exc:
                raise CommandProblem(
                    "INVALID_DATE", "completed_atはISO 8601形式にしてください"
                ) from exc
            status = _complete_task(state, task, completed_at)
            if status == "already_completed":
                plan.warnings.append(
                    ApiIssue(
                        code="TASK_ALREADY_COMPLETED",
                        message="タスクは既に完了しています",
                        details={"task_id": task["id"]},
                    )
                )
            plan.changes.append(
                {
                    "command_index": index,
                    "type": kind,
                    "action": status,
                    "entity": task["id"],
                }
            )
            return {"task_id": task["id"], "status": status}
        if kind == "reschedule_task":
            try:
                parse_date(payload["new_due_date"])
            except ValueError as exc:
                raise CommandProblem(
                    "INVALID_DATE", "new_due_dateはYYYY-MM-DD形式にしてください"
                ) from exc
            _mutate_task(
                state,
                task,
                {
                    "due_date": payload["new_due_date"],
                    "reschedule_reason": payload.get("reason"),
                },
            )
            plan.changes.append(
                {
                    "command_index": index,
                    "type": kind,
                    "action": "reschedule",
                    "entity": task["id"],
                    "new_due_date": payload["new_due_date"],
                }
            )
            return {"task_id": task["id"], "new_due_date": payload["new_due_date"]}
        changes = {
            key: value
            for key, value in {
                "title": payload.get("new_title"),
                "priority": payload.get("priority"),
                "category": payload.get("category"),
                "description": payload.get("description"),
            }.items()
            if value is not None
        }
        _mutate_task(state, task, changes)
        plan.changes.append(
            {
                "command_index": index,
                "type": kind,
                "action": "update",
                "entity": task["id"],
                "fields": sorted(changes),
            }
        )
        return {"task_id": task["id"], "updated_fields": sorted(changes)}
    if kind == "list_tasks":
        values = state.all_tasks()
        p = payload
        if p.get("status"):
            values = [item for item in values if item["status"] == p["status"]]
        elif not p.get("all"):
            values = [item for item in values if item["status"] != "completed"]
        if p.get("priority"):
            values = [item for item in values if item["priority"] == p["priority"]]
        if p.get("category") is not None:
            values = [item for item in values if item["category"] == p["category"]]
        target = request.effective_date
        if p.get("due") == "today":
            values = [item for item in values if item["due_date"] == target]
        elif p.get("due") == "tomorrow":
            values = [
                item for item in values if item["due_date"] == tomorrow_of(target)
            ]
        elif p.get("due") == "overdue":
            values = [
                item
                for item in values
                if item["due_date"]
                and item["due_date"] < target
                and item["status"] != "completed"
            ]
        if p.get("main"):
            values = [item for item in values if item["is_main"]]
        if p.get("minimum"):
            values = [item for item in values if item["is_minimum"]]
        values.sort(key=lambda item: (item.get("due_date") or "9999-12-31", item["id"]))
        return {"tasks": values, "count": len(values)}
    if kind == "generate_instruction":
        target = payload["target_date"]
        try:
            source_day = (parse_date(target) - timedelta(days=1)).isoformat()
        except ValueError as exc:
            raise CommandProblem(
                "INVALID_DATE", "target_dateはYYYY-MM-DD形式にしてください"
            ) from exc
        entry = copy.deepcopy(
            state.daily.get(source_day)
            or {"date": source_day, "created_at": timestamp, "updated_at": timestamp}
        )
        existing = entry.get("tomorrow_plan_proposal")
        if isinstance(existing, dict):
            instruction_id = _instruction_identity(
                source_day, "tomorrow_plan_proposal", existing
            )
            return {
                "instruction_id": instruction_id,
                "instruction": existing,
                "existing": True,
            }
        candidates = [
            item
            for item in state.all_tasks()
            if item["status"] != "completed"
            and (not item["due_date"] or item["due_date"] <= target)
        ]
        try:
            priorities = load_priorities(state.root)
        except (OSError, ValueError):
            priorities = []
        rank = {name: index for index, name in enumerate(priorities)}
        candidates.sort(
            key=lambda item: (
                0 if item["is_main"] else 1,
                {"high": 0, "medium": 1, "low": 2}.get(item["priority"], 3),
                0 if item["due_date"] and item["due_date"] < target else 1,
                rank.get(item["category"], len(rank)),
                item["due_date"] or "9999-12-31",
                item["id"],
            )
        )
        selected = candidates[:3]
        instruction_id = _entity_id("instruction", state.operation_hash, index)
        instruction = {
            "id": instruction_id,
            "status": "pending_review",
            "target_date": target,
            "main": [item["title"] for item in selected],
            "tasks": [
                {
                    "id": item["id"],
                    "area": item["category"] or item["title"],
                    "task": item["title"],
                    "priority": {"high": 1, "medium": 2, "low": 3}.get(
                        item["priority"], 2
                    ),
                    "minimum_line": item["minimum_line"] or "着手する",
                }
                for item in candidates
            ],
            "optional": [item["title"] for item in candidates[3:]],
            "one_change_tomorrow": selected[0]["minimum_line"]
            if selected
            else "最低限を実行する",
            "approved_at": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        entry["tomorrow_plan_proposal"] = instruction
        entry["updated_at"] = timestamp
        state.daily[source_day] = entry
        state.dirty_daily.add(source_day)
        plan.entity_ids.append(instruction_id)
        plan.changes.append(
            {
                "command_index": index,
                "type": kind,
                "action": "create",
                "entity": instruction_id,
                "main": instruction["main"],
                "optional": instruction["optional"],
            }
        )
        reasons = [
            {
                "title": item["title"],
                "selected_as_main": item in selected,
                "reasons": (["Main候補として明示"] if item["is_main"] else [])
                + [f"優先度{item['priority']}"]
                + (
                    ["期限超過または対象日まで"]
                    if item["due_date"] and item["due_date"] <= target
                    else []
                ),
            }
            for item in candidates
        ]
        return {
            "instruction_id": instruction_id,
            "instruction": instruction,
            "selection": reasons,
        }
    if kind == "get_instruction":
        target = payload["target_date"]
        matches = []
        for day, entry in state.daily.items():
            for key in ("tomorrow_plan_final", "tomorrow_plan_proposal"):
                value = entry.get(key)
                if isinstance(value, dict) and value.get("target_date") == target:
                    matches.append(
                        {
                            "instruction_id": _instruction_identity(day, key, value),
                            "kind": key,
                            "instruction": value,
                        }
                    )
        if not matches:
            raise CommandProblem(
                "INSTRUCTION_NOT_FOUND", "対象日の指示書が見つかりません"
            )
        matches.sort(key=lambda item: 0 if item["kind"] == "tomorrow_plan_final" else 1)
        return matches[0]
    if kind in {"approve_instruction", "revise_instruction"}:
        day, key, instruction = _find_instruction(state, payload["instruction_id"])
        entry = state.daily[day]
        if kind == "approve_instruction":
            if key == "tomorrow_plan_final" or isinstance(
                entry.get("tomorrow_plan_final"), dict
            ):
                raise CommandProblem(
                    "INSTRUCTION_ALREADY_APPROVED", "指示書は既に承認済みです"
                )
            final = copy.deepcopy(instruction)
            final.update(
                {
                    "id": payload["instruction_id"],
                    "status": "approved",
                    "approved_at": timestamp,
                    "updated_at": timestamp,
                }
            )
            entry["tomorrow_plan_final"] = final
            entry["updated_at"] = timestamp
            state.dirty_daily.add(day)
            plan.changes.append(
                {
                    "command_index": index,
                    "type": kind,
                    "action": "approve",
                    "entity": payload["instruction_id"],
                }
            )
            return {
                "instruction_id": payload["instruction_id"],
                "status": "approved",
                "instruction": final,
            }
        if key == "tomorrow_plan_final":
            raise CommandProblem(
                "INSTRUCTION_ALREADY_APPROVED", "承認済み指示書はAPIから修正できません"
            )
        requested_main = payload.get("main", [])
        main = requested_main[:3]
        optional = list(payload.get("optional", [])) + requested_main[3:]
        if len(requested_main) > 3:
            plan.warnings.append(
                ApiIssue(
                    code="MAIN_LIMIT_EXCEEDED",
                    message="4件目以降のMainをoptionalへ退避しました",
                    details={"moved": requested_main[3:]},
                )
            )
        minimum = payload.get("minimum", [])
        tasks = []
        for task_index, title in enumerate(main + optional):
            tasks.append(
                {
                    "id": _entity_id("task", state.operation_hash, index, task_index),
                    "area": title,
                    "task": title,
                    "priority": min(task_index + 1, 3),
                    "minimum_line": minimum[task_index]
                    if task_index < len(minimum)
                    else minimum[0]
                    if minimum
                    else "着手する",
                }
            )
        instruction.update(
            {
                "id": payload["instruction_id"],
                "main": main,
                "optional": optional,
                "minimum_candidates": minimum,
                "tasks": tasks,
                "updated_at": timestamp,
            }
        )
        entry["updated_at"] = timestamp
        state.dirty_daily.add(day)
        plan.changes.append(
            {
                "command_index": index,
                "type": kind,
                "action": "revise",
                "entity": payload["instruction_id"],
                "main": main,
                "optional": optional,
            }
        )
        return {"instruction_id": payload["instruction_id"], "instruction": instruction}
    if kind.startswith("scheduler_") or kind.startswith("run_") and kind.endswith(
        "_flow"
    ):
        from .operational_flows import run_operational_flow
        from .scheduler import (
            due_jobs,
            parse_scheduler_at,
            run_due_jobs,
            run_scheduled_job,
            scheduler_history,
            scheduler_status,
        )

        at = (
            parse_scheduler_at(payload.get("at"), request.timezone)
            if payload.get("at")
            else state.now.astimezone(ZoneInfo(request.timezone))
        )
        if kind == "scheduler_status":
            return scheduler_status(state.root, at)
        if kind == "scheduler_due":
            return due_jobs(state.root, at)
        if kind == "scheduler_history":
            return {
                "records": scheduler_history(
                    state.root,
                    job_id=payload.get("job"),
                    status=payload.get("status"),
                    day=payload.get("date"),
                )
            }
        if kind == "scheduler_run_due":
            preview = run_due_jobs(state.root, at, dry_run=True, source="command_api")
            action = {"kind": kind, "index": index, "payload": payload, "at": at.isoformat()}
        elif kind == "scheduler_run_job":
            preview = run_scheduled_job(
                state.root,
                payload["job"],
                current=at,
                dry_run=True,
                force=payload.get("force", False),
                source="command_api",
            )
            action = {"kind": kind, "index": index, "payload": payload, "at": at.isoformat()}
        else:
            flow = kind.removeprefix("run_").removesuffix("_flow")
            preview = run_operational_flow(
                state.root,
                flow,
                day=payload.get("date"),
                month=payload.get("month"),
                current=at,
                dry_run=True,
                force=payload.get("force", False),
                source="command_api",
            )
            action = {
                "kind": kind,
                "flow": flow,
                "index": index,
                "payload": payload,
                "at": at.isoformat(),
            }
        plan.external_actions.append(action)
        plan.changes.append(
            {
                "command_index": index,
                "type": kind,
                "action": "execute_after_confirmation",
                "preview": preview,
            }
        )
        return preview
    raise CommandProblem("UNKNOWN_COMMAND", f"未対応のcommandです: {kind}")


def _execute_external_actions(
    root: Path, plan: ExecutionPlan
) -> None:
    from .operational_flows import run_operational_flow
    from .scheduler import run_due_jobs, run_scheduled_job

    for action in plan.external_actions:
        current = datetime.fromisoformat(action["at"])
        payload = action["payload"]
        if action["kind"] == "scheduler_run_due":
            result = run_due_jobs(root, current, source="command_api")
        elif action["kind"] == "scheduler_run_job":
            result = run_scheduled_job(
                root,
                payload["job"],
                current=current,
                force=payload.get("force", False),
                source="command_api",
            )
        else:
            result = run_operational_flow(
                root,
                action["flow"],
                day=payload.get("date"),
                month=payload.get("month"),
                current=current,
                force=payload.get("force", False),
                source="command_api",
            )
        plan.command_results[action["index"]]["result"] = result


def _build_plan(
    root: Path, request: CommandRequest, operation_hash: str, current: datetime
) -> ExecutionPlan:
    state = WorkspaceState(root, operation_hash, current)
    plan = ExecutionPlan(state)
    for index, model in enumerate(request.commands):
        raw = model.model_dump(mode="json")
        try:
            resolved = _resolve_references(raw, plan.command_results)
            result = _handle_command(plan, resolved, index, request)
            plan.command_results.append(
                {
                    "index": index,
                    "type": raw["type"],
                    "status": "success",
                    "result": result,
                }
            )
        except CommandProblem as exc:
            issue = exc.issue.model_copy(
                update={"field": exc.issue.field or f"commands[{index}]"}
            )
            plan.errors.append(issue)
            plan.command_results.append(
                {
                    "index": index,
                    "type": raw["type"],
                    "status": "error",
                    "error": issue.model_dump(mode="json"),
                }
            )
            if request.execution_policy == "atomic":
                break
    if plan.external_actions and (
        len(request.commands) != 1 or bool(plan.state.writes())
    ):
        plan.errors.append(
            ApiIssue(
                code="INVALID_REQUEST",
                message="schedulerまたはflowの実行commandは安全のため1件だけ指定してください",
                field="commands",
                recoverable=True,
            )
        )
    return plan


class CommandExecutor:
    def __init__(self, root: Path, *, clock: Callable[[], datetime] = _now):
        self.root = root
        self.clock = clock

    def _response(self, request: CommandRequest, **values: Any) -> CommandResponse:
        return CommandResponse(
            request_id=request.request_id,
            mode=request.mode,
            idempotency_key=request.idempotency_key,
            metadata={
                "effective_date": request.effective_date,
                "timezone": request.timezone,
            },
            **values,
        )

    def _save_audit(
        self,
        request: CommandRequest,
        operation_hash: str,
        response: CommandResponse,
        current: datetime,
        token: str | None = None,
    ) -> None:
        try:
            if not load_api_config(self.root)["audit_log_enabled"]:
                return
            audit_id = (
                f"audit-{current.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(6)}"
            )
            path = self.root / "data" / "api" / "audit" / f"{audit_id}.json"
            record = {
                "audit_id": audit_id,
                "request_id": request.request_id,
                "idempotency_key": request.idempotency_key,
                "source": request.source,
                "mode": request.mode,
                "request_hash": operation_hash,
                "raw_input_hash": hashlib.sha256(
                    (request.raw_input or "").encode()
                ).hexdigest()
                if request.raw_input is not None
                else None,
                "command_types": [command.type for command in request.commands],
                "effective_date": request.effective_date,
                "executed_at": current.isoformat(timespec="seconds"),
                "status": response.status,
                "entity_ids": response.result.get("entity_ids", []),
                "confirmation_token_id": token,
                "warning_count": len(response.warnings),
                "error_count": len(response.errors),
            }
            atomic_write_json_data(path, record)
        except (OSError, ValueError):
            # Audit failure must never corrupt or roll back an already committed domain change.
            return

    def execute(self, payload: dict[str, Any] | CommandRequest) -> CommandResponse:
        current = self.clock()
        if (
            isinstance(payload, dict)
            and payload.get("version", API_VERSION) != API_VERSION
        ):
            request_id = str(payload.get("request_id") or "unknown")
            return CommandResponse(
                request_id=request_id,
                status="input_error",
                mode="preview",
                summary="未対応のAPI versionです",
                errors=[
                    ApiIssue(
                        code="UNSUPPORTED_VERSION",
                        message="versionは1を指定してください",
                        field="version",
                    )
                ],
                metadata={},
            )
        try:
            request = (
                payload
                if isinstance(payload, CommandRequest)
                else CommandRequest.model_validate(payload)
            )
            config = load_api_config(self.root)
            if len(request.commands) > config["max_commands_per_request"]:
                raise CommandProblem(
                    "INVALID_REQUEST",
                    "commandsが設定上限を超えています",
                    field="commands",
                )
            if (
                request.raw_input is not None
                and len(request.raw_input) > config["max_raw_input_length"]
            ):
                raise CommandProblem(
                    "INVALID_REQUEST",
                    "raw_inputが設定上限を超えています",
                    field="raw_input",
                )
        except ValidationError as exc:
            request_id = str(
                payload.get("request_id") if isinstance(payload, dict) else "unknown"
            )
            errors = []
            for item in exc.errors(include_url=False):
                location = ".".join(str(part) for part in item["loc"])
                code = (
                    "UNKNOWN_COMMAND"
                    if item["type"] == "union_tag_invalid"
                    else "INVALID_DATE"
                    if location == "effective_date"
                    else "INVALID_PAYLOAD"
                    if location.startswith("commands.")
                    else "INVALID_REQUEST"
                )
                errors.append(ApiIssue(code=code, message=item["msg"], field=location))
            return CommandResponse(
                request_id=request_id,
                status="input_error",
                mode=str(payload.get("mode", "preview"))
                if isinstance(payload, dict)
                and payload.get("mode") in {"preview", "commit"}
                else "preview",
                summary="リクエストが不正です",
                errors=errors,
                metadata={},
            )
        except (CommandProblem, CommandApiError) as exc:
            issue = (
                exc.issue
                if isinstance(exc, CommandProblem)
                else ApiIssue(code="INVALID_REQUEST", message=str(exc))
            )
            request = (
                payload
                if isinstance(payload, CommandRequest)
                else CommandRequest.model_construct(
                    request_id=str(payload.get("request_id", "unknown")),
                    mode=payload.get("mode", "preview"),
                    idempotency_key=payload.get("idempotency_key"),
                    effective_date=str(payload.get("effective_date", "")),
                    timezone=str(payload.get("timezone", "Asia/Tokyo")),
                    source=str(payload.get("source", "manual")),
                    raw_input=payload.get("raw_input"),
                    commands=[],
                )
            )
            return self._response(
                request, status="input_error", summary=issue.message, errors=[issue]
            )

        operation_hash = request_hash(request)
        idem_path = (
            _idempotency_path(self.root, request.idempotency_key)
            if request.idempotency_key
            else None
        )
        try:
            existing_idem = (
                _read_document(idem_path, {})
                if idem_path and idem_path.exists()
                else None
            )
        except (CommandProblem, OSError, ValueError) as exc:
            issue = (
                exc.issue
                if isinstance(exc, CommandProblem)
                else ApiIssue(code="STORAGE_ERROR", message=str(exc), recoverable=False)
            )
            return self._response(
                request, status="storage_error", summary=issue.message, errors=[issue]
            )
        if existing_idem:
            if existing_idem.get("request_hash") != operation_hash:
                response = self._response(
                    request,
                    status="conflict",
                    summary="idempotency keyが異なる内容で使用されています",
                    errors=[
                        ApiIssue(
                            code="IDEMPOTENCY_CONFLICT",
                            message="同じidempotency keyで異なるリクエストは実行できません",
                            field="idempotency_key",
                        )
                    ],
                )
                self._save_audit(request, operation_hash, response, current)
                return response
            if existing_idem.get("status") == "committed":
                stored = existing_idem.get("response") or {}
                response = self._response(
                    request,
                    status="idempotent_replay",
                    summary="既に確定済みの結果を返しました",
                    result=stored.get("result", {}),
                    changes=stored.get("changes", []),
                    warnings=[
                        ApiIssue.model_validate(item)
                        for item in stored.get("warnings", [])
                    ],
                )
                self._save_audit(request, operation_hash, response, current)
                return response
            if request.mode == "preview" and existing_idem.get("status") == "preview":
                old_token = existing_idem.get("confirmation_token")
                try:
                    old_confirmation = read_json_file(
                        _confirmation_path(self.root, old_token)
                    )
                    reusable = (
                        current < datetime.fromisoformat(old_confirmation["expires_at"])
                        and old_confirmation.get("state_hash") == _state_hash(self.root)
                        and not old_confirmation.get("used_at")
                    )
                except (CommandProblem, OSError, KeyError, TypeError, ValueError):
                    reusable = False
                if reusable:
                    stored = existing_idem.get("response") or {}
                    response = self._response(
                        request,
                        status="preview_replay",
                        summary="同じpreview結果を返しました",
                        changes=stored.get("changes", []),
                        warnings=[
                            ApiIssue.model_validate(item)
                            for item in stored.get("warnings", [])
                        ],
                        confirmation_required=True,
                        confirmation_token=old_token,
                        result=stored.get("result", {}),
                    )
                    self._save_audit(
                        request,
                        operation_hash,
                        response,
                        current,
                        response.confirmation_token,
                    )
                    return response

        try:
            plan = _build_plan(self.root, request, operation_hash, current)
        except (CommandProblem, OSError, ValueError) as exc:
            issue = (
                exc.issue
                if isinstance(exc, CommandProblem)
                else ApiIssue(code="STORAGE_ERROR", message=str(exc), recoverable=False)
            )
            response = self._response(
                request, status="error", summary=issue.message, errors=[issue]
            )
            self._save_audit(request, operation_hash, response, current)
            return response
        if plan.errors and request.execution_policy == "atomic":
            response = self._response(
                request,
                status="needs_clarification"
                if any(
                    item.code in {"TASK_AMBIGUOUS", "TASK_NOT_FOUND"}
                    for item in plan.errors
                )
                else "error",
                summary="commandを安全に実行できません",
                changes=[],
                warnings=plan.warnings,
                errors=plan.errors,
                result={"commands": plan.command_results},
            )
            self._save_audit(request, operation_hash, response, current)
            return response

        result = {"commands": plan.command_results, "entity_ids": plan.entity_ids}
        if request.mode == "preview":
            if not plan.has_writes:
                response = self._response(
                    request,
                    status="success",
                    summary=f"読み取り専用commandを{len(request.commands)}件実行しました",
                    changes=plan.changes,
                    warnings=plan.warnings,
                    errors=plan.errors,
                    result=result,
                )
                self._save_audit(request, operation_hash, response, current)
                return response
            if not request.idempotency_key:
                response = self._response(
                    request,
                    status="input_error",
                    summary="書き込みpreviewにはidempotency_keyが必要です",
                    errors=[
                        ApiIssue(
                            code="INVALID_REQUEST",
                            message="idempotency_keyを指定してください",
                            field="idempotency_key",
                        )
                    ],
                )
                self._save_audit(request, operation_hash, response, current)
                return response
            token = f"confirm_{secrets.token_hex(16)}"
            expires = current + timedelta(minutes=config["confirmation_ttl_minutes"])
            response = self._response(
                request,
                status="preview_ready",
                summary=f"{len(plan.changes)}件の変更を確認してください",
                changes=plan.changes,
                warnings=plan.warnings,
                errors=plan.errors,
                confirmation_required=True,
                confirmation_token=token,
                result=result,
            )
            confirmation = {
                "version": "1",
                "token": token,
                "request_hash": operation_hash,
                "change_hash": _content_hash(plan.changes),
                "state_hash": _state_hash(self.root),
                "idempotency_key": request.idempotency_key,
                "effective_date": request.effective_date,
                "issued_at": current.isoformat(timespec="seconds"),
                "expires_at": expires.isoformat(timespec="seconds"),
                "used_at": None,
            }
            idem = {
                "version": "1",
                "idempotency_key": request.idempotency_key,
                "request_hash": operation_hash,
                "request_id": request.request_id,
                "status": "preview",
                "created_at": current.isoformat(timespec="seconds"),
                "completed_at": None,
                "confirmation_token": token,
                "response": response.model_dump(mode="json"),
            }
            writes = [
                (_confirmation_path(self.root, token), _json_text(confirmation)),
                (idem_path, _json_text(idem)),
            ]
            atomic_write_text_many(writes)
            self._save_audit(request, operation_hash, response, current, token)
            return response

        if plan.has_writes:
            if not request.confirmation_token:
                response = self._response(
                    request,
                    status="confirmation_required",
                    summary="commitにはconfirmation tokenが必要です",
                    errors=[
                        ApiIssue(
                            code="CONFIRMATION_REQUIRED",
                            message="previewで発行されたconfirmation tokenを指定してください",
                            field="confirmation_token",
                        )
                    ],
                )
                self._save_audit(request, operation_hash, response, current)
                return response
            try:
                confirmation_path = _confirmation_path(
                    self.root, request.confirmation_token
                )
                if not confirmation_path.exists():
                    raise CommandProblem(
                        "CONFIRMATION_INVALID", "confirmation tokenが見つかりません"
                    )
                confirmation = read_json_file(confirmation_path)
                if (
                    confirmation.get("request_hash") != operation_hash
                    or confirmation.get("idempotency_key") != request.idempotency_key
                ):
                    raise CommandProblem(
                        "CONFIRMATION_INVALID",
                        "confirmation tokenとリクエストが一致しません",
                    )
                if confirmation.get("used_at"):
                    raise CommandProblem(
                        "CONFIRMATION_INVALID", "confirmation tokenは使用済みです"
                    )
                if current >= datetime.fromisoformat(confirmation["expires_at"]):
                    raise CommandProblem(
                        "CONFIRMATION_EXPIRED",
                        "confirmation tokenの有効期限が切れています",
                    )
                if confirmation.get("state_hash") != _state_hash(self.root):
                    raise CommandProblem(
                        "PREVIEW_STALE",
                        "preview後に対象データが変更されました。再previewしてください",
                    )
                if confirmation.get("change_hash") != _content_hash(plan.changes):
                    raise CommandProblem(
                        "PREVIEW_STALE", "preview内容と現在の変更案が一致しません"
                    )
            except (CommandProblem, KeyError, ValueError) as exc:
                issue = (
                    exc.issue
                    if isinstance(exc, CommandProblem)
                    else ApiIssue(
                        code="CONFIRMATION_INVALID",
                        message="confirmation tokenの記録が不正です",
                    )
                )
                response = self._response(
                    request, status="conflict", summary=issue.message, errors=[issue]
                )
                self._save_audit(
                    request,
                    operation_hash,
                    response,
                    current,
                    request.confirmation_token,
                )
                return response
            confirmation["used_at"] = current.isoformat(timespec="seconds")
        else:
            confirmation = None
            confirmation_path = None

        try:
            _execute_external_actions(self.root, plan)
        except (OSError, ValueError) as exc:
            issue = ApiIssue(
                code="SCHEDULER_ERROR",
                message=str(exc),
                recoverable=True,
            )
            response = self._response(
                request,
                status="error",
                summary="schedulerまたはflowの実行に失敗しました",
                errors=[issue],
                result={"commands": plan.command_results},
            )
            self._save_audit(
                request, operation_hash, response, current, request.confirmation_token
            )
            return response

        status = "partial_success" if plan.errors else "committed"
        response = self._response(
            request,
            status=status,
            summary=f"{len(plan.changes)}件の変更を確定しました",
            changes=plan.changes,
            warnings=plan.warnings,
            errors=plan.errors,
            result=result,
        )
        writes = plan.state.writes()
        if confirmation is not None and confirmation_path is not None:
            writes.append((confirmation_path, _json_text(confirmation)))
        if request.idempotency_key and idem_path:
            idem = {
                "version": "1",
                "idempotency_key": request.idempotency_key,
                "request_hash": operation_hash,
                "request_id": request.request_id,
                "status": "committed",
                "created_at": (existing_idem or {}).get(
                    "created_at", current.isoformat(timespec="seconds")
                ),
                "completed_at": current.isoformat(timespec="seconds"),
                "response_summary": response.summary,
                "related_entity_ids": plan.entity_ids,
                "response": response.model_dump(mode="json"),
            }
            writes.append((idem_path, _json_text(idem)))
        try:
            atomic_write_text_many(writes)
        except OSError as exc:
            response = self._response(
                request,
                status="storage_error",
                summary="保存に失敗しました",
                errors=[
                    ApiIssue(code="STORAGE_ERROR", message=str(exc), recoverable=False)
                ],
            )
            self._save_audit(
                request, operation_hash, response, current, request.confirmation_token
            )
            return response
        self._save_audit(
            request, operation_hash, response, current, request.confirmation_token
        )
        return response


def load_audit_history(
    root: Path,
    *,
    date: str | None = None,
    request_id: str | None = None,
    idempotency_key: str | None = None,
) -> list[dict[str, Any]]:
    if date:
        parse_date(date)
    directory = root / "data" / "api" / "audit"
    values = []
    for path in sorted(directory.glob("audit-*.json")) if directory.is_dir() else []:
        value = read_json_file(path)
        if not isinstance(value, dict):
            continue
        if date and value.get("effective_date") != date:
            continue
        if request_id and value.get("request_id") != request_id:
            continue
        if idempotency_key and value.get("idempotency_key") != idempotency_key:
            continue
        values.append(value)
    values.sort(
        key=lambda item: (
            str(item.get("executed_at", "")),
            str(item.get("audit_id", "")),
        )
    )
    return values
