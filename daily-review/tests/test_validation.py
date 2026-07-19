from __future__ import annotations

from daily_review.validation import validate_daily, validate_plan


def _plan(**overrides):
    payload = {
        "status": "pending_review",
        "target_date": "2026-07-14",
        "main": ["院試", "研究", "健康"],
        "tasks": [
            {
                "area": "院試",
                "task": "過去問",
                "priority": 1,
                "minimum_line": "問題文だけ読む",
            }
        ],
        "one_change_tomorrow": "朝イチで過去問を開く",
    }
    payload.update(overrides)
    return payload


def test_main_more_than_three_is_error():
    result = validate_plan(_plan(main=["院試", "研究", "健康", "読書"]), "2026-07-13")
    assert result.has_errors
    assert any("Mainは最大3つ" in item for item in result.errors)


def test_missing_minimum_line_is_detected():
    plan = _plan(
        tasks=[{"area": "院試", "task": "過去問", "priority": 1, "minimum_line": ""}]
    )
    result = validate_plan(plan, "2026-07-13")
    assert any("最低ライン" in item for item in result.errors)


def test_seven_tasks_warns_but_is_not_error_by_itself():
    tasks = [
        {"area": "院試", "task": f"task {index}", "priority": 1, "minimum_line": "読む"}
        for index in range(7)
    ]
    result = validate_plan(_plan(tasks=tasks), "2026-07-13")
    assert result.warnings
    assert not result.errors


def test_wrong_target_date_is_error():
    result = validate_plan(_plan(target_date="2026-07-15"), "2026-07-13")
    assert any("target_date" in item for item in result.errors)


def test_final_requires_approved_status_and_approved_at():
    result = validate_daily(
        {
            "date": "2026-07-13",
            "tomorrow_plan_final": _plan(status="pending_review", approved_at=None),
        }
    )
    assert any("statusはapproved" in item for item in result.errors)
    assert any("approved_at" in item for item in result.errors)
