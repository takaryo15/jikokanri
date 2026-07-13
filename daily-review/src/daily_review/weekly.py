from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .date_utils import date_range, week_range_for
from .storage import load_daily, read_json_file


CARRYOVER_STATUSES = {"partial", "minimum_only", "not_started"}


def _all_daily_entries(root) -> list[dict[str, Any]]:
    daily_dir = root / "data" / "daily"
    if not daily_dir.exists():
        return []
    return [read_json_file(path) for path in sorted(daily_dir.glob("*.json"))]


def build_weekly_summary(root, day: str) -> dict[str, Any]:
    start, end = week_range_for(day)
    entries = []
    warnings: list[str] = []
    for current in date_range(start, end):
        entry = load_daily(root, current)
        if entry:
            entries.append(entry)

    main_status_counts: dict[str, Counter[str]] = defaultdict(Counter)
    minimum_total = 0
    minimum_achieved = 0
    what_went_well: list[str] = []
    breakdown_counter: Counter[str] = Counter()
    daily_changes: list[dict[str, str]] = []
    pending_proposal_days: list[str] = []
    approved_plan_days = 0
    task_total = 0
    task_completed = 0
    task_minimum_total = 0
    task_minimum_achieved = 0
    task_status_counts: Counter[str] = Counter()
    task_area_status_counts: dict[str, Counter[str]] = defaultdict(Counter)
    unrecorded_count = 0
    carryover_count = 0
    incomplete_by_task: Counter[str] = Counter()

    for entry in entries:
        review = entry.get("structured_review") or {}
        for item in review.get("today_main") or []:
            main_status_counts[item.get("area", "未設定")][item.get("status", "未設定")] += 1

        for area, status in (review.get("minimum_line") or {}).items():
            if status == "達成":
                minimum_total += 1
                minimum_achieved += 1
            elif status == "未達":
                minimum_total += 1
            else:
                warnings.append(f"{entry['date']}: 最低ライン「{area}」の値「{status}」は集計から除外しました")

        what_went_well.extend(review.get("what_went_well") or [])
        breakdown_counter.update(review.get("breakdown_causes") or [])
        if review.get("one_change_tomorrow"):
            daily_changes.append({"date": entry["date"], "one_change_tomorrow": review["one_change_tomorrow"]})

        if entry.get("tomorrow_plan_final"):
            approved_plan_days += 1
        if entry.get("tomorrow_plan_proposal") and not entry.get("tomorrow_plan_final"):
            pending_proposal_days.append(entry["date"])

    for entry in _all_daily_entries(root):
        final = entry.get("tomorrow_plan_final") or {}
        target_date = final.get("target_date")
        if not target_date or not (start <= target_date <= end):
            continue
        results = {result.get("task_id"): result for result in entry.get("task_results") or []}
        for task in final.get("tasks") or []:
            task_total += 1
            task_minimum_total += 1
            result = results.get(task.get("id"))
            task_key = f"{task.get('area', '未設定')}：{task.get('task', '未設定')}"
            if not result:
                unrecorded_count += 1
                carryover_count += 1
                incomplete_by_task[task_key] += 1
                task_area_status_counts[task.get("area", "未設定")]["unrecorded"] += 1
                continue
            status = result.get("status", "unrecorded")
            task_status_counts[status] += 1
            task_area_status_counts[task.get("area", "未設定")][status] += 1
            if status == "completed":
                task_completed += 1
            if result.get("minimum_line_achieved"):
                task_minimum_achieved += 1
            if status in CARRYOVER_STATUSES:
                carryover_count += 1
                incomplete_by_task[task_key] += 1

    ranking = [{"cause": cause, "count": count} for cause, count in breakdown_counter.most_common()]
    if ranking:
        next_week_candidate = f"{ranking[0]['cause']}を減らすため、最初の一手を1つだけ決める"
    else:
        next_week_candidate = "データが少ないため、まず毎日の記録を残す"
    if len(entries) < 3:
        warnings.append("記録日数が少ないため、週次傾向は参考値です")

    percent = round((minimum_achieved / minimum_total) * 100, 1) if minimum_total else 0
    task_completion_percent = round((task_completed / task_total) * 100, 1) if task_total else None
    task_minimum_percent = round((task_minimum_achieved / task_minimum_total) * 100, 1) if task_minimum_total else None
    return {
        "start_date": start,
        "end_date": end,
        "recorded_days": len(entries),
        "main_status_counts": {area: dict(counter) for area, counter in main_status_counts.items()},
        "minimum_line_rate": {
            "achieved": minimum_achieved,
            "total": minimum_total,
            "percent": percent,
        },
        "what_went_well": what_went_well,
        "breakdown_ranking": ranking,
        "daily_changes": daily_changes,
        "approved_plan_days": approved_plan_days,
        "pending_proposal_days": pending_proposal_days,
        "next_week_change_candidate": next_week_candidate,
        "warnings": warnings,
        "task_execution": {
            "total": task_total,
            "completion_rate": {
                "completed": task_completed,
                "total": task_total,
                "percent": task_completion_percent,
            },
            "task_minimum_line_rate": {
                "achieved": task_minimum_achieved,
                "total": task_minimum_total,
                "percent": task_minimum_percent,
            },
            "status_counts": dict(task_status_counts),
            "area_status_counts": {area: dict(counter) for area, counter in task_area_status_counts.items()},
            "unrecorded_count": unrecorded_count,
            "carryover_count": carryover_count,
            "repeated_incomplete_candidates": [
                task for task, count in incomplete_by_task.items() if count >= 2
            ],
        },
    }
