"""Safe storage and validation for the independent goal-management foundation."""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from .date_utils import parse_date
from .models import now_iso
from .storage import atomic_write_json_data, read_json_file


GOAL_LEVELS = ("vision", "long", "medium", "short")
GOAL_STATUSES = ("draft", "active", "paused", "completed", "cancelled", "archived")
QUALITATIVE_STATUSES = ("not_met", "partially_met", "met")
METRIC_DIRECTIONS = ("increase", "decrease", "maintain", "boolean")
GOAL_ID_PATTERN = re.compile(r"goal-[a-f0-9]{8}$")
MAX_TEXT_LENGTH = 5_000
MAX_HISTORY = 100


class GoalError(ValueError):
    pass


def goals_items_dir(root: Path) -> Path:
    return root / "data" / "goals" / "items"


def goals_backup_dir(root: Path) -> Path:
    return root / "data" / "backups" / "goals"


def validate_goal_id(goal_id: str) -> str:
    if not GOAL_ID_PATTERN.fullmatch(goal_id):
        raise GoalError("目標IDの形式が不正です")
    return goal_id


def goal_path(root: Path, goal_id: str) -> Path:
    return goals_items_dir(root) / f"{validate_goal_id(goal_id)}.json"


def new_goal_id() -> str:
    return f"goal-{uuid.uuid4().hex[:8]}"


def _validate_text(value: Any, label: str, *, required: bool = False, limit: int = MAX_TEXT_LENGTH) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise GoalError(f"{label}を入力してください")
    if len(value) > limit:
        raise GoalError(f"{label}は{limit}文字以内にしてください")
    return value.strip() if required else value


def _validate_dates(start_date: str | None, due_date: str | None) -> None:
    for label, value in (("start_date", start_date), ("due_date", due_date)):
        if value is not None:
            if not isinstance(value, str):
                raise GoalError(f"{label}はYYYY-MM-DD形式にしてください")
            try:
                parse_date(value)
            except ValueError as exc:
                raise GoalError(f"{label}はYYYY-MM-DD形式にしてください") from exc
    if start_date and due_date and parse_date(due_date) < parse_date(start_date):
        raise GoalError("due_dateはstart_date以降にしてください")


def parse_metric(specification: str) -> dict[str, Any]:
    values = specification.split("|")
    if len(values) != 5:
        raise GoalError("--metric は name|unit|baseline|target|direction 形式にしてください")
    name, unit, baseline_text, target_text, direction = (value.strip() for value in values)
    if not name or len(name) > 200 or len(unit) > 80:
        raise GoalError("metricのnameまたはunitが不正です")
    if direction not in METRIC_DIRECTIONS:
        raise GoalError("metricのdirectionが不正です")
    if direction == "boolean":
        truth = {"true": True, "false": False, "1": True, "0": False}
        if baseline_text.lower() not in truth or target_text.lower() not in truth:
            raise GoalError("boolean metricのbaselineとtargetはtrueまたはfalseにしてください")
        baseline, target = truth[baseline_text.lower()], truth[target_text.lower()]
    else:
        try:
            baseline, target = float(baseline_text), float(target_text)
        except ValueError as exc:
            raise GoalError("metricのbaselineとtargetは数値にしてください") from exc
    return {
        "id": f"metric-{uuid.uuid4().hex[:8]}", "name": name, "unit": unit,
        "baseline": baseline, "target": target, "current": baseline, "direction": direction,
    }


def new_goal(
    *, title: str, level: str, description: str | None = None, category: str | None = None,
    start_date: str | None = None, due_date: str | None = None, parent_id: str | None = None,
    qualitative: list[str] | None = None, metrics: list[str] | None = None,
) -> dict[str, Any]:
    _validate_text(title, "title", required=True, limit=200)
    if level not in GOAL_LEVELS:
        raise GoalError("levelはvision、long、medium、shortのいずれかにしてください")
    description = _validate_text(description, "description")
    category = _validate_text(category, "category", limit=200)
    _validate_dates(start_date, due_date)
    if parent_id is not None:
        validate_goal_id(parent_id)
    qualitative_items = []
    for item in qualitative or []:
        description_item = _validate_text(item, "qualitative", required=True, limit=500)
        qualitative_items.append({"id": f"qual-{uuid.uuid4().hex[:8]}", "description": description_item, "status": "not_met"})
    metric_items = [parse_metric(item) for item in (metrics or [])]
    timestamp = now_iso()
    return {
        "id": new_goal_id(), "title": title.strip(), "description": description, "level": level,
        "category": category, "status": "active", "parent_id": parent_id,
        "start_date": start_date, "due_date": due_date,
        "qualitative_criteria": qualitative_items, "quantitative_metrics": metric_items,
        "milestones": [], "manual_progress": None, "created_at": timestamp, "updated_at": timestamp,
        "completed_at": None, "archived_at": None, "revision": 1, "history": [],
    }


