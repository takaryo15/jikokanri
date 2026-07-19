from __future__ import annotations

from pathlib import Path
from typing import Any

from .date_utils import date_range, month_range_for, tomorrow_of, week_range_for
from .storage import draft_path, inbox_path, load_daily, read_json_file


def _load_daily_safely(
    root: Path, day: str, errors: list[str]
) -> dict[str, Any] | None:
    try:
        return load_daily(root, day)
    except (OSError, ValueError) as exc:
        errors.append(f"日次JSONを読み込めません: {day} ({exc})")
        return None


def _find_final_by_target(
    root: Path, target: str, errors: list[str]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    directory = root / "data" / "daily"
    if not directory.is_dir():
        return None, None
    for path in sorted(directory.glob("*.json")):
        try:
            entry = read_json_file(path)
        except (OSError, ValueError) as exc:
            errors.append(f"日次JSONを読み込めません: {path.name} ({exc})")
            continue
        final = entry.get("tomorrow_plan_final") or {}
        if final.get("target_date") == target:
            return entry, final
    return None, None


def _recorded_days(root: Path, start: str, end: str, errors: list[str]) -> int:
    return sum(
        1
        for day in date_range(start, end)
        if _load_daily_safely(root, day, errors) is not None
    )


def build_daily_summary(root: Path, day: str) -> dict[str, Any]:
    """Read the current daily state without writing or migrating anything."""
    errors: list[str] = []
    entry = _load_daily_safely(root, day, errors) or {}
    final_entry, today_final = _find_final_by_target(root, day, errors)
    results = {
        item.get("task_id"): item
        for item in (final_entry or {}).get("task_results") or []
    }
    tasks = today_final.get("tasks") or [] if today_final else []
    recorded_results = sum(1 for task in tasks if task.get("id") in results)
    draft_approval = (
        entry.get("draft_approval")
        if isinstance(entry.get("draft_approval"), dict)
        else {}
    )
    draft_result_only = not today_final and isinstance(
        draft_approval.get("task_results"), list
    )
    if draft_result_only:
        results = {
            item.get("task_id"): item
            for item in draft_approval["task_results"]
            if isinstance(item, dict)
        }
        tasks = list(results.values())
        recorded_results = len(results)
    incomplete = []
    if not draft_result_only:
        for task in tasks:
            result = results.get(task.get("id"))
            if not result or result.get("status") != "completed":
                incomplete.append(
                    {"task": task, "status": result.get("status") if result else None}
                )
    week_start, week_end = week_range_for(day)
    month_start, month_end = month_range_for(day)
    inbox_entries: list[Any] = []
    inbox = inbox_path(root, day)
    if inbox.exists():
        try:
            inbox_payload = read_json_file(inbox)
            if not isinstance(inbox_payload, dict) or not isinstance(
                inbox_payload.get("entries"), list
            ):
                raise ValueError("entriesがありません")
            inbox_entries = inbox_payload["entries"]
        except (OSError, ValueError) as exc:
            errors.append(f"inbox JSONを読み込めません: {day} ({exc})")
    draft: dict[str, Any] | None = None
    draft_file = draft_path(root, day)
    if draft_file.exists():
        try:
            value = read_json_file(draft_file)
            if not isinstance(value, dict):
                raise ValueError("JSONオブジェクトではありません")
            draft = value
        except (OSError, ValueError) as exc:
            errors.append(f"整理ドラフトを読み込めません: {day} ({exc})")
    return {
        "date": day,
        "entry": entry,
        "today_final": today_final or None,
        "today_main": (today_final or {}).get("main")
        or draft_approval.get("today_main")
        or [],
        "task_results": {
            "recorded": recorded_results,
            "total": len(tasks),
            "exists": bool(results),
        },
        "night_review_exists": bool(
            entry.get("raw_log") or entry.get("structured_review") or entry.get("diary")
        ),
        "tomorrow_proposal": entry.get("tomorrow_plan_proposal"),
        "tomorrow_final": entry.get("tomorrow_plan_final"),
        "week_recorded_days": _recorded_days(root, week_start, week_end, errors),
        "month_recorded_days": _recorded_days(root, month_start, month_end, errors),
        "incomplete_tasks": incomplete,
        "inbox_entry_count": len(inbox_entries),
        "draft": draft,
        "draft_status": (draft or {}).get("status", "draft") if draft else None,
        "errors": list(dict.fromkeys(errors)),
    }


def next_action_kind(summary: dict[str, Any]) -> str:
    entry = summary["entry"]
    if summary.get("draft") and summary.get("draft_status") != "approved":
        return "draft_review"
    if summary.get("inbox_entry_count") and not summary.get("draft"):
        return "organize"
    if entry.get("tomorrow_plan_proposal") and not entry.get("tomorrow_plan_final"):
        return "proposal"
    if entry.get("tomorrow_plan_final"):
        return "complete"
    if summary["today_final"]:
        return "today"
    return "review"


def next_command(summary: dict[str, Any]) -> str:
    entry = summary["entry"]
    day = summary["date"]
    kind = next_action_kind(summary)
    if kind == "proposal":
        return f"daily-review show-proposal --date {day}"
    if kind == "complete":
        target = entry["tomorrow_plan_final"].get("target_date", tomorrow_of(day))
        return f"daily-review today --date {target}"
    if kind == "today":
        return f"daily-review today --date {day}"
    if kind == "organize":
        return f"daily-review organize --date {day}"
    return f"daily-review close-day --date {day} --clipboard --dry-run"


def home_next_command(summary: dict[str, Any]) -> str:
    """Use the v1.1 integrated flow only on the daily home screen."""
    day = summary["date"]
    if not summary.get("draft") and not summary.get("inbox_entry_count"):
        return "daily-review chat-import --clipboard"
    if summary.get("draft") and summary.get("draft_status") != "approved":
        return f"daily-review reflect --date {day} --resume"
    return next_command(summary)
