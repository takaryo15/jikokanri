from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .date_utils import tomorrow_of


MAX_MAIN_ITEMS = 3
NORMAL_TASK_WARNING_THRESHOLD = 6


@dataclass
class ValidationResult:
    ok: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def extend(self, other: "ValidationResult") -> None:
        self.ok.extend(other.ok)
        self.warnings.extend(other.warnings)
        self.errors.extend(other.errors)


def validate_daily(entry: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    source_date = entry.get("date")
    proposal = entry.get("tomorrow_plan_proposal")
    final = entry.get("tomorrow_plan_final")

    if proposal:
        result.extend(validate_plan(proposal, source_date, final=False))
    if final:
        result.extend(validate_plan(final, source_date, final=True))
    if not proposal and not final:
        result.errors.append("提案版または確定版のタスクがありません")
    return result


def validate_plan(plan: dict[str, Any], source_date: str, final: bool = False) -> ValidationResult:
    result = ValidationResult()
    label = "確定版" if final else "提案版"
    main = plan.get("main") or []
    tasks = plan.get("tasks") or []

    if len(main) <= MAX_MAIN_ITEMS:
        result.ok.append(f"{label}: Mainは{MAX_MAIN_ITEMS}件以内です")
    else:
        result.errors.append(f"{label}: Mainは最大{MAX_MAIN_ITEMS}つです（現在{len(main)}つ）")

    if isinstance(plan.get("one_change_tomorrow"), str) and plan["one_change_tomorrow"].strip():
        result.ok.append(f"{label}: 明日変えることが1つの文字列で保存されています")
    else:
        result.errors.append(f"{label}: one_change_tomorrowは空でない文字列にしてください")

    if tasks:
        result.ok.append(f"{label}: タスクがあります")
    else:
        result.errors.append(f"{label}: タスクがありません")

    if len(tasks) > NORMAL_TASK_WARNING_THRESHOLD:
        result.warnings.append(
            f"{label}: 通常タスクが{NORMAL_TASK_WARNING_THRESHOLD}件を超えています（現在{len(tasks)}件）"
        )

    main_set = set(main)
    for index, task in enumerate(tasks, start=1):
        if not str(task.get("minimum_line", "")).strip():
            result.errors.append(f"{label}: タスク{index}に最低ラインがありません")
        priority = task.get("priority")
        if not isinstance(priority, int) or priority < 1:
            result.errors.append(f"{label}: タスク{index}の優先順位が不正です")
        if task.get("area") not in main_set:
            result.warnings.append(f"{label}: タスク{index}の分野「{task.get('area')}」がMainに含まれていません")

    expected_target = tomorrow_of(source_date)
    if plan.get("target_date") == expected_target:
        result.ok.append(f"{label}: target_dateは保存元日の翌日です")
    else:
        result.errors.append(f"{label}: target_dateは{expected_target}にしてください（現在{plan.get('target_date')}）")

    if final:
        if plan.get("status") == "approved":
            result.ok.append("確定版: statusはapprovedです")
        else:
            result.errors.append("確定版: statusはapprovedにしてください")
        if plan.get("approved_at"):
            result.ok.append("確定版: approved_atがあります")
        else:
            result.errors.append("確定版: approved_atがありません")
    return result
