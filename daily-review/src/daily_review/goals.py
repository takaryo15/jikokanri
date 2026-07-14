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
MILESTONE_STATUSES = ("planned", "active", "blocked", "completed", "cancelled")
STEP_STATUSES = ("todo", "doing", "blocked", "done", "cancelled")
GOAL_ID_PATTERN = re.compile(r"goal-[a-f0-9]{8}$")
MILESTONE_ID_PATTERN = re.compile(r"mile-[a-f0-9]{8}$")
STEP_ID_PATTERN = re.compile(r"step-[a-f0-9]{8}$")
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


def new_milestone_id() -> str:
    return f"mile-{uuid.uuid4().hex[:8]}"


def new_step_id() -> str:
    return f"step-{uuid.uuid4().hex[:8]}"


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


def _new_metric(name: str, unit: str, baseline: float, target: float, direction: str = "increase") -> dict[str, Any]:
    if not name.strip() or len(name) > 200 or len(unit) > 80 or direction not in METRIC_DIRECTIONS:
        raise GoalError("マイルストーン定量指標が不正です")
    return {"id": f"metric-{uuid.uuid4().hex[:8]}", "name": name.strip(), "unit": unit, "baseline": baseline, "target": target, "current": baseline, "direction": direction}


def milestones_of(goal: dict[str, Any]) -> list[dict[str, Any]]:
    value = goal.get("milestones")
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise GoalError("milestonesの形式が不正です")
    return value


def find_milestone(goal: dict[str, Any], milestone_id: str) -> dict[str, Any]:
    if not MILESTONE_ID_PATTERN.fullmatch(milestone_id):
        raise GoalError("マイルストーンIDの形式が不正です")
    for milestone in milestones_of(goal):
        if milestone.get("id") == milestone_id:
            return milestone
    raise GoalError("マイルストーンが見つかりません")


