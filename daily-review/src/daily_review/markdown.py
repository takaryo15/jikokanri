from __future__ import annotations

from typing import Any


TASK_STATUS_LABELS = {
    "completed": "完了",
    "partial": "一部進んだ",
    "minimum_only": "最低ラインのみ",
    "not_started": "未着手",
    "skipped": "意図的に見送り",
}


def _lines_for_plan(title: str, plan: dict[str, Any] | None) -> list[str]:
    lines = [f"## {title}"]
    if not plan:
        return lines + ["未保存"]
    status = "承認済み" if plan.get("status") == "approved" else "未承認"
    lines.append(f"状態：{status}  ")
    lines.append(f"対象日：{plan.get('target_date', '未保存')}")
    if plan.get("approved_at"):
        lines.append(f"承認日時：{plan['approved_at']}")
    main = plan.get("main") or []
    if main:
        lines.append("### Main")
        lines.extend(f"{index}. {item}" for index, item in enumerate(main, start=1))
    tasks = plan.get("tasks") or []
    if tasks:
        lines.append("### タスク")
        for index, task in enumerate(tasks, start=1):
            lines.append(f"{index}. {task.get('area', '未設定')}：{task.get('task', '未設定')}")
            lines.append(f"   - 最低ライン：{task.get('minimum_line', '未設定')}")
    if plan.get("one_change_tomorrow"):
        lines.append("### 変えること")
        lines.append(plan["one_change_tomorrow"])
    return lines


def _lines_for_task_results(entry: dict[str, Any]) -> list[str]:
    plan = entry.get("tomorrow_plan_final") or {}
    results = {result.get("task_id"): result for result in entry.get("task_results") or []}
    tasks = plan.get("tasks") or []
    target = plan.get("target_date", "対象日未設定")
    lines = [f"## タスク実行結果｜{target}"]
    if not tasks:
        return lines + ["確定版タスクがありません"]
    for task in tasks:
        result = results.get(task.get("id"))
        lines.append(f"- {task.get('area', '未設定')}：{task.get('task', '未設定')}")
        if result:
            lines.append(f"  - 結果：{TASK_STATUS_LABELS.get(result.get('status'), result.get('status', '未記録'))}")
            lines.append(f"  - 最低ライン：{'達成' if result.get('minimum_line_achieved') else '未達'}")
            if result.get("note"):
                lines.append(f"  - メモ：{result['note']}")
        else:
            lines.append("  - 結果：未記録")
            lines.append("  - 最低ライン：未記録")
    return lines


def render_daily(entry: dict[str, Any]) -> str:
    day = entry["date"]
    review = entry.get("structured_review") or {}
    lines = [f"# 夜の振り返り｜{day}"]

    if entry.get("raw_log"):
        lines.extend(["## 生ログ", entry["raw_log"]])
    else:
        lines.extend(["## 生ログ", "未保存"])

    if entry.get("diary"):
        lines.extend(["## 日記", entry["diary"]])

    today_main = review.get("today_main") or []
    if today_main:
        lines.append("## 今日のMain")
        for item in today_main:
            lines.append(f"- {item.get('area', '未設定')}：{item.get('status', '未設定')}")
            if item.get("note"):
                lines.append(f"  - {item['note']}")

    minimum_line = review.get("minimum_line") or {}
    if minimum_line:
        lines.append("## 最低ライン")
        lines.extend(f"- {area}：{status}" for area, status in minimum_line.items())

    went_well = review.get("what_went_well") or []
    if went_well:
        lines.append("## 今日できたこと")
        lines.extend(f"- {item}" for item in went_well)

    causes = review.get("breakdown_causes") or []
    if causes:
        lines.append("## 崩れた原因")
        lines.extend(f"- {item}" for item in causes)

    if review.get("one_change_tomorrow"):
        lines.extend(["## 明日変えることを1つだけ", review["one_change_tomorrow"]])

    lines.extend(_lines_for_plan("明日の指示書・提案版", entry.get("tomorrow_plan_proposal")))
    lines.extend(_lines_for_plan("明日の指示書・確定版", entry.get("tomorrow_plan_final")))
    if entry.get("tomorrow_plan_final"):
        lines.extend(_lines_for_task_results(entry))
    return "\n".join(lines).rstrip() + "\n"


def render_weekly(summary: dict[str, Any]) -> str:
    return render_report(summary, "週次")


def _percent(value: float | None) -> str:
    return "算出不可" if value is None else f"{value}%"


def render_report(summary: dict[str, Any], title: str) -> str:
    coverage = summary["data_coverage"]
    main = summary["main_summary"]
    minimum = summary["minimum_line_summary"]
    continuity = summary["continuity"]
    lines = [
        f"# {title}振り返り｜{summary['start_date']}〜{summary['end_date']}",
        "## データ状況",
        f"- 対象期間：{coverage['period_days']}日",
        f"- 日次データ：{coverage['daily_data_days']}日",
        f"- タスク結果：{coverage['task_results_days']}日",
        f"- 確定済み指示書：{coverage['approved_plan_days']}日",
        f"確定版指示書を作れた日数：{coverage['approved_plan_days']}",
    ]

    lines.extend(["## Main達成状況", f"- 対象：{main['total']}件", f"- 完了：{main['completed']}件", f"- 一部達成：{main['partial']}件", f"- 最低限のみ：{main['minimum_only']}件", f"- 未着手：{main['not_started']}件", f"- スキップ：{main['skipped']}件", f"- 結果未記録：{main['unrecorded']}件", f"- 達成率：{_percent(main['percent'])}"])

    lines.append("## 最低ライン達成率")
    if minimum["percent"] is None:
        lines.extend(["算出不可", f"理由：{minimum['reason']}"])
    else:
        lines.append(f"{minimum['achieved']}/{minimum['total']}（{minimum['percent']}%）")

    lines.extend(["## 継続状況", f"- 振り返り記録：{continuity['review_recorded']['count']}/{continuity['review_recorded']['total']}日（{_percent(continuity['review_recorded']['percent'])}）", f"- タスク結果記録：{continuity['task_results_recorded']['count']}/{continuity['task_results_recorded']['total']}日（{_percent(continuity['task_results_recorded']['percent'])}）", f"- 確定済み指示書：{continuity['approved_plan']['count']}/{continuity['approved_plan']['total']}日（{_percent(continuity['approved_plan']['percent'])}）"])

    lines.append("## 今週できたこと")
    lines.extend([f"- {item}" for item in summary["what_went_well"]] or ["未保存"])

    lines.append("## 崩れた原因ランキング")
    lines.extend(
        [f"{index}. {item['cause']}（{item['count']}回）" for index, item in enumerate(summary["breakdown_ranking"], 1)]
        or ["未保存"]
    )

    lines.append("## 引き継ぎが多いタスク")
    lines.extend([f"{index}. {item['task']}（{item['count']}回）" for index, item in enumerate(summary["carryover_analysis"], 1)] or ["なし"])
    lines.extend([f"## {'翌月' if title == '月次' else '来週'}変えること1つ", summary["improvement_suggestion"]["text"]])
    if summary.get("weekly_trends") is not None:
        lines.append("## 週ごとの傾向")
        for item in summary["weekly_trends"]:
            lines.append(f"- {item['start_date']}〜{item['end_date']}：Main {_percent(item['main_completion_rate'])}、振り返り {item['review_recorded_days']}日、主な原因 {item['top_failure_reason'] or 'なし'}")
    return "\n".join(lines).rstrip() + "\n"


def render_monthly(summary: dict[str, Any]) -> str:
    return render_report(summary, "月次")
