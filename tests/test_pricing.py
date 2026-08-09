"""Tests for generated pricing lookup and per-turn estimates."""

from __future__ import annotations

from oc_usage.models import UsageRow
from oc_usage.pricing import MODEL_PRICES, estimate_row, model_price


def row(model: str, *, input=0, cache_read=0, cache_write=0, output=0, reasoning=0):
    return UsageRow(
        "provider", model, "", input, cache_read, cache_write, output, reasoning, 0.0, 0
    )


def test_catalog_has_expected_first_party_models():
    assert len(MODEL_PRICES) >= 40
    assert MODEL_PRICES["gpt-5.6-sol"]["input"] == 5
    assert MODEL_PRICES["glm-5.2"]["cache_read"] == 0.26
    assert MODEL_PRICES["deepseek-v4-flash"]["output"] == 0.28
    assert MODEL_PRICES["kimi-k3"]["cache_read"] == 0.3


def test_namespaced_and_dated_model_lookup():
    assert model_price("gateway/kimi-k3") == MODEL_PRICES["kimi-k3"]
    assert model_price("claude-sonnet-4-5-20250929") == MODEL_PRICES["claude-sonnet-4-5"]


def test_reasoning_is_priced_as_output():
    assert estimate_row(row("gpt-4o", output=1_000_000)) == 10
    assert estimate_row(row("gpt-4o", reasoning=1_000_000)) == 10


def test_long_context_tier_is_applied_per_turn():
    assert estimate_row(row("gpt-5.6-sol", input=272_000)) == 1.36
    assert estimate_row(row("gpt-5.6-sol", input=300_000)) == 3
