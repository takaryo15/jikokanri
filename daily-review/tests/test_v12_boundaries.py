from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from daily_review.date_utils import month_range_for, week_range_for
from daily_review.evaluation import _goal_snapshot
from daily_review.handoff import is_expired


def _goal(due_date: str | None = "2026-07-31"):
    return {
        "id": "goal-12345678", "title": "境界", "status": "active", "category": "研究", "due_date": due_date,
        "manual_progress": 50, "qualitative_criteria": [], "quantitative_metrics": [],
        "milestones": [{"id": "mile-12345678", "status": "active", "progress": {"mode": "automatic", "manual_value": None}, "qualitative_criteria": [], "quantitative_metrics": [], "steps": [{"id": "step-12345678", "status": "todo", "due_date": due_date}]}],
    }


def test_tuesday_week_and_month_year_boundaries():
    assert week_range_for("2026-07-20") == ("2026-07-14", "2026-07-20")
    assert week_range_for("2026-07-21") == ("2026-07-21", "2026-07-27")
    assert week_range_for("2026-08-01") == ("2026-07-28", "2026-08-03")
    assert month_range_for("2026-07-31") == ("2026-07-01", "2026-07-31")
    assert month_range_for("2026-12-31") == ("2026-12-01", "2026-12-31")
    assert month_range_for("2027-01-01") == ("2027-01-01", "2027-01-31")


def test_handoff_expiry_is_exact_at_0500_jst():
    item = {"expires_at": "2026-07-15T05:00:00+09:00"}
    assert not is_expired(item, now=datetime(2026, 7, 15, 4, 59, 59, tzinfo=ZoneInfo("Asia/Tokyo")))
    assert is_expired(item, now=datetime(2026, 7, 15, 5, 0, tzinfo=ZoneInfo("Asia/Tokyo")))


def test_due_date_is_not_overdue_until_the_following_day():
    on_due = _goal_snapshot(_goal(), period_end="2026-07-31", activity={"planned_items": 1, "linked_items": 1})
    after_due = _goal_snapshot(_goal(), period_end="2026-08-01", activity={"planned_items": 1, "linked_items": 1})
    no_due = _goal_snapshot(_goal(None), period_end="2026-08-01", activity={"planned_items": 1, "linked_items": 1})
    assert on_due["overdue_steps"] == 0
    assert after_due["overdue_steps"] == 1
    assert no_due["deadline_forecast"]["status"] == "unavailable"


def test_deadline_risk_thresholds_are_stable():
    # one completed plan item gives an observed speed of 1/7 per day
    activity = {"planned_items": 1, "linked_items": 1}
    goal = _goal()
    goal["milestones"][0]["steps"].insert(0, {"id": "step-abcdef12", "status": "done", "due_date": "2026-07-10"})
    values = []
    for remaining_days in (14, 7, 4, 3):
        day = f"2026-07-{31 - remaining_days:02d}"
        values.append(_goal_snapshot(goal, period_end=day, activity=activity)["deadline_forecast"]["status"])
    assert values == ["low_risk", "low_risk", "high_risk", "critical"]
