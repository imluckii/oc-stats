"""Derived statistics over a Report and its row list (Stats tab)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import tzinfo

from oc_usage.models import Bucket, Report, UsageRow
from oc_usage.timegroups import daily_buckets, hourly_buckets

# A 5-cell block bar for share rendering: 0..5 filled cells.
_BAR_CELLS = 5


def cache_hit_rate(bucket: Bucket) -> float | None:
    """cache_read / (input + cache_read + cache_write), or None without input.

    Cache writes are prompt tokens that were *not* served from cache, so they
    belong in the denominator: leaving them out would overstate hit rate.
    """
    denom = bucket.input + bucket.cache_read + bucket.cache_write
    if denom == 0:
        return None
    return bucket.cache_read / denom


def share(part: float, whole: float) -> float | None:
    if whole == 0:
        return None
    return part / whole


def bar(fraction: float | None, cells: int = _BAR_CELLS) -> str:
    """A fixed-width unicode block bar for a 0..1 fraction."""
    if fraction is None:
        return "·" * cells
    filled = round(max(0.0, min(1.0, fraction)) * cells)
    return "█" * filled + "░" * (cells - filled)


def provider_rows(report: Report) -> list[tuple[str, Bucket, float, float]]:
    """(provider, bucket, token share, cost share) sorted by cost desc."""
    total_tokens = report.totals.total or 1
    total_cost = report.totals.estimated_cost or 1.0
    rows = [
        (
            provider,
            bucket,
            bucket.total / total_tokens,
            bucket.estimated_cost / total_cost if report.totals.estimated_cost else 0.0,
        )
        for provider, bucket in report.by_provider.items()
    ]
    return sorted(rows, key=lambda row: row[1].estimated_cost, reverse=True)


def unpriced_ratio(report: Report) -> float | None:
    turns = report.totals.turns
    if turns == 0:
        return None
    return (turns - report.totals.priced_turns) / turns


def busiest(
    rows: Iterable[UsageRow], *, tz: tzinfo
) -> tuple[tuple[str, int] | None, tuple[int, int] | None]:
    """(busiest (day, tokens), busiest (hour, tokens)) or (None, None)."""
    daily = daily_buckets(rows, tz=tz)
    hourly = hourly_buckets(rows, tz=tz)
    day = max(daily.items(), key=lambda kv: kv[1].total, default=None)
    hour = max(hourly.items(), key=lambda kv: kv[1].total, default=None)
    day_out = (day[0], day[1].total) if day else None
    hour_out = (hour[0], hour[1].total) if hour else None
    return day_out, hour_out
