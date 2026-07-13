from __future__ import annotations

from typing import Any


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
    return "\n".join(lines).rstrip() + "\n"


def render_weekly(summary: dict[str, Any]) -> str:
    lines = [
        f"# 週次振り返り｜{summary['start_date']}〜{summary['end_date']}",
        f"記録日数：{summary['recorded_days']}",
        f"確定版指示書を作れた日数：{summary['approved_plan_days']}",
    ]

    lines.append("## Mainの達成状況")
    if summary["main_status_counts"]:
        for area, statuses in summary["main_status_counts"].items():
            detail = "、".join(f"{status}: {count}" for status, count in statuses.items())
            lines.append(f"- {area}：{detail}")
    else:
        lines.append("未保存")

    lines.append("## 最低ライン達成率")
    rate = summary["minimum_line_rate"]
    if rate["total"]:
        lines.append(f"{rate['achieved']}/{rate['total']}（{rate['percent']}%）")
    else:
        lines.append("集計できる記録がありません")

    lines.append("## 今週できたこと")
    lines.extend([f"- {item}" for item in summary["what_went_well"]] or ["未保存"])

    lines.append("## 崩れた原因ランキング")
    lines.extend(
        [f"{index}. {item['cause']}（{item['count']}回）" for index, item in enumerate(summary["breakdown_ranking"], 1)]
        or ["未保存"]
    )

    lines.append("## 日ごとの明日変えること")
    lines.extend([f"- {item['date']}：{item['one_change_tomorrow']}" for item in summary["daily_changes"]] or ["未保存"])

    lines.append("## 未承認の提案版が残った日")
    lines.extend([f"- {day}" for day in summary["pending_proposal_days"]] or ["なし"])

    lines.append("## 来週変えること1つの候補")
    lines.append(summary["next_week_change_candidate"])

    if summary["warnings"]:
        lines.append("## 警告")
        lines.extend(f"- {warning}" for warning in summary["warnings"])
    return "\n".join(lines).rstrip() + "\n"
