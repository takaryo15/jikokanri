from __future__ import annotations

from datetime import date, datetime, timedelta


DATE_FORMAT = "%Y-%m-%d"


def parse_date(value: str) -> date:
    return datetime.strptime(value, DATE_FORMAT).date()


def format_date(value: date) -> str:
    return value.isoformat()


def today_string() -> str:
    return date.today().isoformat()


def tomorrow_of(day: str) -> str:
    return (parse_date(day) + timedelta(days=1)).isoformat()


def week_range_for(day: str) -> tuple[str, str]:
    target = parse_date(day)
    # Python weekday: Monday=0, Tuesday=1. The desired week begins on Tuesday.
    days_since_tuesday = (target.weekday() - 1) % 7
    start = target - timedelta(days=days_since_tuesday)
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def date_range(start: str, end: str) -> list[str]:
    current = parse_date(start)
    final = parse_date(end)
    days: list[str] = []
    while current <= final:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days
