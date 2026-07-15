"""Goal evaluation snapshots built from existing daily and planning records."""
from __future__ import annotations

import calendar
import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from .date_utils import date_range, month_range_for, parse_date, week_range_for
from .goals import GoalError, goal_progress, load_goals, milestone_progress, milestones_of
from .models import now_iso
from .planning import load_daily_plan, load_weekly_plan
from .reporting import build_report
from .storage import atomic_write_json_data, daily_path, read_json_file


EVALUATION_STATUSES = ("draft", "approved")
GOAL_EVALUATION_STATUSES = ("ahead", "on_track", "slightly_delayed", "delayed", "blocked", "inactive", "completed")
TREND_STATUSES = ("improving", "stable", "declining", "stalled", "volatile")
DEADLINE_RISK_THRESHOLDS = {"medium": 1.0, "high": 1.5, "critical": 2.0}


class EvaluationError(ValueError):
    pass


def weekly_evaluations_dir(root: Path) -> Path:
    return root / "data" / "evaluations" / "weekly"


def monthly_evaluations_dir(root: Path) -> Path:
    return root / "data" / "evaluations" / "monthly"


def evaluations_backup_dir(root: Path) -> Path:
    return root / "data" / "backups" / "evaluations"


def weekly_evaluation_path(root: Path, week_start: str) -> Path:
    start, end = week_range_for(week_start)
    return weekly_evaluations_dir(root) / f"{start}_{end}.json"


def monthly_evaluation_path(root: Path, month: str) -> Path:
    _validate_month(month)
    return monthly_evaluations_dir(root) / f"{month}.json"


def _validate_month(month: str) -> str:
    try:
        parsed = parse_date(f"{month}-01")
    except ValueError as exc:
        raise EvaluationError("monthはYYYY-MM形式で指定してください") from exc
    if parsed.strftime("%Y-%m") != month:
        raise EvaluationError("monthはYYYY-MM形式で指定してください")
    return month


def _backup(path: Path, root: Path) -> None:
    if not path.exists():
        return
    target = evaluations_backup_dir(root) / f"{path.stem}_{now_iso().replace(':', '').replace('+', '_')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def _load_daily(root: Path, day: str) -> dict[str, Any]:
    path = daily_path(root, day)
    if not path.exists():
        return {}
    value = read_json_file(path)
    if not isinstance(value, dict):
        raise EvaluationError(f"日次JSONの形式が不正です: {path.name}")
    return value


def _result_counts(entry: dict[str, Any]) -> dict[str, int]:
    counts = {"completed": 0, "partial": 0, "not_completed": 0}
    for item in entry.get("task_results") or []:
        status = item.get("status") if isinstance(item, dict) else None
        if status == "completed": counts["completed"] += 1
        elif status == "partial": counts["partial"] += 1
        elif status in {"minimum_only", "not_started", "skipped"}: counts["not_completed"] += 1
    if not entry.get("task_results"):
        for item in (entry.get("structured_review") or {}).get("today_main") or []:
            status = item.get("status") if isinstance(item, dict) else None
            if status == "完了": counts["completed"] += 1
            elif status in {"一部進んだ", "一部進行"}: counts["partial"] += 1
            elif status: counts["not_completed"] += 1
    return counts


def _minimum_achieved(entry: dict[str, Any]) -> bool:
    if any(item.get("minimum_line_achieved") is True for item in entry.get("task_results") or [] if isinstance(item, dict)):
        return True
    minimums = (entry.get("structured_review") or {}).get("minimum_line") or {}
    return any(value == "達成" for value in minimums.values()) if isinstance(minimums, dict) else False


