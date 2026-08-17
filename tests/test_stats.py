"""Tests for derived statistics."""

from datetime import timedelta, timezone

from oc_usage.models import UsageRow, aggregate
from oc_usage.stats import (
    bar,
    busiest,
    cache_hit_rate,
    provider_rows,
    share,
    unpriced_ratio,
)
from oc_usage.timegroups import DEFAULT_TZ

IST = DEFAULT_TZ
UTC = timezone.utc
OTHER = timezone(timedelta(hours=-8))


def _row(
    provider: str = "openai",
    model: str = "gpt-5.6-sol",
    ts: int = 1_750_000_000_000,
    input: int = 10,
    cache_read: int = 0,
    output: int = 5,
    cost: float = 0.01,
) -> UsageRow:
    return UsageRow(
        provider=provider,
        model=model,
        variant="",
        input=input,
        cache_read=cache_read,
        cache_write=0,
        output=output,
        reasoning=0,
        cost=cost,
        time_created=ts,
    )


def _report(rows):
    return aggregate(rows, source="test")


class TestCacheHitRate:
    def test_zero_when_no_reads(self):
        assert cache_hit_rate(_report([_row(input=10)]).totals) == 0.0

    def test_none_when_no_reads_or_input(self):
        assert cache_hit_rate(_report([]).totals) is None

    def test_mixed(self):
        report = _report([_row(input=10, cache_read=30)])
        rate = cache_hit_rate(report.totals)
        assert rate is not None and abs(rate - 0.75) < 1e-9


class TestShareAndBar:
    def test_share(self):
        assert share(1, 4) == 0.25
        assert share(1, 0) is None

    def test_bar_bounds(self):
        assert bar(0.0) == "░" * 5
        assert bar(1.0) == "█" * 5
        assert bar(None) == "·" * 5
        assert len(bar(2.0)) == 5  # clamped, not longer


class TestProviderRows:
    def _report_with(self, providers: dict[str, tuple[int, float]]):
        from oc_usage.models import Bucket, Report

        by_provider = {}
        for name, (total_out, cost) in providers.items():
            bucket = Bucket(output=total_out, estimated_cost=cost, turns=1, priced_turns=1)
            by_provider[name] = bucket
        totals = Bucket(
            output=sum(t for t, _ in providers.values()),
            estimated_cost=sum(c for _, c in providers.values()),
            turns=len(providers),
            priced_turns=len(providers),
        )
        return Report(totals=totals, by_provider=by_provider, by_model={}, span=None, source="test")

    def test_sorted_by_cost_desc(self):
        report = self._report_with({"openai": (10, 0.02), "zai-coding-plan": (90, 0.50)})
        result = provider_rows(report)
        assert [r[0] for r in result] == ["zai-coding-plan", "openai"]
        assert result[0][2] + result[1][2] == 1.0  # token shares sum to 1

    def test_zero_total_cost_gives_zero_cost_shares(self):
        report = self._report_with({"openai": (10, 0.0)})
        assert provider_rows(report)[0][3] == 0.0


class TestUnpriced:
    def test_none_when_empty(self):
        assert unpriced_ratio(_report([])) is None

    def test_full_when_no_priced_turns(self):
        # rows with a model that has no price entry stay unpriced
        report = _report([_row(model="totally-unknown-model")])
        assert report.totals.priced_turns == 0
        assert unpriced_ratio(report) == 1.0


class TestBusiest:
    def test_empty(self):
        assert busiest([], tz=IST) == (None, None)

    def test_single_row(self):
        rows = [_row(ts=1_750_000_000_000)]
        day, hour = busiest(rows, tz=IST)
        assert day is not None and hour is not None
        assert day[1] == rows[0].total
        assert hour[1] == rows[0].total

    def test_spread_rows(self):
        base = 1_750_000_000_000
        rows = [
            _row(ts=base, output=1),
            _row(ts=base + 86_400_000, output=100),  # next day, dominates
        ]
        day, _hour = busiest(rows, tz=IST)
        assert day is not None and day[1] == rows[1].total
