from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import daily_review.date_utils as date_utils
from daily_review.date_utils import today_string, week_range_for
from daily_review.handoff import expires_at, is_expired


def test_today_uses_japan_local_date_across_midnight(monkeypatch):
    class BeforeMidnight(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 14, 23, 59, tzinfo=tz)

    monkeypatch.setattr(date_utils, "datetime", BeforeMidnight)
    assert today_string() == "2026-07-14"

    class AfterMidnight(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 15, 0, 0, tzinfo=tz)

    monkeypatch.setattr(date_utils, "datetime", AfterMidnight)
    assert today_string() == "2026-07-15"


def test_handoff_expiry_is_valid_at_0459_and_expired_at_0500():
    item = {"expires_at": expires_at("2026-07-14")}
    timezone = ZoneInfo("Asia/Tokyo")
    assert not is_expired(item, now=datetime(2026, 7, 15, 4, 59, 59, tzinfo=timezone))
    assert is_expired(item, now=datetime(2026, 7, 15, 5, 0, 0, tzinfo=timezone))


def test_tuesday_starts_new_week_and_monday_ends_it():
    assert week_range_for("2026-07-14") == ("2026-07-14", "2026-07-20")
    assert week_range_for("2026-07-20") == ("2026-07-14", "2026-07-20")
