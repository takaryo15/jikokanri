"""Read-only state and prompt helpers for the ``daily-review chat`` workflow."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from .date_utils import week_range_for
from .session import load_session
from .storage import DEFAULT_PRIORITIES, daily_path, load_daily, priorities_path, read_json_file


def load_priorities(root: Path, *, create: bool = False) -> list[str]:
    """Read the user-editable priorities configuration without accepting bad data."""
    path = priorities_path(root)
    if not path.exists():
        if create:
            from .storage import atomic_write_json_data

            atomic_write_json_data(path, DEFAULT_PRIORITIES)
        else:
            raise FileNotFoundError(f"優先順位設定がありません: {path}")
    value = read_json_file(path)
    priorities = value.get("priorities") if isinstance(value, dict) else None
    if not isinstance(priorities, list) or not all(isinstance(item, str) and item.strip() for item in priorities):
        raise ValueError("prioritiesは空でない文字列の配列にしてください")
    if len(priorities) != len(set(priorities)):
        raise ValueError("prioritiesに重複があります")
    return list(priorities)


def workflow_state(root: Path, day: str, draft: dict[str, Any] | None) -> str:
    """Use daily/draft data as truth; sessions only refine a waiting state."""
    daily_exists = daily_path(root, day).is_file()
    if daily_exists:
        return "approved" if draft and draft.get("status") == "approved" else "daily_only"
    if draft:
        return "daily_only" if draft.get("status") == "approved" else "draft"
    try:
        session = load_session(root, day)
    except (OSError, ValueError):
        return "new"
    if session and session.get("status") in {"prompt_ready", "waiting_for_chatgpt"}:
        return "waiting_for_chatgpt"
    return "new"


def chat_home_next_command(root: Path, summary: dict[str, Any]) -> str:
    state = workflow_state(root, summary["date"], summary.get("draft"))
    day = summary["date"]
    if summary.get("inbox_entry_count") and state == "new":
        # Preserve the existing raw-input organization step when the user has
        # deliberately used the lower-level input command.
        return ""
    if state == "draft":
        return f"daily-review chat --date {day} --resume"
    if state == "waiting_for_chatgpt":
        return f"daily-review chat --date {day} --import-only --clipboard"
    if state == "new":
        return f"daily-review chat --date {day}"
    return ""


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _previous_plan_context(root: Path, day: str) -> list[str]:
    from .date_utils import parse_date

    previous_day = (parse_date(day) - timedelta(days=1)).isoformat()
    try:
        entry = load_daily(root, previous_day) or {}
    except (OSError, ValueError):
        return []
    plan = entry.get("tomorrow_plan_final") or entry.get("tomorrow_plan_proposal") or {}
    values: list[str] = []
    for item in plan.get("main") or []:
        if isinstance(item, str):
            _append_unique(values, item)
    for task in plan.get("tasks") or []:
        if isinstance(task, dict) and isinstance(task.get("task"), str):
            _append_unique(values, task["task"])
    return values


def _weekly_minimum_lines(root: Path, day: str) -> list[str]:
    start, end = week_range_for(day)
    from .date_utils import date_range

    values: list[str] = []
    for item_day in date_range(start, end):
        try:
            entry = load_daily(root, item_day) or {}
        except (OSError, ValueError):
            continue
        plan = entry.get("tomorrow_plan_final") or entry.get("tomorrow_plan_proposal") or {}
        for task in plan.get("tasks") or []:
            if isinstance(task, dict) and isinstance(task.get("minimum_line"), str):
                _append_unique(values, task["minimum_line"])
    return values[:6]


def build_dynamic_prompt(root: Path, day: str, summary: dict[str, Any], template: str, priorities: list[str]) -> str:
    """Add local context before the existing, explicit import schema template."""
    lines = [f"対象日: {day}", "", "現在の優先順位:"]
    lines.extend(f"{index}. {priority}" for index, priority in enumerate(priorities, start=1))
    carryover = _previous_plan_context(root, day)
    if carryover:
        lines.extend(["", "前日からの引き継ぎ:"])
        lines.extend(f"- {item}" for item in carryover)
    incomplete = summary.get("incomplete_tasks") or []
    if incomplete:
        lines.extend(["", "今日の未完了タスク:"])
        for item in incomplete:
            task = item.get("task") if isinstance(item, dict) else None
            if isinstance(task, dict) and isinstance(task.get("task"), str):
                lines.append(f"- {task['task']}")
    minimums = _weekly_minimum_lines(root, day)
    if minimums:
        lines.extend(["", "今週の最低ライン:"])
        lines.extend(f"- {item}" for item in minimums)
    lines.extend([
        "",
        "以下に、今日の出来事・進捗・崩れた原因・明日の予定を自由に書いてください。",
        "",
        template.replace("YYYY-MM-DD", day).strip(),
        "",
    ])
    return "\n".join(lines)