def _period_activity(root: Path, start: str, end: str) -> dict[str, Any]:
    summary = {
        "recorded_days": 0, "approved_days": 0, "planned_days": 0, "planned_main_count": 0,
        "completed_main_count": 0, "partial_main_count": 0, "not_completed_main_count": 0,
        "minimum_achieved_days": 0, "goal_link_count": 0, "goal_progress_applied_count": 0,
        "carryover_count": 0, "replanned_count": 0, "overloaded_days": 0,
        "reflection_notes": [], "breakdown_causes": [],
    }
    per_goal: dict[str, dict[str, int]] = {}
    for day in date_range(start, end):
        entry = _load_daily(root, day)
        if entry.get("raw_log") or entry.get("structured_review") or entry.get("diary"):
            summary["recorded_days"] += 1
        review = entry.get("structured_review") or {}
        for note in review.get("what_went_well") or []:
            if isinstance(note, str) and note not in summary["reflection_notes"]: summary["reflection_notes"].append(note)
        for cause in review.get("breakdown_causes") or []:
            if isinstance(cause, str) and cause not in summary["breakdown_causes"]: summary["breakdown_causes"].append(cause)
        result = _result_counts(entry)
        summary["completed_main_count"] += result["completed"]
        summary["partial_main_count"] += result["partial"]
        summary["not_completed_main_count"] += result["not_completed"]
        if _minimum_achieved(entry): summary["minimum_achieved_days"] += 1
        try:
            plan = load_daily_plan(root, day)
        except (OSError, ValueError):
            plan = None
        if not plan: continue
        summary["planned_days"] += 1
        if plan.get("status") == "approved": summary["approved_days"] += 1
        main = plan.get("main_candidates") or []
        summary["planned_main_count"] += len(main)
        if len(main) >= 3 and plan.get("other_tasks"): summary["overloaded_days"] += 1
        summary["goal_link_count"] += len(plan.get("goal_links") or [])
        summary["goal_progress_applied_count"] += sum(len(item.get("updates") or []) for item in plan.get("progress_applications") or [] if isinstance(item, dict))
        for item in main:
            if not isinstance(item, dict) or not isinstance(item.get("goal_id"), str): continue
            values = per_goal.setdefault(item["goal_id"], {"planned_items": 0, "linked_items": 0})
            values["planned_items"] += 1
        for link in plan.get("goal_links") or []:
            if isinstance(link, dict) and isinstance(link.get("goal_id"), str):
                per_goal.setdefault(link["goal_id"], {"planned_items": 0, "linked_items": 0})["linked_items"] += 1
    try:
        weekly_plan = load_weekly_plan(root, start)
    except (OSError, ValueError):
        weekly_plan = None
    if weekly_plan:
        summary["carryover_count"] = len(weekly_plan.get("carryovers") or [])
    replan_dir = root / "data" / "replans"
    for path in sorted(replan_dir.glob("replan-*.json")) if replan_dir.is_dir() else []:
        try: replan = read_json_file(path)
        except (OSError, ValueError): continue
        if isinstance(replan, dict) and replan.get("status") == "applied" and isinstance(replan.get("applied_at"), str) and start <= replan["applied_at"][:10] <= end:
            summary["replanned_count"] += 1
    return {"summary": summary, "per_goal": per_goal}


