from __future__ import annotations

from collections import Counter
from typing import Any

from .date_utils import date_range, week_range_for
from .storage import load_daily, read_json_file


CARRYOVER_STATUSES = {"partial", "minimum_only", "not_started"}
STATUS_KEYS = ("completed", "partial", "minimum_only", "not_started", "skipped")


def all_daily_entries(root) -> list[dict[str, Any]]:
    daily_dir = root / "data" / "daily"
    if not daily_dir.exists():
        return []
    return [read_json_file(path) for path in sorted(daily_dir.glob("*.json"))]


def _normal_text(value: Any) -> str:
    return " ".join(str(value).replace("\n", " ").split())


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 1) if denominator else None


def _main_task(task: dict[str, Any], plan: dict[str, Any]) -> bool:
    main = {_normal_text(item) for item in plan.get("main") or []}
    return (
        _normal_text(task.get("area", "")) in main
        or _normal_text(task.get("task", "")) in main
    )


def build_report(root, start: str, end: str, *, period_type: str) -> dict[str, Any]:
    period_days = date_range(start, end)
    entries = [entry for day in period_days if (entry := load_daily(root, day))]
    all_entries = all_daily_entries(root)
    causes: Counter[str] = Counter()
    completed_items: list[str] = []
    seen_completed: set[str] = set()
    daily_changes: list[dict[str, str]] = []
    pending_proposal_days: list[str] = []
    structured_minimum_total = structured_minimum_achieved = 0
    structured_minimum_values: list[bool] = []
    legacy_main_status: dict[str, Counter[str]] = {}

    for entry in entries:
        review = entry.get("structured_review") or {}
        for item in review.get("today_main") or []:
            area, status = item.get("area", "未設定"), item.get("status", "未設定")
            legacy_main_status.setdefault(area, Counter())[status] += 1
        for status in (review.get("minimum_line") or {}).values():
            if status in {"達成", "未達"}:
                structured_minimum_total += 1
                structured_minimum_achieved += status == "達成"
                structured_minimum_values.append(status == "達成")
        for cause in review.get("breakdown_causes") or []:
            normalized = _normal_text(cause)
            if normalized:
                causes[normalized] += 1
        for item in review.get("what_went_well") or []:
            normalized = _normal_text(item)
            if normalized and normalized not in seen_completed:
                completed_items.append(normalized)
                seen_completed.add(normalized)
        if review.get("one_change_tomorrow"):
            daily_changes.append(
                {
                    "date": entry.get("date", ""),
                    "one_change_tomorrow": review["one_change_tomorrow"],
                }
            )
        if entry.get("tomorrow_plan_proposal") and not entry.get("tomorrow_plan_final"):
            pending_proposal_days.append(entry.get("date", ""))
    approved_entry_days = sum(
        1 for entry in entries if entry.get("tomorrow_plan_final")
    )

    status_counts: Counter[str] = Counter()
    category_counts: dict[str, Counter[str]] = {}
    main_status_counts: Counter[str] = Counter()
    carryovers: Counter[str] = Counter()
    task_total = task_completed = unrecorded = 0
    main_total = main_completed = main_unrecorded = 0
    result_days: set[str] = set()
    approved_targets: set[str] = set()
    task_minimum_total = task_minimum_achieved = 0
    task_minimum_values: list[bool] = []
    for entry in all_entries:
        final = entry.get("tomorrow_plan_final") or {}
        target = final.get("target_date")
        if not target or not (start <= target <= end):
            continue
        approved_targets.add(target)
        results = {
            result.get("task_id"): result for result in entry.get("task_results") or []
        }
        if results:
            result_days.add(target)
        for task in final.get("tasks") or []:
            is_main = _main_task(task, final)
            result = results.get(task.get("id"))
            task_total += 1
            if is_main:
                main_total += 1
            task_name = _normal_text(task.get("task", "未設定"))
            if not result:
                unrecorded += 1
                if is_main:
                    main_unrecorded += 1
                continue
            status = result.get("status")
            category = _normal_text(task.get("area", "未設定")) or "未設定"
            if status in STATUS_KEYS:
                status_counts[status] += 1
                category_counts.setdefault(category, Counter())[status] += 1
                if is_main:
                    main_status_counts[status] += 1
            if status == "completed":
                task_completed += 1
                label = f"{task.get('area', '未設定')}：{task.get('task', '未設定')}"
                if label not in seen_completed:
                    completed_items.append(label)
                    seen_completed.add(label)
                if is_main:
                    main_completed += 1
            if result.get("minimum_line_achieved") is not None:
                task_minimum_total += 1
                task_minimum_achieved += result.get("minimum_line_achieved") is True
                task_minimum_values.append(result.get("minimum_line_achieved") is True)
            if status in CARRYOVER_STATUSES:
                carryovers[task_name] += 1

    # A newer optional field is preferred when present; old data retains its review-level metric.
    explicit_minimum: list[bool] = []
    for entry in entries:
        value = (entry.get("minimum_line_result") or {}).get("status")
        if value == "achieved":
            explicit_minimum.append(True)
        elif value == "not_achieved":
            explicit_minimum.append(False)
    if not explicit_minimum:
        explicit_minimum = structured_minimum_values or task_minimum_values
    min_percent = (
        _rate(sum(explicit_minimum), len(explicit_minimum))
        if explicit_minimum
        else None
    )
    review_days = len(entries)
    continuity = {
        "review_recorded": {
            "count": review_days,
            "total": len(period_days),
            "percent": _rate(review_days, len(period_days)),
        },
        "task_results_recorded": {
            "count": len(result_days),
            "total": len(period_days),
            "percent": _rate(len(result_days), len(period_days)),
        },
        "approved_plan": {
            "count": approved_entry_days,
            "total": len(period_days),
            "percent": _rate(approved_entry_days, len(period_days)),
        },
    }
    failure_reasons = [
        {"cause": cause, "count": count} for cause, count in causes.most_common()
    ]
    carryover_analysis = [
        {"task": name, "count": count} for name, count in carryovers.most_common()
    ]
    main_recorded = main_total - main_unrecorded
    main_summary = {
        "total": main_total,
        "completed": main_completed,
        "partial": main_status_counts["partial"],
        "minimum_only": main_status_counts["minimum_only"],
        "not_started": main_status_counts["not_started"],
        "skipped": main_status_counts["skipped"],
        "unrecorded": main_unrecorded,
        "recorded": main_recorded,
        "percent": _rate(main_completed, main_recorded),
    }
    if failure_reasons and failure_reasons[0]["count"] >= 2:
        suggestion = {
            "text": f"{failure_reasons[0]['cause']}が{failure_reasons[0]['count']}回記録されています。次の期間はこの原因への対策を1つ決めてください。",
            "rule": "most_common_failure_reason",
            "evidence": [failure_reasons[0]],
        }
    elif carryover_analysis and carryover_analysis[0]["count"] >= 2:
        suggestion = {
            "text": f"{carryover_analysis[0]['task']}が{carryover_analysis[0]['count']}回未完了です。次の期間は最初の一手を小さくしてください。",
            "rule": "repeated_carryover",
            "evidence": [carryover_analysis[0]],
        }
    elif main_summary["percent"] is not None and main_summary["percent"] < 50:
        suggestion = {
            "text": "Main達成率が50%未満です。次の期間はMainを絞ってください。",
            "rule": "low_main_completion",
            "evidence": [main_summary["percent"]],
        }
    elif continuity["task_results_recorded"]["percent"] < 50:
        suggestion = {
            "text": "タスク結果の記録率が50%未満です。次の期間は結果を記録するタイミングを1つ決めてください。",
            "rule": "low_task_result_continuity",
            "evidence": [continuity["task_results_recorded"]],
        }
    else:
        suggestion = {
            "text": "現在の運用を継続し、毎日の記録を続けてください。",
            "rule": "continue_current_practice",
            "evidence": [],
        }
    warnings = (
        ["記録日数が少ないため、週次傾向は参考値です"]
        if period_type == "weekly" and review_days < 3
        else []
    )
    category_analysis = []
    for category, counts in sorted(category_counts.items()):
        recorded = sum(counts.values())
        category_analysis.append(
            {
                "category": category,
                "recorded": recorded,
                "completed": counts["completed"],
                "completion_percent": _rate(counts["completed"], recorded),
            }
        )
    return {
        "report_type": period_type,
        "period": {"start_date": start, "end_date": end},
        "start_date": start,
        "end_date": end,
        "data_coverage": {
            "period_days": len(period_days),
            "daily_data_days": review_days,
            "task_results_days": len(result_days),
            "approved_plan_days": approved_entry_days,
        },
        "recorded_days": review_days,
        "approved_plan_days": approved_entry_days,
        "main_summary": main_summary,
        "minimum_line_summary": {
            "achieved": sum(explicit_minimum),
            "total": len(explicit_minimum),
            "percent": min_percent,
            "reason": None
            if explicit_minimum
            else "日次データに最低ライン達成結果が保存されていないため",
        },
        "continuity": continuity,
        "completed_items": completed_items,
        "failure_reasons": failure_reasons,
        "carryover_analysis": carryover_analysis,
        "category_analysis": category_analysis,
        "long_incomplete_tasks": [
            item for item in carryover_analysis if item["count"] >= 3
        ],
        "improvement_suggestion": suggestion,
        "main_status_counts": {
            area: dict(counter) for area, counter in legacy_main_status.items()
        },
        "minimum_line_rate": {
            "achieved": structured_minimum_achieved,
            "total": structured_minimum_total,
            "percent": _rate(structured_minimum_achieved, structured_minimum_total)
            or 0,
        },
        "what_went_well": completed_items,
        "breakdown_ranking": failure_reasons,
        "daily_changes": daily_changes,
        "pending_proposal_days": pending_proposal_days,
        "next_week_change_candidate": suggestion["text"],
        "warnings": warnings,
        "task_execution": {
            "total": task_total,
            "completion_rate": {
                "completed": task_completed,
                "total": task_total,
                "percent": _rate(task_completed, task_total),
            },
            "task_minimum_line_rate": {
                "achieved": task_minimum_achieved,
                "total": task_minimum_total,
                "percent": _rate(task_minimum_achieved, task_minimum_total),
            },
            "status_counts": dict(status_counts),
            "unrecorded_count": unrecorded,
            "carryover_count": sum(carryovers.values()),
            "repeated_incomplete_candidates": [
                item["task"] for item in carryover_analysis if item["count"] >= 2
            ],
        },
    }


def weekly_trends(root, start: str, end: str) -> list[dict[str, Any]]:
    trends = []
    cursor = start
    while cursor <= end:
        week_start, week_end = week_range_for(cursor)
        clipped_start, clipped_end = max(start, week_start), min(end, week_end)
        report = build_report(root, clipped_start, clipped_end, period_type="week")
        trends.append(
            {
                "start_date": clipped_start,
                "end_date": clipped_end,
                "main_completion_rate": report["main_summary"]["percent"],
                "review_recorded_days": report["data_coverage"]["daily_data_days"],
                "top_failure_reason": (report["failure_reasons"] or [{}])[0].get(
                    "cause"
                ),
            }
        )
        from .date_utils import parse_date

        cursor = (
            parse_date(week_end).fromordinal(parse_date(week_end).toordinal() + 1)
        ).isoformat()
    return trends
