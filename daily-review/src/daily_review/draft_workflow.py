"""Editing and approval helpers for rule-based organization drafts."""
from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .date_utils import tomorrow_of
from .models import now_iso
from .organizer import EDITABLE_DRAFT_FIELDS, add_draft_revision
from .storage import daily_path
from .validation import validate_plan


def _clean_items(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("編集値は文字列にしてください")
        value = value.strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def replace_draft_fields(draft: dict[str, Any], replacements: dict[str, list[str]], *, force: bool) -> list[str]:
    """Apply explicit list replacements and return fields that actually changed."""
    unknown = sorted(set(replacements) - set(EDITABLE_DRAFT_FIELDS))
    if unknown:
        raise ValueError("編集できないフィールドです: " + ", ".join(unknown))
    if draft.get("status", "draft") == "approved" and not force:
        raise PermissionError("承認済みドラフトは編集できません。--forceを指定すると編集できます")

    changed: list[str] = []
    for field, values in replacements.items():
        group, key = field.split(".", 1) if "." in field else (None, field)
        target = draft[group] if group else draft
        cleaned = _clean_items(values)
        if field in {"today.main_candidates", "tomorrow.main_candidates"} and len(cleaned) > 3:
            raise ValueError(f"{field} は最大3件です")
        if target.get(key) != cleaned:
            target[key] = cleaned
            changed.append(field)

    if changed:
        if draft.get("status") == "approved":
            draft["status"] = "draft"
            draft["approved_at"] = None
            draft["approved_daily_path"] = None
        add_draft_revision(draft, changed)
        draft["updated_at"] = now_iso()
    return changed


def _items(draft: dict[str, Any], field: str) -> list[str]:
    if "." in field:
        group, key = field.split(".", 1)
        value = draft[group][key]
    else:
        value = draft[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"整理ドラフトの{field}が不正です")
    return _clean_items(value)


def _today_results(draft: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    result_entries: list[dict[str, Any]] = []
    status_by_text: dict[str, str] = {}
    for source, status, achieved in (
        ("today.completed", "completed", True),
        ("today.partial", "partial", False),
        ("today.not_completed", "not_started", False),
    ):
        for text in _items(draft, source):
            if text in status_by_text:
                continue
            status_by_text[text] = status
            result_entries.append(
                {
                    "task_id": f"draft-today-{len(result_entries) + 1}",
                    "status": status,
                    "note": text,
                    "minimum_line_achieved": achieved,
                    "recorded_at": now_iso(),
                }
            )
    return result_entries, status_by_text


def build_daily_from_draft(existing: dict[str, Any], day: str, draft: dict[str, Any]) -> dict[str, Any]:
    """Merge approved draft content into a compatible daily-record document."""
    entry = deepcopy(existing)
    today_main = _items(draft, "today.main_candidates")
    tomorrow_main = _items(draft, "tomorrow.main_candidates")
    tomorrow_other = _items(draft, "tomorrow.other_tasks")
    minimums = _items(draft, "tomorrow.minimum_candidates")
    good = _items(draft, "reflection.good")
    problems = _items(draft, "reflection.problems")
    causes = _items(draft, "reflection.causes")
    changes = _items(draft, "reflection.change_next")
    journal = _items(draft, "journal")
    unclassified = _items(draft, "unclassified")

    if not tomorrow_main:
        raise ValueError("明日のMain候補がありません。edit-draftで追加してから承認してください")
    all_tomorrow = _clean_items(tomorrow_main + tomorrow_other)
    task_results, status_by_text = _today_results(draft)
    structured_main = [
        {
            "area": item,
            "status": {"completed": "完了", "partial": "一部進んだ", "not_started": "未完了"}.get(
                status_by_text.get(item), "未記録"
            ),
            "note": item,
        }
        for item in today_main
    ]
    minimum_line = {
        item: "達成" if status_by_text.get(item) == "completed" else "未達"
        for item in today_main
    }
    entry["structured_review"] = {
        "today_main": structured_main,
        "minimum_line": minimum_line,
        "what_went_well": good,
        "breakdown_causes": causes,
        "one_change_tomorrow": changes[0] if changes else None,
    }
    if journal:
        entry["diary"] = "\n".join(journal)

    tasks = [
        {
            "id": f"draft-tomorrow-{index}",
            "area": tomorrow_main[min(index - 1, len(tomorrow_main) - 1)],
            "task": task,
            "priority": index,
            "minimum_line": minimums[index - 1] if index <= len(minimums) else task,
        }
        for index, task in enumerate(all_tomorrow, start=1)
    ]
    proposal = {
        "status": "pending_review",
        "target_date": tomorrow_of(day),
        "main": tomorrow_main,
        "tasks": tasks,
        "one_change_tomorrow": changes[0] if changes else all_tomorrow[0],
    }
    validation = validate_plan(proposal, day, final=False)
    if validation.has_errors:
        raise ValueError(" / ".join(validation.errors))
    entry["tomorrow_plan_proposal"] = proposal
    entry["draft_approval"] = {
        "draft_revision": draft.get("revision", 0),
        "today_main": today_main,
        "task_results": task_results,
        "reflection": {
            "good": good,
            "problems": problems,
            "causes": causes,
            "change_next": changes,
            "journal": journal,
        },
        "unclassified": unclassified,
    }
    return entry


def backup_daily_before_reapproval(root: Path, day: str) -> Path | None:
    source = daily_path(root, day)
    if not source.is_file():
        return None
    timestamp = now_iso().replace(":", "").replace("+", "_")
    destination = root / "data" / "backups" / "daily" / f"{day}_{timestamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    while destination.exists():
        destination = destination.with_name(destination.stem + "_1" + destination.suffix)
    shutil.copy2(source, destination)
    return destination
