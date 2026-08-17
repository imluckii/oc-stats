"""Date-range filtering and daily/hourly bucketing for UsageRow lists.

Day and hour keys are computed in a single timezone (IST by default,
``OC_STATS_TZ`` to override) so that "today" and "hour of day" match the
user's wall clock rather than UTC.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from enum import Enum

from oc_usage.models import Bucket, UsageRow

DEFAULT_TZ = timezone(timedelta(hours=5, minutes=30))  # IST


class DateRange(Enum):
    ALL = "all"
    TODAY = "today"
    D7 = "7d"
    D30 = "30d"

    @property
    def next(self) -> DateRange:
        order = [DateRange.ALL, DateRange.TODAY, DateRange.D7, DateRange.D30]
        return order[(order.index(self) + 1) % len(order)]

    @property
    def label(self) -> str:
        return {
            DateRange.ALL: "all time",
            DateRange.TODAY: "today",
            DateRange.D7: "7 days",
            DateRange.D30: "30 days",
        }[self]


def get_tz() -> timezone:
    """Resolve the grouping timezone from ``OC_STATS_TZ`` (UTC supported)."""
    raw = os.environ.get("OC_STATS_TZ", "").strip().upper()
    if raw == "UTC":
        return timezone.utc
    return DEFAULT_TZ


def day_key(ts_ms: int, tz: timezone) -> str:
    """Local calendar date for an epoch-ms timestamp, ``YYYY-MM-DD``."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=tz).strftime("%Y-%m-%d")


def hour_key(ts_ms: int, tz: timezone) -> int:
    """Local hour of day (0-23) for an epoch-ms timestamp."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=tz).hour


def range_cutoff_ms(rng: DateRange, *, now_ms: int, tz: timezone) -> int | None:
    """Inclusive-lower cutoff (epoch ms) for a range, or None for ALL."""
    if rng is DateRange.ALL:
        return None
    if rng is DateRange.TODAY:
        local = datetime.fromtimestamp(now_ms / 1000, tz=tz)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp() * 1000)
    days = 7 if rng is DateRange.D7 else 30
    return now_ms - days * 86_400_000


def filter_range(
    rows: Iterable[UsageRow],
    rng: DateRange,
    *,
    now_ms: int | None = None,
    tz: timezone = DEFAULT_TZ,
) -> list[UsageRow]:
    """Rows at or after the range's cutoff. Rows with no timestamp are kept
    only for ALL (they cannot be placed in time)."""
    cutoff = range_cutoff_ms(
        rng,
        now_ms=now_ms if now_ms is not None else int(datetime.now(tz).timestamp() * 1000),
        tz=tz,
    )
    if cutoff is None:
        return list(rows)
    return [row for row in rows if row.time_created and row.time_created >= cutoff]


def daily_buckets(rows: Iterable[UsageRow], *, tz: timezone = DEFAULT_TZ) -> dict[str, Bucket]:
    """Sum rows into one Bucket per local calendar day (key: ``YYYY-MM-DD``)."""
    buckets: dict[str, Bucket] = {}
    for row in rows:
        if not row.time_created:
            continue
        buckets.setdefault(day_key(row.time_created, tz), Bucket()).add(row)
    return buckets


def hourly_buckets(rows: Iterable[UsageRow], *, tz: timezone = DEFAULT_TZ) -> dict[int, Bucket]:
    """Sum rows into one Bucket per local hour of day (key: 0-23)."""
    buckets: dict[int, Bucket] = {}
    for row in rows:
        if not row.time_created:
            continue
        buckets.setdefault(hour_key(row.time_created, tz), Bucket()).add(row)
    return buckets