def find_step(goal: dict[str, Any], milestone_id: str, step_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    milestone = find_milestone(goal, milestone_id)
    if not STEP_ID_PATTERN.fullmatch(step_id):
        raise GoalError("ステップIDの形式が不正です")
    for step in milestone.get("steps") or []:
        if step.get("id") == step_id:
            return milestone, step
    raise GoalError("ステップが見つかりません")


def _normalize_orders(items: list[dict[str, Any]]) -> None:
    items.sort(key=lambda item: (item.get("order", 10**9), item.get("created_at", ""), item.get("id", "")))
    for index, item in enumerate(items, start=1):
        item["order"] = index


def new_milestone(
    goal: dict[str, Any], *, title: str, description: str | None = None, start_date: str | None = None,
    due_date: str | None = None, qualitative: list[str] | None = None, metric_name: str | None = None,
    metric_unit: str = "", metric_baseline: float | None = None, metric_target: float | None = None,
) -> dict[str, Any]:
    _validate_text(title, "title", required=True, limit=200)
    _validate_text(description, "description")
    _validate_dates(start_date, due_date)
    if (metric_name is None) != (metric_target is None):
        raise GoalError("定量指標は--metric-nameと--metric-targetを同時に指定してください")
    if metric_name is not None and metric_baseline is None:
        metric_baseline = 0
    metrics = [] if metric_name is None else [_new_metric(metric_name, metric_unit, float(metric_baseline), float(metric_target))]
    criteria = [{"id": f"qual-{uuid.uuid4().hex[:8]}", "description": _validate_text(item, "qualitative", required=True, limit=500), "status": "not_met"} for item in (qualitative or [])]
    timestamp = now_iso()
    result = {
        "id": new_milestone_id(), "title": title.strip(), "description": description, "status": "planned",
        "order": len(milestones_of(goal)) + 1, "start_date": start_date, "due_date": due_date,
        "completed_at": None, "progress": {"mode": "automatic", "manual_value": None},
        "qualitative_criteria": criteria, "quantitative_metrics": metrics, "dependencies": [], "steps": [],
        "created_at": timestamp, "updated_at": timestamp, "revision": 1,
    }
    return result


def new_step(*, title: str, description: str | None = None, start_date: str | None = None, due_date: str | None = None, minimum: str | None = None, order: int) -> dict[str, Any]:
    _validate_text(title, "title", required=True, limit=200)
    _validate_text(description, "description")
    _validate_text(minimum, "minimum", limit=500)
    _validate_dates(start_date, due_date)
    timestamp = now_iso()
    return {"id": new_step_id(), "title": title.strip(), "description": description or "", "status": "todo", "order": order, "start_date": start_date, "due_date": due_date, "minimum": minimum, "dependencies": [], "created_at": timestamp, "updated_at": timestamp, "completed_at": None, "revision": 1}


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


def _validate_dependency_graph(items: list[dict[str, Any]], *, item_label: str, pattern: re.Pattern[str]) -> None:
    mapped: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not pattern.fullmatch(item_id):
            raise GoalError(f"{item_label}IDの形式が不正です")
        if item_id in mapped:
            raise GoalError(f"{item_label}IDが重複しています")
        mapped[item_id] = item
    for item_id, item in mapped.items():
        dependencies = item.get("dependencies") or []
        if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
            raise GoalError(f"{item_label}依存関係の形式が不正です")
        if item_id in dependencies:
            raise GoalError(f"{item_label}は自分自身に依存できません")
        if any(value not in mapped for value in dependencies):
            raise GoalError(f"{item_label}の依存先が存在しません")
        seen: set[str] = set()
        stack = list(dependencies)
        while stack:
            current = stack.pop()
            if current == item_id:
                raise GoalError(f"{item_label}依存関係が循環します")
            if current in seen:
                continue
            seen.add(current)
            stack.extend(mapped[current].get("dependencies") or [])


def validate_milestones(goal: dict[str, Any]) -> None:
    milestones = milestones_of(goal)
    if (
        not all(isinstance(item.get("order"), int) and not isinstance(item.get("order"), bool) for item in milestones)
        or sorted(item["order"] for item in milestones) != list(range(1, len(milestones) + 1))
    ):
        raise GoalError("マイルストーンorderが不正です")
    for milestone in milestones:
        if not MILESTONE_ID_PATTERN.fullmatch(str(milestone.get("id", ""))):
            raise GoalError("マイルストーンIDの形式が不正です")
        _validate_text(milestone.get("title"), "マイルストーンtitle", required=True, limit=200)
        _validate_dates(milestone.get("start_date"), milestone.get("due_date"))
        if milestone.get("status") not in MILESTONE_STATUSES:
            raise GoalError("マイルストーンstatusが不正です")
        if milestone.get("status") == "completed" and not isinstance(milestone.get("completed_at"), str):
            raise GoalError("completedマイルストーンにcompleted_atがありません")
        for item in milestone.get("qualitative_criteria") or []:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not isinstance(item.get("description"), str)
                or item.get("status") not in QUALITATIVE_STATUSES
            ):
                raise GoalError("マイルストーン定性指標が不正です")
            _validate_text(item.get("description"), "マイルストーン定性指標", required=True, limit=500)
        for item in milestone.get("quantitative_metrics") or []:
            if not isinstance(item, dict) or item.get("direction") not in METRIC_DIRECTIONS:
                raise GoalError("マイルストーン定量指標が不正です")
            if not isinstance(item.get("name"), str) or not item["name"].strip() or len(item["name"]) > 200:
                raise GoalError("マイルストーン定量指標のnameが不正です")
            if not isinstance(item.get("unit"), str) or len(item["unit"]) > 80:
                raise GoalError("マイルストーン定量指標のunitが不正です")
            if item["direction"] == "boolean":
                if not all(isinstance(item.get(key), bool) for key in ("baseline", "target", "current")):
                    raise GoalError("マイルストーンboolean定量指標の型が不正です")
            elif not all(isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool) for key in ("baseline", "target", "current")):
                raise GoalError("マイルストーン定量指標の型が不正です")
        progress = milestone.get("progress") or {}
        if progress.get("mode", "automatic") not in {"automatic", "manual"}:
            raise GoalError("マイルストーンprogressが不正です")
        manual = progress.get("manual_value")
        if manual is not None and (not isinstance(manual, (int, float)) or isinstance(manual, bool) or not 0 <= manual <= 100):
            raise GoalError("マイルストーンmanual_progressは0〜100にしてください")
        steps = milestone.get("steps") or []
        if not isinstance(steps, list) or not all(isinstance(item, dict) for item in steps):
            raise GoalError("stepsの形式が不正です")
        if (
            not all(isinstance(item.get("order"), int) and not isinstance(item.get("order"), bool) for item in steps)
            or sorted(item["order"] for item in steps) != list(range(1, len(steps) + 1))
        ):
            raise GoalError("ステップorderが不正です")
        for step in steps:
            if not STEP_ID_PATTERN.fullmatch(str(step.get("id", ""))):
                raise GoalError("ステップIDの形式が不正です")
            _validate_text(step.get("title"), "ステップtitle", required=True, limit=200)
            _validate_dates(step.get("start_date"), step.get("due_date"))
            if step.get("status") not in STEP_STATUSES:
                raise GoalError("ステップstatusが不正です")
            if step.get("status") == "done" and not isinstance(step.get("completed_at"), str):
                raise GoalError("doneステップにcompleted_atがありません")
        _validate_dependency_graph(steps, item_label="ステップ", pattern=STEP_ID_PATTERN)
        step_by_id = {step["id"]: step for step in steps}
        for step in steps:
            if step.get("status") == "done" and any(step_by_id[dependency].get("status") != "done" for dependency in step.get("dependencies") or []):
                raise GoalError("完了ステップが未完了の依存先を持っています")
    _validate_dependency_graph(milestones, item_label="マイルストーン", pattern=MILESTONE_ID_PATTERN)
    milestone_by_id = {milestone["id"]: milestone for milestone in milestones}
    for milestone in milestones:
        if milestone.get("status") == "completed" and any(milestone_by_id[dependency].get("status") != "completed" for dependency in milestone.get("dependencies") or []):
            raise GoalError("完了マイルストーンが未完了の依存先を持っています")


