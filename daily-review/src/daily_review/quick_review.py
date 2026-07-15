"""Small, loss-resistant daily review input use case."""

from __future__ import annotations

import copy
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from .date_utils import parse_date, tomorrow_of
from .markdown import render_daily
from .models import DailyEntry, now_iso
from .storage import (
    atomic_write_json_data,
    daily_log_path,
    inbox_path,
    load_daily,
    save_daily,
    write_text,
)


class QuickReviewError(ValueError):
    pass


FIELDS = ("done", "not_done", "causes", "tomorrow", "minimum")


def normalize_payload(payload: Any, *, day: str) -> dict[str, Any]:
    parse_date(day)
    if not isinstance(payload, dict):
        raise QuickReviewError("クイックレビュー入力はJSONオブジェクトにしてください")
    unknown = sorted(set(payload) - {*FIELDS, "journal", "date"})
    if unknown:
        raise QuickReviewError(f"未知のフィールドがあります: {', '.join(unknown)}")
    if payload.get("date") not in (None, day):
        raise QuickReviewError(
            f"日付が一致しません: CLI={day} JSON={payload.get('date')}"
        )
    result: dict[str, Any] = {"date": day}
    for field in FIELDS:
        value = payload.get(field, [])
        if isinstance(value, str):
            value = [value] if value else []
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise QuickReviewError(f"{field}は文字列または文字列配列にしてください")
        result[field] = [item for item in value if item.strip()]
    journal = payload.get("journal", "")
    if journal is None:
        journal = ""
    if not isinstance(journal, str):
        raise QuickReviewError("journalは文字列にしてください")
    result["journal"] = journal
    if not any(result[field] for field in FIELDS) and not journal.strip():
        raise QuickReviewError("入力内容が空です")
    return result


def render_raw(payload: dict[str, Any]) -> str:
    labels = (
        ("done", "今日できたこと"),
        ("not_done", "できなかったこと"),
        ("causes", "崩れた原因"),
        ("tomorrow", "明日やること"),
        ("minimum", "明日の最低限"),
    )
    parts: list[str] = []
    for key, label in labels:
        parts.append(f"## {label}")
        parts.extend(payload[key] or ["なし"])
    parts.extend(["## 日記", payload["journal"] or "なし"])
    return "\n".join(parts)


def build_quick_entry(
    day: str, payload: dict[str, Any], existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    value = normalize_payload(payload, day=day)
    entry = (
        copy.deepcopy(existing)
        if existing
        else {"date": day, "created_at": now_iso(), "updated_at": now_iso()}
    )
    minimums = value["minimum"]
    tomorrow_tasks = []
    for index, title in enumerate(value["tomorrow"], start=1):
        minimum = (
            minimums[index - 1]
            if index <= len(minimums)
            else minimums[0]
            if minimums
            else "着手する"
        )
        tomorrow_tasks.append(
            {
                "id": f"quick-task-{index}",
                "area": title,
                "task": title,
                "priority": index,
                "minimum_line": minimum,
            }
        )
    today_main = [{"area": item, "status": "完了"} for item in value["done"][:3]] + [
        {"area": item, "status": "未完了"}
        for item in value["not_done"][: max(0, 3 - len(value["done"][:3]))]
    ]
    entry.update(
        {
            "raw_log": render_raw(value),
            "diary": value["journal"] or entry.get("diary"),
            "structured_review": {
                "today_main": today_main,
                "minimum_line": entry.get("structured_review", {}).get(
                    "minimum_line", {}
                )
                if isinstance(entry.get("structured_review"), dict)
                else {},
                "what_went_well": value["done"],
                "breakdown_causes": value["causes"],
                "one_change_tomorrow": value["tomorrow"][0]
                if value["tomorrow"]
                else "最低限を実行する",
            },
            "tomorrow_plan_proposal": {
                "status": "pending_review",
                "target_date": tomorrow_of(day),
                "main": value["tomorrow"][:3],
                "tasks": tomorrow_tasks,
                "one_change_tomorrow": value["tomorrow"][0]
                if value["tomorrow"]
                else "最低限を実行する",
                "approved_at": None,
            },
            "quick_review": {
                **value,
                "main_candidates": value["tomorrow"][:3],
                "backlog_candidates": value["tomorrow"][3:],
                "recorded_at": now_iso(),
                "revision": int((entry.get("quick_review") or {}).get("revision", 0))
                + 1,
            },
        }
    )
    entry["updated_at"] = now_iso()
    DailyEntry.model_validate(entry)
    return entry


def _save_raw_inbox(
    root: Path, day: str, payload: dict[str, Any], raw_input: str
) -> str:
    path = inbox_path(root, day)
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, dict) or not isinstance(
            current.get("entries"), list
        ):
            raise QuickReviewError("inbox JSONの形式が不正です")
    else:
        current = {"date": day, "entries": []}
    entry_id = f"quick-{day.replace('-', '')}-{uuid.uuid4().hex[:8]}"
    current["entries"].append(
        {
            "id": entry_id,
            "created_at": now_iso(),
            "source": "quick_review",
            "raw_text": raw_input,
            "quick_review_payload": payload,
        }
    )
    atomic_write_json_data(path, current)
    return entry_id


def save_quick_review(
    root: Path, day: str, payload: dict[str, Any], *, raw_input: str, force: bool
) -> dict[str, Any]:
    normalized = normalize_payload(payload, day=day)
    existing = load_daily(root, day)
    if existing and not force:
        raise QuickReviewError(
            f"{day}の日次レビューはすでに存在します。更新する場合は --force を指定してください"
        )
    planned = build_quick_entry(day, normalized, existing)
    # Raw input is intentionally committed first.  A later formatting or
    # daily-save failure can be resumed without losing user input.
    input_id = _save_raw_inbox(root, day, normalized, raw_input)
    backup_path: Path | None = None
    if existing:
        timestamp = now_iso().replace(":", "").replace("+", "_")
        source = root / "data" / "daily" / f"{day}.json"
        backup_path = root / "data" / "backups" / "daily" / f"{day}_{timestamp}.json"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
    json_path = save_daily(root, day, planned)
    markdown_path = write_text(daily_log_path(root, day), render_daily(planned))
    return {
        "entry": planned,
        "input_id": input_id,
        "json_path": json_path,
        "markdown_path": markdown_path,
        "backup_path": backup_path,
    }
