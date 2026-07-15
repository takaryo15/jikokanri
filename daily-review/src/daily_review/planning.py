"""Safe goal-to-weekly/daily planning helpers.

Plans are deliberately separate from the existing daily and weekly review
documents.  They only contain references to goals, so older review files stay
valid and a plan can always be reviewed before it is used.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from .date_utils import parse_date, week_range_for
from .goals import GoalError, find_milestone, find_step, load_goal, load_goals, milestones_of, next_goal_action
from .models import now_iso
from .storage import atomic_write_json_data, read_json_file


WEEKLY_PLAN_STATUSES = ("draft", "approved")
DAILY_PLAN_STATUSES = ("draft", "approved")
MAX_FOCUS_ITEMS = 5
MAX_MAIN_CANDIDATES = 3


class PlanningError(ValueError):
    pass


def plans_dir(root: Path) -> Path:
    return root / "data" / "plans"


def weekly_plans_dir(root: Path) -> Path:
    return plans_dir(root) / "weekly"


def daily_plans_dir(root: Path) -> Path:
    return plans_dir(root) / "daily"


def plans_backup_dir(root: Path) -> Path:
    return root / "data" / "backups" / "plans"


def weekly_plan_path(root: Path, week_start: str) -> Path:
    start, end = week_range_for(week_start)
    return weekly_plans_dir(root) / f"{start}_{end}.json"


def daily_plan_path(root: Path, day: str) -> Path:
    parse_date(day)
    return daily_plans_dir(root) / f"{day}.json"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _backup(path: Path, root: Path) -> None:
    if not path.exists():
        return
    target = plans_backup_dir(root) / f"{path.stem}_{now_iso().replace(':', '').replace('+', '_')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def load_weekly_plan(root: Path, week_start: str) -> dict[str, Any] | None:
    path = weekly_plan_path(root, week_start)
    if not path.exists():
        return None
    value = read_json_file(path)
    validate_weekly_plan(value, week_start=week_start)
    return value


def load_daily_plan(root: Path, day: str) -> dict[str, Any] | None:
    path = daily_plan_path(root, day)
    if not path.exists():
        return None
    value = read_json_file(path)
    validate_daily_plan(value, day=day)
    return value


def _validate_ref(root: Path, item: dict[str, Any]) -> None:
    goal_id = item.get("goal_id")
    milestone_id = item.get("milestone_id")
    step_id = item.get("step_id")
    if not isinstance(goal_id, str):
        raise PlanningError("goal_idがありません")
    try:
        goal = load_goal(root, goal_id)
        if goal.get("status") in {"completed", "cancelled", "archived"}:
            raise PlanningError("完了・中止・archived目標は参照できません")
        if milestone_id is not None:
            milestone = find_milestone(goal, milestone_id)
            if milestone.get("status") == "cancelled":
                raise PlanningError("cancelledマイルストーンは参照できません")
            if step_id is not None:
                find_step(goal, milestone_id, step_id)
    except GoalError as exc:
        raise PlanningError(str(exc)) from exc


def _validate_item(item: Any, *, daily: bool = False) -> None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
        raise PlanningError("計画項目IDが不正です")
    if not isinstance(item.get("title"), str) or not item["title"].strip():
        raise PlanningError("計画項目titleが不正です")
    if item.get("source_type") not in {"milestone", "goal_step"}:
        raise PlanningError("計画項目source_typeが不正です")
    if item.get("due_date") is not None:
        try:
            parse_date(item["due_date"])
        except (TypeError, ValueError) as exc:
            raise PlanningError("計画項目due_dateが不正です") from exc
    if daily and item.get("estimated_minutes") is not None and (not isinstance(item["estimated_minutes"], int) or item["estimated_minutes"] < 0):
        raise PlanningError("estimated_minutesが不正です")


def validate_weekly_plan(value: Any, *, week_start: str | None = None) -> None:
    if not isinstance(value, dict):
        raise PlanningError("週次計画JSONの形式が不正です")
    start, end = week_range_for(value.get("week_start", ""))
    if value.get("week_start") != start or value.get("week_end") != end or (week_start and start != week_range_for(week_start)[0]):
        raise PlanningError("週次計画の週境界が不正です")
    if value.get("status") not in WEEKLY_PLAN_STATUSES:
        raise PlanningError("週次計画statusが不正です")
    if value["status"] == "approved" and not isinstance(value.get("approved_at"), str):
        raise PlanningError("承認済み週次計画にapproved_atがありません")
    focus = value.get("focus_items")
    if not isinstance(focus, list) or len(focus) > MAX_FOCUS_ITEMS:
        raise PlanningError("週次重点は最大5件です")
    if len({item.get("id") for item in focus if isinstance(item, dict)}) != len(focus):
        raise PlanningError("週次重点IDが重複しています")
    for item in focus + (value.get("other_candidates") or []):
        _validate_item(item)


def validate_daily_plan(value: Any, *, day: str | None = None) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("date"), str):
        raise PlanningError("日次計画JSONの形式が不正です")
    parse_date(value["date"])
    if day and value["date"] != day:
        raise PlanningError("別日の日次計画を参照しています")
    if value.get("status") not in DAILY_PLAN_STATUSES:
        raise PlanningError("日次計画statusが不正です")
    if value["status"] == "approved" and not isinstance(value.get("approved_at"), str):
        raise PlanningError("承認済み日次計画にapproved_atがありません")
    main = value.get("main_candidates")
    if not isinstance(main, list) or len(main) > MAX_MAIN_CANDIDATES:
        raise PlanningError("日次Main候補は最大3件です")
    if len({item.get("id") for item in main if isinstance(item, dict)}) != len(main):
        raise PlanningError("日次Main候補IDが重複しています")
    for item in main + (value.get("other_tasks") or []):
        _validate_item(item, daily=True)
    links = value.get("goal_links") or []
    if not isinstance(links, list):
        raise PlanningError("goal_linksの形式が不正です")
    seen = set()
    for link in links:
        if not isinstance(link, dict) or link.get("record_type") not in {"main", "task"} or not isinstance(link.get("record_index"), int):
            raise PlanningError("goal linkの形式が不正です")
        key = (link["record_type"], link["record_index"])
        if key in seen:
            raise PlanningError("同一記録への重複リンクがあります")
        seen.add(key)


def _candidate(goal: dict[str, Any], milestone: dict[str, Any], step: dict[str, Any] | None, *, reason: str) -> dict[str, Any]:
    source_type = "goal_step" if step else "milestone"
    item = step or milestone
    return {
        "id": _new_id("focus"), "title": item["title"], "category": goal.get("category") or goal["title"],
        "source_type": source_type, "goal_id": goal["id"], "milestone_id": milestone["id"],
        "step_id": step.get("id") if step else None, "due_date": item.get("due_date") or milestone.get("due_date"),
        "reason": reason, "minimum": step.get("minimum") if step else None, "status": item.get("status"),
    }


def _eligible_actions(goal: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    completed = {item.get("id") for item in milestones_of(goal) if item.get("status") == "completed"}
    result: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for milestone in milestones_of(goal):
        if milestone.get("status") not in {"planned", "active"} or not set(milestone.get("dependencies") or []) <= completed:
            continue
        done = {step.get("id") for step in milestone.get("steps") or [] if step.get("status") == "done"}
        candidates = [step for step in milestone.get("steps") or [] if step.get("status") in {"todo", "doing"} and set(step.get("dependencies") or []) <= done]
        if candidates:
            result.extend((milestone, step) for step in candidates)
        elif not milestone.get("steps"):
            result.append((milestone, None))
    return result


def generate_weekly_plan(root: Path, day: str, priorities: list[str]) -> dict[str, Any]:
    start, end = week_range_for(day)
    scored: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for goal in load_goals(root):
        if goal.get("status") != "active":
            continue
        for milestone, step in _eligible_actions(goal):
            item = step or milestone
            due = item.get("due_date") or milestone.get("due_date") or "9999-12-31"
            overdue = due < start
            this_week = start <= due <= end
            doing = item.get("status") == "doing"
            reason = "期限超過" if overdue else "今週期限" if this_week else "進行中のステップ" if doing else "目標の次アクション"
            category_rank = priorities.index(goal.get("category")) if goal.get("category") in priorities else len(priorities)
            score = (category_rank, 0 if overdue else 1, 0 if this_week else 1, 0 if doing else 1, 0 if milestone.get("status") == "active" else 1, due, milestone.get("order", 0), (step or {}).get("order", 0))
            scored.append((score, _candidate(goal, milestone, step, reason=reason)))
    scored.sort(key=lambda item: item[0])
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for _, item in scored:
        key = (item["goal_id"], item["milestone_id"], item.get("step_id"))
        if key not in seen:
            seen.add(key); unique.append(item)
    carryovers: list[dict[str, Any]] = []
    previous_start = (parse_date(start) - timedelta(days=7)).isoformat()
    try:
        previous = load_weekly_plan(root, previous_start)
    except (OSError, ValueError, PlanningError):
        previous = None
    if previous and previous.get("status") == "approved":
        for item in previous.get("focus_items") or []:
            try:
                goal = load_goal(root, item["goal_id"])
                milestone = find_milestone(goal, item["milestone_id"])
                subject = find_step(goal, item["milestone_id"], item["step_id"])[1] if item.get("step_id") else milestone
                if subject.get("status") not in {"done", "completed", "cancelled"} and (not subject.get("due_date") or subject["due_date"] >= start):
                    carryovers.append(dict(item))
            except (GoalError, KeyError):
                continue
    timestamp = now_iso()
    return {"week_start": start, "week_end": end, "status": "draft", "focus_items": unique[:MAX_FOCUS_ITEMS], "other_candidates": unique[MAX_FOCUS_ITEMS:], "carryovers": carryovers, "created_at": timestamp, "updated_at": timestamp, "approved_at": None, "revision": 1}


def save_weekly_plan(root: Path, plan: dict[str, Any]) -> Path:
    validate_weekly_plan(plan)
    path = weekly_plan_path(root, plan["week_start"])
    if path.exists():
        current = load_weekly_plan(root, plan["week_start"])
        if current and current.get("status") == "approved":
            raise PlanningError("承認済み週次計画はplan reviewで編集してください")
        _backup(path, root)
        plan["revision"] = int((current or {}).get("revision", 1)) + 1
        plan["created_at"] = (current or {}).get("created_at", plan["created_at"])
    plan["updated_at"] = now_iso()
    atomic_write_json_data(path, plan)
    return path


def save_daily_plan(root: Path, plan: dict[str, Any]) -> Path:
    validate_daily_plan(plan)
    path = daily_plan_path(root, plan["date"])
    if path.exists():
        current = load_daily_plan(root, plan["date"])
        if current and current.get("status") == "approved":
            raise PlanningError("承認済み日次計画はplan reviewで編集してください")
        _backup(path, root)
        plan["revision"] = int((current or {}).get("revision", 1)) + 1
        plan["created_at"] = (current or {}).get("created_at", plan["created_at"])
    plan["updated_at"] = now_iso()
    atomic_write_json_data(path, plan)
    return path


def generate_daily_plan(root: Path, day: str, priorities: list[str]) -> dict[str, Any]:
    week_start, _ = week_range_for(day)
    weekly = load_weekly_plan(root, week_start)
    candidates: list[dict[str, Any]] = []
    if weekly and weekly.get("status") == "approved":
        candidates.extend(dict(item, id=_new_id("daily-plan"), weekly_focus_id=item["id"], reason="承認済み週次重点") for item in weekly.get("focus_items") or [])
    generated = generate_weekly_plan(root, day, priorities)
    for item in generated["focus_items"] + generated["other_candidates"]:
        item = dict(item, id=_new_id("daily-plan"), weekly_focus_id=None)
        candidates.append(item)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in candidates:
        key = (item["goal_id"], item["milestone_id"], item.get("step_id"))
        if key not in seen:
            seen.add(key); unique.append(item)
    main_limit = MAX_MAIN_CANDIDATES
    planning_config = root / "config" / "planning.json"
    if planning_config.exists():
        config = read_json_file(planning_config)
        configured = config.get("max_daily_main") if isinstance(config, dict) else None
        if not isinstance(configured, int) or isinstance(configured, bool) or not 1 <= configured <= MAX_MAIN_CANDIDATES:
            raise PlanningError("config/planning.jsonのmax_daily_mainは1〜3にしてください")
        main_limit = configured
    timestamp = now_iso()
    main, other = unique[:main_limit], unique[main_limit:]
    return {"date": day, "status": "draft", "main_candidates": main, "other_tasks": other, "minimum_candidates": [item["minimum"] for item in main if item.get("minimum")], "goal_links": [], "created_at": timestamp, "updated_at": timestamp, "approved_at": None, "revision": 1}


def approve_plan(root: Path, *, week: str | None = None, day: str | None = None) -> dict[str, Any]:
    if (week is None) == (day is None):
        raise PlanningError("--week または --date のどちらか一方を指定してください")
    if week:
        plan = load_weekly_plan(root, week)
        if not plan: raise PlanningError("週次計画が見つかりません")
        path = weekly_plan_path(root, week); validate_weekly_plan(plan)
    else:
        plan = load_daily_plan(root, day or "")
        if not plan: raise PlanningError("日次計画が見つかりません")
        path = daily_plan_path(root, day or ""); validate_daily_plan(plan)
    _backup(path, root)
    plan["status"] = "approved"; plan["approved_at"] = now_iso(); plan["updated_at"] = now_iso(); plan["revision"] = int(plan.get("revision", 1)) + 1
    atomic_write_json_data(path, plan)
    return plan


def update_daily_plan(root: Path, plan: dict[str, Any]) -> None:
    validate_daily_plan(plan)
    path = daily_plan_path(root, plan["date"])
    _backup(path, root)
    plan["updated_at"] = now_iso(); plan["revision"] = int(plan.get("revision", 1)) + 1
    atomic_write_json_data(path, plan)


def update_weekly_plan(root: Path, plan: dict[str, Any]) -> None:
    validate_weekly_plan(plan)
    path = weekly_plan_path(root, plan["week_start"])
    _backup(path, root)
    plan["updated_at"] = now_iso(); plan["revision"] = int(plan.get("revision", 1)) + 1
    atomic_write_json_data(path, plan)


def apply_step_updates(root: Path, updates: list[tuple[str, str, str, str]]) -> None:
    """Validate every requested status update before writing any goal file."""
    from .goals import update_step
    for goal_id, milestone_id, step_id, _ in updates:
        find_step(load_goal(root, goal_id), milestone_id, step_id)
    for goal_id, milestone_id, step_id, status in updates:
        update_step(root, goal_id, milestone_id, step_id, {"status": status, "completed_at": now_iso() if status == "done" else None})