def milestone_warnings(goal: dict[str, Any], milestone: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if goal.get("due_date") and milestone.get("due_date") and milestone["due_date"] > goal["due_date"]:
        warnings.append("マイルストーン期限が目標期限より後です")
    if goal.get("start_date") and milestone.get("start_date") and milestone["start_date"] < goal["start_date"]:
        warnings.append("マイルストーン開始日が目標開始日より前です")
    for dependency_id in milestone.get("dependencies") or []:
        dependency = next((item for item in milestones_of(goal) if item.get("id") == dependency_id), None)
        if dependency and dependency.get("due_date") and milestone.get("due_date") and milestone["due_date"] < dependency["due_date"]:
            warnings.append("依存先より前にマイルストーン期限が設定されています")
    return warnings


def step_warnings(milestone: dict[str, Any], step: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if milestone.get("due_date") and step.get("due_date") and step["due_date"] > milestone["due_date"]:
        warnings.append("ステップ期限がマイルストーン期限より後です")
    return warnings


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
    validate_milestones(goal)


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
    milestone_values = [milestone_progress(item)[0] for item in milestones_of(goal) if item.get("status") != "cancelled"]
    milestone_values = [value for value in milestone_values if value is not None]
    if milestone_values:
        return _clamp(sum(milestone_values) / len(milestone_values)), "milestones"
    manual = goal.get("manual_progress")
    if manual is not None:
        return float(manual), "manual"
    return None, "unset"


def step_progress(step: dict[str, Any]) -> float | None:
    return {"todo": 0.0, "doing": 50.0, "blocked": 0.0, "done": 100.0, "cancelled": None}.get(step.get("status"))


def milestone_progress(milestone: dict[str, Any]) -> tuple[float | None, str]:
    progress = milestone.get("progress") or {}
    if progress.get("mode") == "manual" and progress.get("manual_value") is not None:
        return _clamp(float(progress["manual_value"])), "manual"
    indicator_goal = {"qualitative_criteria": milestone.get("qualitative_criteria") or [], "quantitative_metrics": milestone.get("quantitative_metrics") or [], "milestones": [], "manual_progress": None}
    indicator_value, indicator_mode = goal_progress(indicator_goal)
    if indicator_value is not None:
        return indicator_value, "indicators" if indicator_mode == "auto" else indicator_mode
    values = [step_progress(step) for step in milestone.get("steps") or []]
    values = [value for value in values if value is not None]
    if values:
        return _clamp(sum(values) / len(values)), "steps"
    return None, "unset"


def _touch_item(item: dict[str, Any], changed_fields: list[str]) -> None:
    item["revision"] = int(item.get("revision", 1)) + 1
    item["updated_at"] = now_iso()
    item["last_changed_fields"] = changed_fields


def _save_roadmap_change(root: Path, goal: dict[str, Any], *, scope: str, item_id: str, changed_fields: list[str]) -> None:
    existing = goal_path(root, goal["id"])
    goals = [item for item in load_goals(root) if item.get("id") != goal["id"]] + [goal]
    validate_goal(goal, goals)
    _backup_goal(root, existing, goal["id"])
    goal["revision"] = int(goal.get("revision", 1)) + 1
    history = list(goal.get("history") or [])
    history.append({"revision": goal["revision"], "changed_at": now_iso(), "scope": scope, "item_id": item_id, "changed_fields": changed_fields})
    goal["history"] = history[-MAX_HISTORY:]
    goal["updated_at"] = now_iso()
    atomic_write_json_data(existing, goal)


def add_milestone(root: Path, goal_id: str, milestone: dict[str, Any]) -> dict[str, Any]:
    goal = load_goal(root, goal_id)
    milestones = milestones_of(goal)
    milestones.append(milestone)
    _normalize_orders(milestones)
    goal["milestones"] = milestones
    _save_roadmap_change(root, goal, scope="milestone", item_id=milestone["id"], changed_fields=["milestones"])
    return milestone


def update_milestone(root: Path, goal_id: str, milestone_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    goal = load_goal(root, goal_id)
    milestone = find_milestone(goal, milestone_id)
    changed = [key for key, value in changes.items() if milestone.get(key) != value]
    if not changed:
        raise GoalError("変更内容がありません")
    milestone.update(changes)
    _touch_item(milestone, changed)
    _save_roadmap_change(root, goal, scope="milestone", item_id=milestone_id, changed_fields=changed)
    return milestone


def add_step(root: Path, goal_id: str, milestone_id: str, step: dict[str, Any]) -> dict[str, Any]:
    goal = load_goal(root, goal_id)
    milestone = find_milestone(goal, milestone_id)
    steps = milestone.setdefault("steps", [])
    steps.append(step)
    _normalize_orders(steps)
    _save_roadmap_change(root, goal, scope="step", item_id=step["id"], changed_fields=["steps"])
    return step


def update_step(root: Path, goal_id: str, milestone_id: str, step_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    goal = load_goal(root, goal_id)
    _, step = find_step(goal, milestone_id, step_id)
    changed = [key for key, value in changes.items() if step.get(key) != value]
    if not changed:
        raise GoalError("変更内容がありません")
    step.update(changes)
    _touch_item(step, changed)
    _save_roadmap_change(root, goal, scope="step", item_id=step_id, changed_fields=changed)
    return step


def reorder_item(items: list[dict[str, Any]], item_id: str, *, before_id: str | None = None, position: int | None = None) -> None:
    if (before_id is None) == (position is None):
        raise GoalError("--before または --position のどちらか一方を指定してください")
    item = next((value for value in items if value.get("id") == item_id), None)
    if item is None:
        raise GoalError("並べ替え対象が見つかりません")
    items.remove(item)
    if before_id is not None:
        index = next((index for index, value in enumerate(items) if value.get("id") == before_id), None)
        if index is None:
            raise GoalError("--beforeの対象が見つかりません")
    else:
        if position is None or position < 1 or position > len(items) + 1:
            raise GoalError("positionが不正です")
        index = position - 1
    items.insert(index, item)
    _normalize_orders(items)


def reorder_milestone(root: Path, goal_id: str, milestone_id: str, *, before_id: str | None, position: int | None) -> None:
    goal = load_goal(root, goal_id)
    reorder_item(milestones_of(goal), milestone_id, before_id=before_id, position=position)
    _save_roadmap_change(root, goal, scope="milestone", item_id=milestone_id, changed_fields=["order"])


def reorder_step(root: Path, goal_id: str, milestone_id: str, step_id: str, *, before_id: str | None, position: int | None) -> None:
    goal = load_goal(root, goal_id)
    milestone = find_milestone(goal, milestone_id)
    reorder_item(milestone.get("steps") or [], step_id, before_id=before_id, position=position)
    _save_roadmap_change(root, goal, scope="step", item_id=step_id, changed_fields=["order"])


def next_goal_action(goal: dict[str, Any], *, today: str) -> dict[str, Any] | None:
    milestones = milestones_of(goal)
    completed = {item.get("id") for item in milestones if item.get("status") == "completed"}
    candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for milestone in milestones:
        if milestone.get("status") in {"completed", "cancelled", "blocked"} or not set(milestone.get("dependencies") or []) <= completed:
            continue
        done_steps = {step.get("id") for step in milestone.get("steps") or [] if step.get("status") == "done"}
        for step in milestone.get("steps") or []:
            if step.get("status") in {"done", "cancelled", "blocked"} or not set(step.get("dependencies") or []) <= done_steps:
                continue
            due = step.get("due_date") or milestone.get("due_date") or "9999-12-31"
            priority = 0 if step.get("status") == "doing" else 1
            candidates.append(((priority, due, milestone.get("order", 0), step.get("order", 0)), {"milestone": milestone, "step": step}))
        if not milestone.get("steps"):
            due = milestone.get("due_date") or "9999-12-31"
            candidates.append(((1, due, milestone.get("order", 0), 0), {"milestone": milestone, "step": None}))
    return min(candidates, default=(None, None), key=lambda item: item[0])[1]


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
