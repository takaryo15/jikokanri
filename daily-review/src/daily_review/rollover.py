"""Non-duplicating, preview-first carry-over of unfinished tasks."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .chat_workflow import load_priorities
from .date_utils import parse_date
from .models import now_iso
from .operation_lock import WorkspaceLock
from .storage import atomic_write_json_data, atomic_write_json_data_many, read_json_file
from .task_service import collect_tasks


DEFAULT_ROLLOVER_CONFIG = {
    "enabled": True,
    "default_policy": "ask",
    "warning_after_days": 3,
    "split_suggestion_after_days": 5,
    "main_exclusion_after_days": 7,
    "max_automatic_rollovers": 7,
    "preserve_original_due_date": True,
}
ROLLOVER_POLICIES = {
    "automatic",
    "ask",
    "never",
    "until_completed",
    "max_n_days",
    "recurring",
}
EXCLUDED_STATUSES = {
    "completed",
    "cancelled",
    "archived",
    "deleted",
    "skipped",
    "someday",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def load_rollover_config(root: Path) -> dict[str, Any]:
    result = dict(DEFAULT_ROLLOVER_CONFIG)
    path = root / "config" / "recovery.json"
    if path.exists():
        value = read_json_file(path)
        section = value.get("rollover", {}) if isinstance(value, dict) else None
        if not isinstance(section, dict):
            raise ValueError("rollover設定はJSONオブジェクトにしてください")
        for key in result:
            if key in section:
                result[key] = section[key]
    if result["default_policy"] not in ROLLOVER_POLICIES:
        raise ValueError("rollover.default_policyが不正です")
    for key in (
        "warning_after_days",
        "split_suggestion_after_days",
        "main_exclusion_after_days",
        "max_automatic_rollovers",
    ):
        if (
            not isinstance(result[key], int)
            or isinstance(result[key], bool)
            or result[key] < 1
        ):
            raise ValueError(f"rollover.{key}は1以上の整数にしてください")
    if not isinstance(result["enabled"], bool) or not isinstance(
        result["preserve_original_due_date"], bool
    ):
        raise ValueError("rolloverの真偽値設定が不正です")
    return result


def _task_raw(
    root: Path, task: dict[str, Any]
) -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
    source = task["source"]
    if source == "command_api":
        path = root / "data" / "api" / "tasks.json"
        document = read_json_file(path)
        raw = next(
            (
                item
                for item in document.get("tasks", [])
                if isinstance(item, dict) and item.get("id") == task["id"]
            ),
            None,
        )
        return (path, document, raw) if raw is not None else None
    if source == "daily_instruction":
        path = root / "data" / "daily" / f"{task['source_review_date']}.json"
        if not path.exists():
            return None
        document = read_json_file(path)
        plan = (
            document.get("tomorrow_plan_final")
            or document.get("tomorrow_plan_proposal")
            or {}
        )
        raw = next(
            (
                item
                for item in plan.get("tasks", [])
                if isinstance(item, dict) and item.get("id") == task["id"]
            ),
            None,
        )
        return (path, document, raw) if raw is not None else None
    if source == "goal_daily_plan":
        path = root / "data" / "plans" / "daily" / f"{task['source_review_date']}.json"
        if not path.exists():
            return None
        document = read_json_file(path)
        values = (document.get("main_candidates") or []) + (
            document.get("other_tasks") or []
        )
        raw = next(
            (
                item
                for item in values
                if isinstance(item, dict) and item.get("id") == task["id"]
            ),
            None,
        )
        return (path, document, raw) if raw is not None else None
    return None


def _existing_target_ids(root: Path, target_date: str) -> set[str]:
    values: set[str] = set()
    directory = root / "data" / "daily"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            entry = read_json_file(path)
        except (OSError, ValueError):
            continue
        for key in ("tomorrow_plan_final", "tomorrow_plan_proposal"):
            plan = entry.get(key) if isinstance(entry, dict) else None
            if not isinstance(plan, dict) or plan.get("target_date") != target_date:
                continue
            values.update(
                str(item.get("id"))
                for item in plan.get("tasks", [])
                if isinstance(item, dict) and item.get("id")
            )
    return values


def build_rollover_preview(root: Path, target_date: str) -> dict[str, Any]:
    target = parse_date(target_date)
    source_date = (target - timedelta(days=1)).isoformat()
    config = load_rollover_config(root)
    if not config["enabled"]:
        raise ValueError("rolloverは設定で無効です")
    existing = _existing_target_ids(root, target_date)
    candidates = []
    ignored = []
    state_values = []
    for task in collect_tasks(root):
        raw_ref = _task_raw(root, task)
        raw = raw_ref[2] if raw_ref else {}
        status = str(raw.get("status") or task.get("status") or "pending")
        policy = str(raw.get("rollover_policy") or config["default_policy"])
        count = max(0, int(raw.get("rollover_count") or 0))
        due = str(raw.get("current_due_date") or task.get("due_date") or "")
        planned = str(raw.get("planned_date") or "")
        blocked = status == "blocked" and not raw.get("blocked_until") == target_date
        reason = None
        if status in EXCLUDED_STATUSES:
            reason = f"status={status}"
        elif policy == "never" or raw.get("rollover_disabled") is True:
            reason = "繰越禁止"
        elif blocked:
            reason = "blockedの解除条件未達"
        elif task["id"] in existing:
            reason = "対象日の指示書に登録済み"
        elif count >= config["max_automatic_rollovers"] and policy != "until_completed":
            reason = "自動繰越上限"
        elif not (
            task.get("source_review_date") == source_date
            or (due and due <= source_date)
            or planned == target_date
            or policy in {"automatic", "until_completed", "recurring"}
        ):
            reason = "対象条件外"
        if reason:
            ignored.append(
                {"task_id": task["id"], "title": task["title"], "reason": reason}
            )
            continue
        after = count + 1
        reasons = []
        if task.get("is_main"):
            reasons.append("前日Main")
        if task.get("is_minimum"):
            reasons.append("最低限付きタスク")
        if due and due <= source_date:
            reasons.append("期限超過または前日期限")
        if after >= config["main_exclusion_after_days"]:
            decision = "needs_confirmation"
            reasons.append("長期未完了のため自動Main対象外")
        elif after >= config["split_suggestion_after_days"]:
            decision = "split_suggested"
            reasons.append("分解・延期・中止の見直し候補")
        else:
            decision = "carry_over"
        if after >= config["warning_after_days"]:
            reasons.append(f"{after}回目の繰越")
        minimum = str(task.get("minimum_line") or "着手する")
        suggested_minimum = (
            f"「{task['title']}」を開いて次の1手を確認する"
            if after >= config["warning_after_days"]
            else minimum
        )
        candidate = {
            "task_id": task["id"],
            "title": task["title"],
            "source": task["source"],
            "source_review_date": task["source_review_date"],
            "category": task.get("category", ""),
            "priority": task.get("priority", "medium"),
            "decision": decision,
            "reasons": reasons,
            "rollover_count_before": count,
            "rollover_count_after": after,
            "original_due_date": raw.get("original_due_date") or due or None,
            "current_due_date": due or None,
            "planned_date": target_date,
            "minimum": minimum,
            "suggested_minimum": suggested_minimum,
            "minimum_is_suggestion": suggested_minimum != minimum,
        }
        candidates.append(candidate)
        if raw_ref:
            state_values.append((str(raw_ref[0]), _hash(raw_ref[1])))
    try:
        priorities = load_priorities(root)
    except (OSError, ValueError):
        priorities = []
    category_rank = {name: index for index, name in enumerate(priorities)}
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(
        key=lambda item: (
            1
            if item["rollover_count_after"] >= config["main_exclusion_after_days"]
            else 0,
            0 if "前日Main" in item["reasons"] else 1,
            priority_rank.get(item["priority"], 3),
            category_rank.get(item["category"], len(category_rank)),
            item["task_id"],
        )
    )
    main = [
        item["task_id"]
        for item in candidates
        if item["rollover_count_after"] < config["main_exclusion_after_days"]
    ][:3]
    optional = [item["task_id"] for item in candidates if item["task_id"] not in main]
    return {
        "status": "preview_ready",
        "target_date": target_date,
        "source_date": source_date,
        "candidates": candidates,
        "ignored": ignored,
        "main_task_ids": main,
        "optional_task_ids": optional,
        "state_hash": _hash(sorted(set(state_values))),
    }


def _confirmation_path(root: Path, token: str) -> Path:
    if not token.startswith("rollover_confirm_"):
        raise ValueError("rollover confirmation tokenが不正です")
    return root / "data" / "transactions" / "rollover" / f"{token}.json"


def preview_rollover(
    root: Path, target_date: str, *, idempotency_key: str | None = None
) -> dict[str, Any]:
    preview = build_rollover_preview(root, target_date)
    token = f"rollover_confirm_{uuid.uuid4().hex}"
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    preview.update(
        {
            "confirmation_required": True,
            "confirmation_token": token,
            "idempotency_key": idempotency_key,
        }
    )
    atomic_write_json_data(
        _confirmation_path(root, token),
        {
            "token": token,
            "target_date": target_date,
            "preview_hash": _hash(preview["candidates"]),
            "state_hash": preview["state_hash"],
            "idempotency_key": idempotency_key,
            "issued_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
            "used_at": None,
        },
    )
    return preview


def _history_path(root: Path) -> Path:
    return root / "data" / "rollover" / "history.json"


def rollover_history(root: Path) -> list[dict[str, Any]]:
    path = _history_path(root)
    if not path.exists():
        return []
    value = read_json_file(path)
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError("rollover履歴が不正です")
    return sorted(
        value["records"], key=lambda item: str(item.get("applied_at", "")), reverse=True
    )


def apply_rollover(
    root: Path,
    target_date: str,
    *,
    confirmation_token: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    path = _confirmation_path(root, confirmation_token)
    if not path.exists():
        raise ValueError("rollover confirmation tokenが見つかりません")
    confirmation = read_json_file(path)
    if confirmation.get("used_at"):
        stored = confirmation.get("result") or {}
        return {**stored, "status": "idempotent_replay"}
    if datetime.now(ZoneInfo("Asia/Tokyo")) >= datetime.fromisoformat(
        str(confirmation.get("expires_at"))
    ):
        raise ValueError("rollover confirmation tokenの有効期限が切れています")
    preview = build_rollover_preview(root, target_date)
    if (
        confirmation.get("target_date") != target_date
        or confirmation.get("state_hash") != preview["state_hash"]
        or confirmation.get("preview_hash") != _hash(preview["candidates"])
    ):
        raise ValueError(
            "rollover preview後にタスクが変更されました。再previewしてください"
        )
    effective_key = (
        idempotency_key
        or confirmation.get("idempotency_key")
        or f"rollover-{target_date}"
    )
    idem_path = (
        root
        / "data"
        / "rollover"
        / "idempotency"
        / f"{hashlib.sha256(effective_key.encode()).hexdigest()}.json"
    )
    request_hash = _hash(
        {"target_date": target_date, "candidates": preview["candidates"]}
    )
    if idem_path.exists():
        existing = read_json_file(idem_path)
        if existing.get("request_hash") != request_hash:
            raise ValueError("同じidempotency keyが異なる繰越内容で使われています")
        return existing["result"]
    documents: dict[Path, dict[str, Any]] = {}
    records = []
    for candidate in preview["candidates"]:
        task = next(
            item
            for item in collect_tasks(root)
            if item["id"] == candidate["task_id"]
            and item["source"] == candidate["source"]
            and item["source_review_date"] == candidate["source_review_date"]
        )
        raw_ref = _task_raw(root, task)
        if not raw_ref:
            raise ValueError(f"繰越対象タスクが見つかりません: {candidate['task_id']}")
        document_path, document, raw = raw_ref
        if document_path in documents:
            document = documents[document_path]
            refreshed = _task_raw_from_document(document, task)
            if refreshed is None:
                raise ValueError(
                    f"繰越対象タスクが見つかりません: {candidate['task_id']}"
                )
            raw = refreshed
        else:
            document = copy.deepcopy(document)
            raw = _task_raw_from_document(document, task)
            documents[document_path] = document
        before = {
            "rollover_count": int(raw.get("rollover_count") or 0),
            "planned_date": raw.get("planned_date"),
            "due_date": raw.get("due_date"),
        }
        due = raw.get("current_due_date") or raw.get("due_date") or task.get("due_date")
        if due and not raw.get("original_due_date"):
            raw["original_due_date"] = due
        raw["current_due_date"] = due
        raw["planned_date"] = target_date
        raw["next_action_date"] = target_date
        raw["first_planned_date"] = (
            raw.get("first_planned_date")
            or task.get("source_review_date")
            or target_date
        )
        raw["latest_planned_date"] = target_date
        raw["rollover_count"] = candidate["rollover_count_after"]
        raw["consecutive_unfinished_days"] = candidate["rollover_count_after"]
        raw["last_rolled_over_at"] = now_iso()
        raw["last_rolled_from_date"] = preview["source_date"]
        raw["rollover_reason"] = "; ".join(candidate["reasons"])
        raw["rollover_policy"] = (
            raw.get("rollover_policy") or load_rollover_config(root)["default_policy"]
        )
        records.append(
            {
                "rollover_id": f"rollover_{uuid.uuid4().hex[:12]}",
                "target_date": target_date,
                "source_date": preview["source_date"],
                "task_id": candidate["task_id"],
                "previous_state": before,
                "new_state": {
                    "rollover_count": raw["rollover_count"],
                    "planned_date": target_date,
                },
                "decision": candidate["decision"],
                "reason": candidate["reasons"],
                "rollover_count_before": candidate["rollover_count_before"],
                "rollover_count_after": candidate["rollover_count_after"],
                "applied_at": now_iso(),
                "source": "cli",
                "idempotency_key": effective_key,
            }
        )
    result = {
        "status": "applied",
        "target_date": target_date,
        "applied_count": len(records),
        "main_task_ids": preview["main_task_ids"],
        "optional_task_ids": preview["optional_task_ids"],
        "task_ids": [item["task_id"] for item in records],
    }
    confirmation["used_at"] = now_iso()
    confirmation["result"] = result
    with WorkspaceLock(root, "rollover"):
        history = {"version": "1", "records": rollover_history(root)}
        history["records"].extend(records)
        writes = list(documents.items()) + [
            (_history_path(root), history),
            (
                idem_path,
                {
                    "idempotency_key": effective_key,
                    "request_hash": request_hash,
                    "result": result,
                    "completed_at": now_iso(),
                },
            ),
            (path, confirmation),
        ]
        atomic_write_json_data_many(writes)
    return result


def _task_raw_from_document(
    document: dict[str, Any], task: dict[str, Any]
) -> dict[str, Any] | None:
    if task["source"] == "command_api":
        values = document.get("tasks", [])
    elif task["source"] == "daily_instruction":
        plan = (
            document.get("tomorrow_plan_final")
            or document.get("tomorrow_plan_proposal")
            or {}
        )
        values = plan.get("tasks", [])
    else:
        values = (document.get("main_candidates") or []) + (
            document.get("other_tasks") or []
        )
    return next(
        (
            item
            for item in values
            if isinstance(item, dict) and item.get("id") == task["id"]
        ),
        None,
    )