def _goal_snapshot(goal: dict[str, Any], *, period_end: str, activity: dict[str, int], start_progress_override: float | None = None) -> dict[str, Any]:
    progress, _ = goal_progress(goal)
    end_progress = round(progress or 0, 2)
    start_progress = end_progress if start_progress_override is None else round(start_progress_override, 2)
    completed_steps = partial_steps = not_started_steps = overdue_steps = blocked_steps = 0
    milestone_values: list[float] = []
    for milestone in milestones_of(goal):
        value, _ = milestone_progress(milestone)
        if value is not None and milestone.get("status") != "cancelled": milestone_values.append(value)
        for step in milestone.get("steps") or []:
            status = step.get("status")
            if status == "done": completed_steps += 1
            elif status == "doing": partial_steps += 1
            elif status == "todo": not_started_steps += 1
            elif status == "blocked": blocked_steps += 1
            if step.get("due_date") and step["due_date"] < period_end and status not in {"done", "cancelled"}: overdue_steps += 1
    planned_items = activity.get("planned_items", 0)
    completed_items = min(planned_items, completed_steps)
    remaining_steps = partial_steps + not_started_steps + blocked_steps
    remaining_days = max(0, (parse_date(goal["due_date"]) - parse_date(period_end)).days) if goal.get("due_date") else None
    if planned_items and remaining_days is not None:
        observed_speed = completed_items / 7
        required_speed = remaining_steps / max(1, remaining_days)
        ratio = required_speed / observed_speed if observed_speed else float("inf")
        risk = (
            "low_risk" if ratio <= DEADLINE_RISK_THRESHOLDS["medium"]
            else "medium_risk" if ratio <= DEADLINE_RISK_THRESHOLDS["high"]
            else "high_risk" if ratio < DEADLINE_RISK_THRESHOLDS["critical"]
            else "critical"
        )
        forecast = {"status": risk, "remaining_steps": remaining_steps, "remaining_days": remaining_days, "observed_steps_per_day": round(observed_speed, 3), "required_steps_per_day": round(required_speed, 3), "basis": "期間内の計画項目と現在step状態"}
    else:
        forecast = {"status": "unavailable", "label": "予測不能", "remaining_steps": remaining_steps, "remaining_days": remaining_days, "reason": "期間内の計画実績または目標期限が不足しています"}
    if goal.get("status") == "completed": status = "completed"; reason = "目標がcompletedです"
    elif blocked_steps: status = "blocked"; reason = f"blockedステップが{blocked_steps}件あります"
    elif planned_items == 0 and activity.get("linked_items", 0) == 0: status = "inactive"; reason = "期間内の計画・リンク実績がありません"
    elif overdue_steps >= 2: status = "delayed"; reason = f"overdueステップが{overdue_steps}件あります"
    elif overdue_steps == 1: status = "slightly_delayed"; reason = "overdueステップが1件あります"
    elif planned_items and completed_items >= planned_items: status = "ahead"; reason = "計画項目をすべて完了しoverdueがありません"
    else: status = "on_track"; reason = "重大なoverdueやblockがありません"
    return {
        "goal_id": goal["id"], "title": goal["title"], "category": goal.get("category"),
        "start_progress": start_progress, "end_progress": end_progress, "progress_delta": round(end_progress - start_progress, 2),
        "completed_steps": completed_steps, "partial_steps": partial_steps, "not_started_steps": not_started_steps,
        "overdue_steps": overdue_steps, "blocked_steps": blocked_steps,
        "milestone_progress": round(sum(milestone_values) / len(milestone_values), 2) if milestone_values else None,
        "planned_items": planned_items, "completed_items": completed_items, "status": status,
        "evidence": [reason, f"期間末進捗{end_progress:g}%"], "status_reason": reason,
        "deadline_forecast": forecast,
    }


