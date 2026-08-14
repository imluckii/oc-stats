"""Tests for normalization, aggregation, and token-total reconciliation."""

from __future__ import annotations

from oc_usage.models import UsageRow, aggregate, component_total
from tests.helpers import T0


def _row(**kw) -> UsageRow:
    base = {
        "provider": "p",
        "model": "m",
        "variant": "",
        "input": 0,
        "cache_read": 0,
        "cache_write": 0,
        "output": 0,
        "reasoning": 0,
        "cost": 0.0,
        "time_created": 0,
    }
    base.update(kw)
    return UsageRow(**base)


def test_component_total_definition():
    # input + cache_read + cache_write + output + reasoning
    assert component_total(10, 20, 30, 40, 50) == 150


def test_row_total_uses_components_not_a_stored_field():
    r = _row(input=1, cache_read=2, cache_write=3, output=4, reasoning=5)
    assert r.total == 15  # 1+2+3+4+5


def test_aggregate_sums_each_category():
    rows = [
        _row(
            provider="p1",
            model="m1",
            input=10,
            cache_read=100,
            cache_write=1,
            output=5,
            reasoning=2,
        ),
        _row(
            provider="p1",
            model="m1",
            input=20,
            cache_read=200,
            cache_write=0,
            output=6,
            reasoning=3,
        ),
        _row(
            provider="p2", model="m2", input=5, cache_read=0, cache_write=0, output=1, reasoning=0
        ),
    ]
    report = aggregate(rows)

    t = report.totals
    assert t.turns == 3
    assert t.input == 35
    assert t.cache_read == 300
    assert t.cache_write == 1
    assert t.output == 12
    assert t.reasoning == 5
    assert t.total == 35 + 300 + 1 + 12 + 5


def test_aggregate_reconciles_provider_and_model_totals():
    rows = [
        _row(
            provider="p1",
            model="m1",
            input=10,
            cache_read=100,
            cache_write=1,
            output=5,
            reasoning=2,
        ),
        _row(
            provider="p1",
            model="m1",
            input=20,
            cache_read=200,
            cache_write=0,
            output=6,
            reasoning=3,
        ),
        _row(
            provider="p2", model="m2", input=5, cache_read=0, cache_write=0, output=1, reasoning=0
        ),
    ]
    report = aggregate(rows)

    # Sum of provider buckets == grand total.
    prov_sum = sum(b.total for b in report.by_provider.values())
    assert prov_sum == report.totals.total

    # Sum of model buckets == grand total.
    model_sum = sum(b.total for b in report.by_model.values())
    assert model_sum == report.totals.total

    assert report.by_provider["p1"].turns == 2
    assert report.by_provider["p2"].turns == 1
    assert report.by_model[("p1", "m1", "")].total == 347


def test_estimate_uses_model_price_not_recorded_cost():
    report = aggregate(
        [
            _row(
                provider="openai",
                model="gpt-5.6-sol",
                input=100_000,
                output=10_000,
                reasoning=10_000,
                cost=99,
            )
        ]
    )
    assert report.totals.estimated_cost == 1.1
    assert report.totals.estimate_complete is True


def test_estimate_marks_unknown_models_incomplete():
    report = aggregate([_row(model="unknown"), _row(provider="openai", model="gpt-4o")])
    assert report.totals.priced_turns == 1
    assert report.totals.estimate_complete is False


def test_estimate_applies_long_context_rate_per_turn():
    report = aggregate([_row(provider="openai", model="gpt-5.6-sol", input=300_000)])
    assert report.totals.estimated_cost == 3.0


def test_estimate_accepts_namespaced_model_ids():
    report = aggregate([_row(provider="openrouter", model="moonshotai/kimi-k3", input=1_000_000)])
    assert report.totals.estimated_cost == 3.0


def test_span_is_min_max_of_created_times_in_utc():
    rows = [
        _row(time_created=T0 + 5000),
        _row(time_created=T0),
        _row(time_created=T0 + 2000),
    ]
    report = aggregate(rows)
    assert report.span is not None
    lo, hi = report.span
    # min == T0, max == T0 + 5000 (converted from ms)
    import datetime

    expected_lo = datetime.datetime.fromtimestamp(T0 / 1000, tz=datetime.timezone.utc)
    assert lo == expected_lo
    assert hi > lo


def test_span_none_when_no_timestamps():
    report = aggregate([_row(time_created=0), _row(time_created=0)])
    assert report.span is None


def test_model_key_includes_variant():
    report = aggregate(
        [
            _row(provider="p", model="m", variant="high"),
            _row(provider="p", model="m", variant="low"),
        ]
    )
    assert ("p", "m", "high") in report.by_model
    assert ("p", "m", "low") in report.by_model
    assert len(report.by_model) == 2


def test_cache_write_is_tracked_separately():
    report = aggregate([_row(cache_write=250), _row(cache_write=10)])
    assert report.totals.cache_write == 260