def load_goal(root: Path, goal_id: str) -> dict[str, Any]:
    path = goal_path(root, goal_id)
    if not path.is_file():
        raise GoalError("目標が見つかりません")
    value = read_json_file(path)
    if not isinstance(value, dict):
        raise GoalError("目標JSONの形式が不正です")
    return value


def load_goals(root: Path) -> list[dict[str, Any]]:
    directory = goals_items_dir(root)
    if not directory.is_dir():
        return []
    values: list[dict[str, Any]] = []
    for path in sorted(directory.glob("goal-*.json")):
        value = read_json_file(path)
        if not isinstance(value, dict):
            raise GoalError(f"目標JSONの形式が不正です: {path.name}")
        values.append(value)
    return values


def _goal_map(goals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for goal in goals:
        goal_id = goal.get("id")
        if not isinstance(goal_id, str) or not GOAL_ID_PATTERN.fullmatch(goal_id):
            raise GoalError("目標IDの形式が不正です")
        if goal_id in result:
            raise GoalError("目標IDが重複しています")
        result[goal_id] = goal
    return result


def validate_relationships(goals: list[dict[str, Any]]) -> None:
    mapped = _goal_map(goals)
    # Prefer the direct, actionable self-parent error even when another child
    # happens to be visited first by filesystem ordering.
    for goal_id, goal in mapped.items():
        if goal.get("parent_id") == goal_id:
            raise GoalError("自分自身を親にできません")
    for goal_id, goal in mapped.items():
        parent_id = goal.get("parent_id")
        if parent_id is None:
            continue
        if not isinstance(parent_id, str) or parent_id not in mapped:
            raise GoalError("親目標が存在しません")
        parent = mapped[parent_id]
        if parent.get("status") == "archived":
            raise GoalError("archived目標を親にできません")
        if GOAL_LEVELS.index(parent.get("level")) > GOAL_LEVELS.index(goal.get("level")):
            raise GoalError("親子のlevel関係が不自然です")
        seen = {goal_id}
        current = parent_id
        while current is not None:
            if current in seen:
                raise GoalError("親子関係が循環します")
            seen.add(current)
            current = mapped[current].get("parent_id")


def validate_goal(goal: dict[str, Any], goals: list[dict[str, Any]]) -> None:
    _goal_map(goals)
    validate_goal_id(goal.get("id", ""))
    _validate_text(goal.get("title"), "title", required=True, limit=200)
    _validate_text(goal.get("description"), "description")
    _validate_text(goal.get("category"), "category", limit=200)
    if goal.get("level") not in GOAL_LEVELS:
        raise GoalError("levelが不正です")
    if goal.get("status") not in GOAL_STATUSES:
        raise GoalError("statusが不正です")
    _validate_dates(goal.get("start_date"), goal.get("due_date"))
    if goal.get("status") == "completed" and not isinstance(goal.get("completed_at"), str):
        raise GoalError("completedなのにcompleted_atがありません")
    if goal.get("status") == "archived" and not isinstance(goal.get("archived_at"), str):
        raise GoalError("archivedなのにarchived_atがありません")
    for item in goal.get("qualitative_criteria", []):
        if not isinstance(item, dict) or item.get("status") not in QUALITATIVE_STATUSES:
            raise GoalError("定性指標の形式またはstatusが不正です")
    for item in goal.get("quantitative_metrics", []):
        if not isinstance(item, dict) or item.get("direction") not in METRIC_DIRECTIONS:
            raise GoalError("定量指標の形式またはdirectionが不正です")
        if item["direction"] == "boolean":
            if not all(isinstance(item.get(key), bool) for key in ("baseline", "target", "current")):
                raise GoalError("boolean定量指標の型が不正です")
        elif not all(isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool) for key in ("baseline", "target", "current")):
            raise GoalError("定量指標の型が不正です")
    manual = goal.get("manual_progress")
    if manual is not None and (not isinstance(manual, (int, float)) or isinstance(manual, bool) or not 0 <= manual <= 100):
        raise GoalError("manual_progressは0〜100にしてください")
    progress, _ = goal_progress(goal)
    if progress is not None and not 0 <= progress <= 100:
        raise GoalError("progressが範囲外です")
    validate_relationships(goals)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _metric_progress(metric: dict[str, Any]) -> float | None:
    direction = metric["direction"]
    baseline, target, current = metric["baseline"], metric["target"], metric["current"]
    if direction == "boolean":
        return 100.0 if current == target else 0.0
    if direction == "maintain":
        return None
    denominator = (target - baseline) if direction == "increase" else (baseline - target)
    numerator = (current - baseline) if direction == "increase" else (baseline - current)
    if denominator == 0:
        return 100.0 if current == target else 0.0
    return _clamp(100 * numerator / denominator)


