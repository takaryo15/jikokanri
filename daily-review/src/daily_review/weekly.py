from .date_utils import week_range_for
from .reporting import build_report


def build_weekly_summary(root, day: str):
    start, end = week_range_for(day)
    return build_report(root, start, end, period_type="weekly")
