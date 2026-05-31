from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .models import WeeklyWindows


def compute_weekly_windows(digest_date: date) -> WeeklyWindows:
    new_start = digest_date - timedelta(days=7)
    new_end_exclusive = digest_date
    previous_start = digest_date - timedelta(days=14)
    previous_end_exclusive = digest_date - timedelta(days=7)
    first_seen_since = datetime.combine(new_start, time.min, tzinfo=timezone.utc)
    return WeeklyWindows(
        digest_date=digest_date.isoformat(),
        new_this_week_start=new_start.isoformat(),
        new_this_week_end=(new_end_exclusive - timedelta(days=1)).isoformat(),
        previous_week_start=previous_start.isoformat(),
        previous_week_end=(previous_end_exclusive - timedelta(days=1)).isoformat(),
        late_additions_before=previous_start.isoformat(),
        late_additions_first_seen_since=first_seen_since.isoformat(),
    )