def goal_progress(goal: dict[str, Any]) -> tuple[float | None, str]:
    values: list[float] = []
    for item in goal.get("qualitative_criteria") or []:
        values.append({"not_met": 0.0, "partially_met": 50.0, "met": 100.0}[item.get("status", "not_met")])
    for item in goal.get("quantitative_metrics") or []:
        value = _metric_progress(item)
        if value is not None:
            values.append(value)
    if values:
        return _clamp(sum(values) / len(values)), "auto"
    manual = goal.get("manual_progress")
    if manual is not None:
        return float(manual), "manual"
    return None, "unset"


def _backup_goal(root: Path, path: Path, goal_id: str) -> Path:
    destination = goals_backup_dir(root) / f"{goal_id}_{now_iso().replace(':', '').replace('+', '_')}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return destination


def save_goal(root: Path, goal: dict[str, Any], *, changed_fields: list[str], backup: bool) -> Path:
    existing = goal_path(root, goal["id"])
    goals = [item for item in load_goals(root) if item.get("id") != goal["id"]] + [goal]
    validate_goal(goal, goals)
    if backup and existing.exists():
        _backup_goal(root, existing, goal["id"])
    if backup:
        goal["revision"] = int(goal.get("revision", 1)) + 1
        history = list(goal.get("history") or [])
        history.append({"revision": goal["revision"], "changed_at": now_iso(), "changed_fields": changed_fields})
        goal["history"] = history[-MAX_HISTORY:]
    goal["updated_at"] = now_iso()
    atomic_write_json_data(existing, goal)
    return existing


def children_of(goals: list[dict[str, Any]], goal_id: str) -> list[dict[str, Any]]:
    return [goal for goal in goals if goal.get("parent_id") == goal_id]


def edit_goal(root: Path, goal_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    goal = load_goal(root, goal_id)
    changed_fields = [key for key, value in changes.items() if goal.get(key) != value]
    if not changed_fields:
        raise GoalError("変更内容がありません")
    goal.update({key: value for key, value in changes.items()})
    save_goal(root, goal, changed_fields=changed_fields, backup=True)
    return goal


def set_goal_status(root: Path, goal_id: str, status: str) -> dict[str, Any]:
    if status not in GOAL_STATUSES:
        raise GoalError("statusが不正です")
    goal = load_goal(root, goal_id)
    changes: dict[str, Any] = {"status": status}
    if status == "completed":
        changes["completed_at"] = goal.get("completed_at") or now_iso()
    elif goal.get("completed_at") is not None:
        changes["completed_at"] = None
    if status == "archived":
        changes["archived_at"] = goal.get("archived_at") or now_iso()
    return edit_goal(root, goal_id, changes)


def archive_goal(root: Path, goal_id: str) -> dict[str, Any]:
    return set_goal_status(root, goal_id, "archived")


def goal_summary(root: Path, *, today: str) -> dict[str, Any]:
    goals = load_goals(root)
    active = [goal for goal in goals if goal.get("status") == "active"]
    near: list[tuple[int, dict[str, Any]]] = []
    for goal in active:
        due = goal.get("due_date")
        if isinstance(due, str):
            try:
                days = (parse_date(due) - parse_date(today)).days
            except ValueError:
                continue
            if 0 <= days <= 7:
                near.append((days, goal))
    return {"active_count": len(active), "near_due": [goal for _, goal in sorted(near, key=lambda pair: (pair[0], pair[1].get("title", "")))[:3]]}
