"""Tests for date-range filtering and daily/hourly bucketing."""

from datetime import datetime, timedelta, timezone

import pytest

from oc_usage.models import UsageRow
from oc_usage.timegroups import (
    DateRange,
    daily_buckets,
    day_key,
    filter_range,
    hour_key,
    hourly_buckets,
)

IST = timezone(timedelta(hours=5, minutes=30))

# 2025-06-15 18:26:40 UTC (fixed epoch for deterministic tests)
NOW = 1_750_000_000_000


def _row(ts_ms: int, output: int = 10) -> UsageRow:
    return UsageRow(
        provider="zai-coding-plan",
        model="glm-5.3",
        variant="",
        input=100,
        cache_read=50,
        cache_write=0,
        output=output,
        reasoning=0,
        cost=0.01,
        time_created=ts_ms,
    )


class TestFilterRange:
    def test_all_keeps_everything(self):
        rows = [_row(NOW), _row(0)]
        assert filter_range(rows, DateRange.ALL, now_ms=NOW, tz=IST) == rows

    def test_today_excludes_yesterday(self):
        rows = [_row(NOW), _row(NOW - 90_000_000)]  # ~25h earlier
        out = filter_range(rows, DateRange.TODAY, now_ms=NOW, tz=IST)
        assert [r.time_created for r in out] == [NOW]

    def test_today_keeps_same_local_day(self):
        # midnight IST of NOW's local day is inside "today"
        midnight = int(
            datetime.fromtimestamp(NOW / 1000, tz=IST)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        rows = [_row(NOW), _row(midnight)]
        out = filter_range(rows, DateRange.TODAY, now_ms=NOW, tz=IST)
        assert len(out) == 2

    def test_7d_window(self):
        rows = [_row(NOW), _row(NOW - 7 * 86_400_000 + 1), _row(NOW - 8 * 86_400_000)]
        out = filter_range(rows, DateRange.D7, now_ms=NOW, tz=IST)
        assert len(out) == 2

    def test_30d_window(self):
        rows = [_row(NOW - 29 * 86_400_000)]
        assert len(filter_range(rows, DateRange.D30, now_ms=NOW, tz=IST)) == 1

    def test_untimestamped_rows_only_survive_all(self):
        rows = [_row(0)]
        assert len(filter_range(rows, DateRange.ALL, now_ms=NOW, tz=IST)) == 1
        assert filter_range(rows, DateRange.TODAY, now_ms=NOW, tz=IST) == []


class TestBuckets:
    def test_daily_sums_same_day(self):
        rows = [_row(NOW), _row(NOW + 3_600_000)]
        buckets = daily_buckets(rows, tz=IST)
        assert len(buckets) == 1
        bucket = next(iter(buckets.values()))
        assert bucket.output == 20
        assert bucket.turns == 2

    def test_daily_splits_at_ist_midnight(self):
        late = int(
            datetime.fromtimestamp(NOW / 1000, tz=IST)
            .replace(hour=23, minute=30, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        early_next = late + 3_600_000  # 00:30 next local day
        rows = [_row(late), _row(early_next)]
        assert len(daily_buckets(rows, tz=IST)) == 2

    def test_hourly_buckets_by_local_hour(self):
        rows = [_row(NOW), _row(NOW + 3_600_000)]
        buckets = hourly_buckets(rows, tz=IST)
        assert len(buckets) == 2
        assert sum(b.total for b in buckets.values()) == 2 * _row(0).total

    def test_skips_untimestamped(self):
        assert daily_buckets([_row(0)], tz=IST) == {}
        assert hourly_buckets([_row(0)], tz=IST) == {}


class TestKeys:
    def test_hour_key_matches_local_hour(self):
        assert hour_key(NOW, IST) == datetime.fromtimestamp(NOW / 1000, tz=IST).hour

    def test_range_cycle(self):
        rng = DateRange.ALL
        seen = [rng := rng.next for _ in range(4)]
        assert seen == [DateRange.TODAY, DateRange.D7, DateRange.D30, DateRange.ALL]


class TestTimezoneOverride:
    def test_iana_zone_name_is_honoured(self):
        from zoneinfo import ZoneInfo

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("OC_STATS_TZ", "America/New_York")
            from oc_usage.timegroups import get_tz, tz_label

            tz = get_tz()
            assert isinstance(tz, ZoneInfo) and str(tz) == "America/New_York"
            assert tz_label() == "America/New_York"
            # 04:00 UTC on Jun 16 is still Jun 15 in New York.
            assert day_key(1_750_014_400_000, tz) == "2025-06-15"
            assert day_key(1_750_014_400_000, IST) == "2025-06-16"

    def test_unresolvable_zone_falls_back_to_ist(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("OC_STATS_TZ", "Mars/Olympus_Mons")
            from oc_usage.timegroups import get_tz

            assert get_tz() == IST

    def test_default_label(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("OC_STATS_TZ", raising=False)
            from oc_usage.timegroups import tz_label

            assert tz_label() == "IST"