def _diagnostics(summary: dict[str, Any], goal_values: list[dict[str, Any]], *, period_days: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    def add(code: str, severity: str, message: str, evidence: list[Any], goal_id: str | None = None) -> None:
        result.append({"code": code, "severity": severity, "message": message, "goal_id": goal_id, "evidence": evidence})
    planned = summary["planned_main_count"]
    completed = summary["completed_main_count"]
    incomplete = summary["partial_main_count"] + summary["not_completed_main_count"]
    if summary["overloaded_days"] >= 2: add("overloaded", "warning", "予定過多の日が複数あります", [summary["overloaded_days"]])
    if summary["planned_days"] < max(1, period_days // 3): add("underplanned", "info", "計画作成日数が少ないです", [summary["planned_days"], period_days])
    if summary["carryover_count"] >= 3: add("frequent_carryover", "warning", "繰越が3件以上あります", [summary["carryover_count"]])
    if summary["minimum_achieved_days"] < max(1, summary["recorded_days"] // 2): add("minimum_not_working", "warning", "最低ライン達成が記録日の半分未満です", [summary["minimum_achieved_days"], summary["recorded_days"]])
    if summary["recorded_days"] < max(1, period_days // 2): add("low_recording_rate", "warning", "振り返り記録率が低いです", [summary["recorded_days"], period_days])
    if planned and completed / planned < .5: add("low_completion_rate", "warning", "計画Main完了率が50%未満です", [completed, planned])
    if incomplete >= 3: add("too_many_main_items", "warning", "未完了または一部進行のMainが多いです", [incomplete])
    active = [item for item in goal_values if item["status"] not in {"completed"}]
    if len(active) > 5: add("too_many_goals", "warning", "同時に追跡する目標が多すぎます", [len(active)])
    for item in goal_values:
        if item["status"] == "inactive": add("goal_inactive", "warning", f"{item['title']}に期間内実績がありません", item["evidence"], item["goal_id"])
        if item["status"] in {"delayed", "slightly_delayed"}: add("goal_delayed", "warning", f"{item['title']}が遅れています", item["evidence"], item["goal_id"])
        if item["blocked_steps"]: add("dependency_blocked", "warning", f"{item['title']}にblocked項目があります", [item["blocked_steps"]], item["goal_id"])
        if item["overdue_steps"]: add("deadline_risk", "warning", f"{item['title']}に期限超過があります", [item["overdue_steps"]], item["goal_id"])
    return result


def _recommendations(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping = {
        "overloaded": ("reduce_daily_load", "日次Mainを減らし、1/3の余白を確保する"),
        "low_completion_rate": ("reduce_daily_load", "日次計画の件数を減らす"),
        "minimum_not_working": ("change_minimum", "最低ラインをさらに小さくする"),
        "goal_inactive": ("review_goal_definition", "目標の次アクションまたは継続可否を確認する"),
        "goal_delayed": ("extend_deadline", "期限またはスコープを見直す"),
        "dependency_blocked": ("remove_blocker", "依存先を優先してblockerを確認する"),
        "deadline_risk": ("reduce_scope", "期限内に必要な範囲へスコープを絞る"),
    }
    values = []
    for item in diagnostics:
        if item["code"] in mapping:
            kind, title = mapping[item["code"]]
            identity = json.dumps(
                [item["code"], item.get("goal_id"), item["message"], item["evidence"]],
                ensure_ascii=False,
                sort_keys=True,
            )
            recommendation_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
            values.append({"id": f"recommendation-{recommendation_id}", "type": kind, "goal_id": item.get("goal_id"), "title": title, "reason": item["message"], "evidence": item["evidence"]})
    return values


def generate_weekly_evaluation(root: Path, day: str) -> dict[str, Any]:
    start, end = week_range_for(day)
    activity = _period_activity(root, start, end)
    goals = [goal for goal in load_goals(root) if goal.get("status") not in {"archived", "cancelled"}]
    previous_start = (parse_date(start) - timedelta(days=7)).isoformat()
    try: previous = load_weekly_evaluation(root, previous_start)
    except (OSError, ValueError): previous = None
    previous_progress = {item["goal_id"]: float(item.get("end_progress") or 0) for item in (previous or {}).get("goal_evaluations") or []}
    goal_values = [_goal_snapshot(goal, period_end=end, activity=activity["per_goal"].get(goal["id"], {}), start_progress_override=previous_progress.get(goal["id"])) for goal in goals]
    diagnostics = _diagnostics(activity["summary"], goal_values, period_days=7)
    activity["summary"]["unplanned_completed_count"] = max(0, activity["summary"]["completed_main_count"] - activity["summary"]["planned_main_count"])
    timestamp = now_iso()
    return {"week_start": start, "week_end": end, "status": "draft", "summary": activity["summary"], "goal_evaluations": goal_values, "diagnostics": diagnostics, "recommendations": _recommendations(diagnostics), "created_at": timestamp, "updated_at": timestamp, "approved_at": None, "revision": 1}


def _weekly_starts_in_month(month: str) -> list[str]:
    start, end = month_range_for(f"{month}-01")
    seen: list[str] = []
    for day in date_range(start, end):
        week_start, _ = week_range_for(day)
        if week_start not in seen: seen.append(week_start)
    return seen


def _trend(deltas: list[float]) -> str:
    if not deltas or all(value == 0 for value in deltas): return "stalled"
    if all(value >= 0 for value in deltas) and sum(deltas) > 0: return "improving"
    if all(value <= 0 for value in deltas) and sum(deltas) < 0: return "declining"
    if max(deltas) - min(deltas) >= 20: return "volatile"
    return "stable"


def generate_monthly_evaluation(root: Path, month: str) -> dict[str, Any]:
    month = _validate_month(month)
    start, end = month_range_for(f"{month}-01")
    activity = _period_activity(root, start, end)
    weekly: list[dict[str, Any]] = []
    deltas_by_goal: dict[str, list[float]] = {}
    weekly_values_by_goal: dict[str, list[dict[str, Any]]] = {}
    for week_start in _weekly_starts_in_month(month):
        try:
            value = load_weekly_evaluation(root, week_start)
        except (OSError, ValueError):
            continue
        if not value:
            continue
        weekly.append({"week_start": value["week_start"], "week_end": value["week_end"], "summary": value["summary"]})
        for item in value.get("goal_evaluations") or []:
            deltas_by_goal.setdefault(item["goal_id"], []).append(float(item.get("progress_delta") or 0))
            weekly_values_by_goal.setdefault(item["goal_id"], []).append(item)
    goals = [goal for goal in load_goals(root) if goal.get("status") != "archived"]
    goal_values = []
    for goal in goals:
        weekly_goal_values = weekly_values_by_goal.get(goal["id"], [])
        start_progress = float(weekly_goal_values[0].get("start_progress") or 0) if weekly_goal_values else None
        snapshot = _goal_snapshot(
            goal,
            period_end=end,
            activity=activity["per_goal"].get(goal["id"], {}),
            start_progress_override=start_progress,
        )
        if weekly_goal_values:
            snapshot["end_progress"] = float(weekly_goal_values[-1].get("end_progress") or 0)
            snapshot["progress_delta"] = round(snapshot["end_progress"] - snapshot["start_progress"], 2)
        deltas = deltas_by_goal.get(goal["id"], [])
        snapshot["trend"] = _trend(deltas); snapshot["weekly_progress_deltas"] = deltas
        snapshot["stalled_weeks"] = sum(value == 0 for value in deltas)
        goal_values.append(snapshot)
    diagnostics = _diagnostics(activity["summary"], goal_values, period_days=calendar.monthrange(parse_date(start).year, parse_date(start).month)[1])
    timestamp = now_iso()
    summary = dict(activity["summary"], weekly_evaluation_count=len(weekly), active_goal_count=sum(goal.get("status") == "active" for goal in goals), completed_goal_count=sum(goal.get("status") == "completed" for goal in goals), paused_cancelled_goal_count=sum(goal.get("status") in {"paused", "cancelled"} for goal in goals))
    return {"month": month, "status": "draft", "summary": summary, "goal_evaluations": goal_values, "weekly_trends": weekly, "diagnostics": diagnostics, "recommendations": _recommendations(diagnostics), "created_at": timestamp, "updated_at": timestamp, "approved_at": None, "revision": 1}


def validate_evaluation(value: Any, *, period_type: str) -> None:
    if not isinstance(value, dict) or value.get("status") not in EVALUATION_STATUSES:
        raise EvaluationError("評価JSONまたはstatusが不正です")
    if period_type == "week":
        start, end = week_range_for(value.get("week_start", ""))
        if value.get("week_start") != start or value.get("week_end") != end: raise EvaluationError("週次評価の境界が不正です")
    else:
        _validate_month(value.get("month", ""))
    if value["status"] == "approved" and not isinstance(value.get("approved_at"), str): raise EvaluationError("承認済み評価にapproved_atがありません")
    if not isinstance(value.get("summary"), dict) or not isinstance(value.get("goal_evaluations"), list): raise EvaluationError("評価summaryまたはgoal_evaluationsが不正です")
    for item in value["goal_evaluations"]:
        if not isinstance(item, dict) or item.get("status") not in GOAL_EVALUATION_STATUSES: raise EvaluationError("目標評価statusが不正です")
        if not isinstance(item.get("evidence"), list): raise EvaluationError("目標評価evidenceがありません")
        if not all(isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool) for key in ("start_progress", "end_progress", "progress_delta")):
            raise EvaluationError("進捗スナップショットが不正です")
        if round(float(item["end_progress"]) - float(item["start_progress"]), 2) != round(float(item["progress_delta"]), 2):
            raise EvaluationError("progress deltaがスナップショットと一致しません")
        if period_type == "month" and item.get("trend") is not None and item.get("trend") not in TREND_STATUSES: raise EvaluationError("月次trendが不正です")
        forecast = item.get("deadline_forecast")
        if forecast is not None and (not isinstance(forecast, dict) or forecast.get("status") not in {"low_risk", "medium_risk", "high_risk", "critical", "unavailable"}): raise EvaluationError("期限リスク予測が不正です")
    if period_type == "month":
        weeks = value.get("weekly_trends") or []
        starts = [item.get("week_start") for item in weeks if isinstance(item, dict)]
        if len(starts) != len(set(starts)):
            raise EvaluationError("月次評価に重複した週があります")


def load_weekly_evaluation(root: Path, week_start: str) -> dict[str, Any] | None:
    path = weekly_evaluation_path(root, week_start)
    if not path.exists(): return None
    value = read_json_file(path); validate_evaluation(value, period_type="week"); return value


def load_monthly_evaluation(root: Path, month: str) -> dict[str, Any] | None:
    path = monthly_evaluation_path(root, month)
    if not path.exists(): return None
    value = read_json_file(path); validate_evaluation(value, period_type="month"); return value


def save_evaluation(root: Path, value: dict[str, Any], *, period_type: str) -> Path:
    validate_evaluation(value, period_type=period_type)
    path = weekly_evaluation_path(root, value["week_start"]) if period_type == "week" else monthly_evaluation_path(root, value["month"])
    if path.exists():
        current = read_json_file(path)
        if current.get("status") == "approved": raise EvaluationError("承認済み評価は上書きできません")
        _backup(path, root); value["created_at"] = current.get("created_at", value["created_at"]); value["revision"] = int(current.get("revision", 1)) + 1
    value["updated_at"] = now_iso(); atomic_write_json_data(path, value); return path


def approve_evaluation(root: Path, *, week: str | None = None, month: str | None = None) -> dict[str, Any]:
    if (week is None) == (month is None): raise EvaluationError("--week または --month のどちらか一方を指定してください")
    value = load_weekly_evaluation(root, week or "") if week else load_monthly_evaluation(root, month or "")
    if not value: raise EvaluationError("評価が見つかりません")
    period_type = "week" if week else "month"; path = weekly_evaluation_path(root, week or "") if week else monthly_evaluation_path(root, month or "")
    _backup(path, root); value["status"] = "approved"; value["approved_at"] = now_iso(); value["updated_at"] = now_iso(); value["revision"] = int(value.get("revision", 1)) + 1
    validate_evaluation(value, period_type=period_type); atomic_write_json_data(path, value); return value


def attach_coach_analysis(root: Path, evaluation: dict[str, Any], analysis: dict[str, Any], *, period_type: str) -> None:
    validate_evaluation(evaluation, period_type=period_type)
    path = weekly_evaluation_path(root, evaluation["week_start"]) if period_type == "week" else monthly_evaluation_path(root, evaluation["month"])
    _backup(path, root); evaluation["coach_analysis"] = analysis; evaluation["coach_received_at"] = now_iso(); evaluation["updated_at"] = now_iso(); evaluation["revision"] = int(evaluation.get("revision", 1)) + 1
    atomic_write_json_data(path, evaluation)
