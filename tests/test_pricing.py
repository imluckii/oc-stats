"""Tests for the price list: loading, matching rules, estimation, overrides."""

from __future__ import annotations

import pytest

from oc_usage.models import UsageRow, aggregate
from oc_usage.pricing import (
    Pricing,
    PricingError,
    default_pricing,
    estimate_row,
    load,
    load_bundled,
    user_price_path,
)


def row(provider, model, inp=0, cr=0, cw=0, out=0, reas=0, cost=0.0):
    return UsageRow(
        provider=provider,
        model=model,
        variant="",
        input=inp,
        cache_read=cr,
        cache_write=cw,
        output=out,
        reasoning=reas,
        cost=cost,
        time_created=0,
    )


# ── bundled file ──────────────────────────────────────────────────────────────


def test_bundled_file_loads_and_is_sane():
    pricing = load_bundled()
    assert pricing.lookup("anthropic", "claude-opus-5") is not None
    assert pricing.lookup("openai", "gpt-5.6-sol") is not None


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        # Spot-checks pinning verified official prices (2026-08-14).
        ("anthropic", "claude-sonnet-5", (2, 10, 0.2, 2.5)),
        ("anthropic", "claude-opus-4-1", (15, 75, 1.5, 18.75)),
        ("openai", "gpt-5.6-sol", (5, 30, 0.5, 6.25)),
        ("openai", "gpt-5.6-terra", (2, 12, 0.2, 2.5)),
        ("openai", "gpt-5.6-luna", (0.2, 1.2, 0.02, 0.25)),
        ("openai", "gpt-4o-mini", (0.15, 0.6, 0.075, 0)),
        ("google", "gemini-3.5-flash", (1.5, 9, 0.15, 0)),
        ("google", "gemini-2.5-pro", (1.25, 10, 0.125, 0)),
        ("xai", "grok-4.6", (2, 6, 0.5, 0)),
        ("deepseek", "deepseek-v4-flash", (0.22, 0.66, 0.007, 0)),
        ("mistral", "mistral-large-latest", (0.5, 1.5, 0.05, 0)),
        ("moonshotai", "kimi-k3", (3, 15, 0.3, 0)),
        ("zai", "glm-5.2", (1.4, 4.4, 0.26, 0)),
        ("minimax", "minimax-m3", (0.3, 1.2, 0.06, 0)),
        ("groq", "llama-3.3-70b-versatile", (0.59, 0.79, 0, 0)),
        ("cohere", "command-r-08-2024", (0.15, 0.6, 0, 0)),
        ("perplexity", "sonar-pro", (3, 15, 0, 0)),
        ("alibaba", "qwen3.7-plus", (0.5, 3, 0.05, 0.625)),
        ("opencode", "gpt-5.6-terra", (2, 12, 0.2, 2.5)),
    ],
)
def test_bundled_spot_checks(provider, model, expected):
    price = load_bundled().lookup(provider, model)
    assert price is not None, f"missing {provider}/{model}"
    r = price.rates
    got = (r.input, r.output, r.cache_read, r.cache_write)
    assert got == expected


def test_openai_cache_write_is_zero_not_input():
    # Regression guard: the old generated catalog billed missing cache tiers
    # at the input rate, over-charging every cached OpenAI request.
    r = load_bundled().lookup("openai", "gpt-4o").rates
    assert (r.input, r.output, r.cache_read, r.cache_write) == (2.5, 10, 1.25, 0)


# ── matching rules ────────────────────────────────────────────────────────────


def test_matching_is_case_insensitive():
    pricing = load_bundled()
    assert pricing.lookup("Anthropic", "Claude-Opus-5") == pricing.lookup(
        "anthropic", "claude-opus-5"
    )


def test_gateway_slash_id_matches_first_party():
    rates = load_bundled().lookup("my-proxy", "anthropic/claude-opus-5")
    assert rates is not None and rates.rates.input == 5


def test_custom_provider_id_falls_back_to_vendor_prefix():
    # OpenCode lets users rename providers ("openai-anchit"); trimming the
    # custom suffix reaches the vendor table with full cache fidelity.
    price = load_bundled().lookup("openai-anchit", "gpt-5.6-sol")
    assert price is not None
    assert price.rates.input == 5
    assert price.rates.cache_write == 6.25


def test_coding_plan_prefix_trims_step_by_step():
    # "zai-coding-plan-cn" → "zai-coding-plan" (exact gateway) before "zai".
    pricing = load_bundled()
    assert pricing.lookup("zai-coding-plan", "glm-4.7") is not None
    # glm-5.3 is pinned on the zai table and inherited by the coding plan.
    assert pricing.lookup("zai", "glm-5.3").rates.input == 1.4
    assert pricing.lookup("zai-coding-plan", "glm-5.3").rates.input == 1.4


def test_new_release_models_are_priced():
    pricing = load_bundled()
    sol_fast = pricing.lookup("openai", "gpt-5.6-sol-fast")
    assert sol_fast is not None
    assert (sol_fast.rates.input, sol_fast.rates.output) == (10, 60)
    # Custom provider ids reach the fast variants too.
    assert pricing.lookup("openai-anchit", "gpt-5.6-sol-fast") is not None
    # opencode-go models (MiniMax M3, GLM-5.3) come straight from models.dev.
    assert pricing.lookup("opencode-go", "minimax-m3").rates.input == 0.3
    assert pricing.lookup("opencode-go", "glm-5.3").rates.input == 1.4


def test_subscription_gateways_inherit_vendor_api_rates():
    # models.dev lists coding plans at $0/token; the generator backfills the
    # vendor's real API list price instead of estimating $0.
    pricing = load_bundled()
    glm52 = pricing.lookup("zai-coding-plan", "glm-5.2").rates
    assert (glm52.input, glm52.output, glm52.cache_read) == (1.4, 4.4, 0.26)
    glm53 = pricing.lookup("zai-coding-plan", "glm-5.3").rates
    assert (glm53.input, glm53.output) == (1.4, 4.4)  # via the zai override
    m3 = pricing.lookup("minimax-coding-plan", "MiniMax-M2.5").rates
    assert (m3.input, m3.output) == (0.3, 1.2)
    # Genuinely free tiers keep their $0.
    assert pricing.lookup("opencode", "big-pickle").rates.input == 0


def test_cursor_variant_priced_at_underlying_model_rate():
    # Cursor publishes no token pricing; the override uses grok-4.5's
    # published API rate for the underlying model.
    r = load_bundled().lookup("cursor-acp", "cursor-grok-4.5-high-fast").rates
    assert (r.input, r.output, r.cache_read) == (2, 6, 0.3)


def test_openrouter_section_wins_for_its_own_ids():
    # OpenRouter prices glm-5.2 cheaper than Z.AI's own list; that difference
    # is exactly why the openrouter section exists.
    price = load_bundled().lookup("openrouter", "z-ai/glm-5.2")
    assert price is not None and price.rates.input < 1.4


def test_aliases_resolve():
    price = load_bundled().lookup("openai", "gpt-4o-2024-11-20")
    assert price is not None and price.rates.input == 2.5


def test_dated_suffix_falls_back_to_base_model():
    price = load_bundled().lookup("openai", "gpt-4o-20241120")
    assert price is not None and price.rates.input == 2.5


def test_global_unique_model_name_resolves():
    # With resellers included, common names (gpt-4o-mini) are priced
    # differently across providers and must refuse to guess. A genuinely
    # unique name still resolves from any provider string.
    pricing = load_bundled()
    assert pricing.lookup("custom-gateway", "gpt-4o-mini") is None
    price = pricing.lookup("custom-gateway", "big-pickle")
    assert price is not None and price.rates.input == 0


def test_global_ambiguous_model_name_is_rejected():
    # glm-4.7 cache-read differs between Z.AI ($0.11) and Zen ($0.10) ⇒ no guess.
    assert load_bundled().lookup("custom-gateway", "glm-4.7") is None


def test_unknown_model_has_no_estimate():
    assert load_bundled().lookup("nobody", "mystery-model-9000") is None


# ── estimation math ───────────────────────────────────────────────────────────


def test_estimate_uses_all_components():
    # claude-sonnet-5: 2 / 0.2 / 2.5 / 10 per 1M
    est = load_bundled().estimate(
        row(
            "anthropic",
            "claude-sonnet-5",
            inp=1_000_000,
            cr=1_000_000,
            cw=1_000_000,
            out=1_000_000,
        )
    )
    assert est == pytest.approx(2 + 0.2 + 2.5 + 10)


def test_reasoning_billed_as_output():
    est = load_bundled().estimate(row("anthropic", "claude-opus-5", out=500_000, reas=500_000))
    assert est == pytest.approx(25)


def test_estimate_none_for_unpriced():
    assert load_bundled().estimate(row("nobody", "mystery")) is None


def test_long_context_tier_applies_above_threshold():
    pricing = load_bundled()  # gpt-5.6-sol: 5/30 short, 10/45 above 272k
    short = pricing.estimate(row("openai", "gpt-5.6-sol", inp=272_000))
    long = pricing.estimate(row("openai", "gpt-5.6-sol", inp=272_001))
    assert short == pytest.approx(272_000 * 5 / 1_000_000)
    assert long == pytest.approx(272_001 * 10 / 1_000_000)


def test_long_context_counts_cached_tokens_toward_threshold():
    pricing = load_bundled()
    est = pricing.estimate(
        row("openai", "gpt-5.6-sol", inp=1, cr=272_000)  # 272_001 total input
    )
    assert est == pytest.approx((1 * 10 + 272_000 * 1) / 1_000_000)


def test_estimate_row_uses_default_pricing():
    est = estimate_row(row("moonshotai", "kimi-k3", inp=1_000_000))
    assert est == pytest.approx(3)
    assert default_pricing().lookup("moonshotai", "kimi-k3") is not None


# ── user override ─────────────────────────────────────────────────────────────


def test_user_override_path_from_env(monkeypatch, tmp_path):
    custom = tmp_path / "my-prices.toml"
    custom.write_text('[openai."gpt-4o-mini"]\ninput = 99\noutput = 199\n')
    monkeypatch.setenv("OC_USAGE_PRICES", str(custom))
    assert user_price_path() == custom

    pricing, src = load()
    assert src == str(custom)
    r = pricing.lookup("openai", "gpt-4o-mini").rates
    assert (r.input, r.output) == (99, 199)


def test_user_override_via_xdg_default(monkeypatch, tmp_path):
    monkeypatch.delenv("OC_USAGE_PRICES", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "oc-usage"
    cfg_dir.mkdir()
    (cfg_dir / "prices.toml").write_text(
        '[acme."turbo-1"]\ninput = 1\noutput = 2\ncache_read = 0.1\n'
    )

    pricing, src = load()
    assert src is not None
    r = pricing.lookup("acme", "turbo-1").rates
    assert (r.input, r.output, r.cache_read) == (1, 2, 0.1)
    # bundled entries still work alongside the new provider
    assert pricing.lookup("anthropic", "claude-opus-5").rates.input == 5


def test_override_replaces_bundled_entry_including_aliases(monkeypatch, tmp_path):
    # Bundled xai.grok-4.20 carries non-dated aliases (grok-4.20-multi-agent,
    # ...). A user override replaces the whole entry, so those bundled aliases
    # must go. (Dated aliases like gpt-4o-20241120 still resolve afterwards —
    # by design — because the undating fallback strips the suffix.)
    custom = tmp_path / "prices.toml"
    custom.write_text('[xai."grok-4.20"]\ninput = 9\noutput = 9\n')
    monkeypatch.setenv("OC_USAGE_PRICES", str(custom))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    pricing, _ = load()
    assert pricing.lookup("xai", "grok-4.20").rates.input == 9
    assert pricing.lookup("xai", "grok-4.20-multi-agent") is None


def test_user_long_context_tier(monkeypatch, tmp_path):
    custom = tmp_path / "prices.toml"
    custom.write_text(
        '[acme."big-1"]\ninput = 1\noutput = 2\n'
        '[acme."big-1".long_context]\nthreshold = 1000\ninput = 4\noutput = 8\n'
    )
    monkeypatch.setenv("OC_USAGE_PRICES", str(custom))
    pricing, _ = load()
    assert pricing.estimate(row("acme", "big-1", inp=1_000)) == pytest.approx(0.001)
    assert pricing.estimate(row("acme", "big-1", inp=1_001)) == pytest.approx(0.004004)


def test_invalid_user_file_raises_with_path(monkeypatch, tmp_path):
    bad = tmp_path / "prices.toml"
    bad.write_text("this is not toml {{{")
    monkeypatch.setenv("OC_USAGE_PRICES", str(bad))
    with pytest.raises(PricingError, match="invalid TOML"):
        load()


def test_negative_price_rejected():
    with pytest.raises(PricingError, match="negative"):
        Pricing([("x", {"m": {"input": -1, "output": 2}})])


def test_missing_required_key_rejected():
    with pytest.raises(PricingError, match="input"):
        Pricing([("x", {"m": {"output": 2}})])


def test_bad_long_context_threshold_rejected():
    with pytest.raises(PricingError, match="threshold"):
        Pricing([("x", {"m": {"input": 1, "output": 2, "long_context": {"input": 3}}})])


# ── aggregation with pricing ──────────────────────────────────────────────────


def test_aggregate_estimates_via_price_list():
    report = aggregate(
        [
            row("openai", "gpt-5.6-sol", inp=100_000, out=10_000, reas=10_000),
            row("nobody", "mystery", inp=1_000_000),
        ]
    )
    assert report.totals.priced_turns == 1
    assert report.totals.estimate_complete is False
    assert report.totals.estimated_cost == pytest.approx(0.5 + 0.6)

    sol = report.by_model[("openai", "gpt-5.6-sol", "")]
    assert sol.estimated_cost == pytest.approx(1.1)
    mystery = report.by_model[("nobody", "mystery", "")]
    assert mystery.estimated_cost == 0.0 and mystery.priced_turns == 0


def test_free_models_estimate_to_zero_and_count_as_priced():
    # Zen free tiers are priced 0 — the estimate exists, it is just $0.
    report = aggregate([row("opencode", "big-pickle", inp=1000, out=500)])
    assert report.totals.estimate_complete is True
    assert report.totals.estimated_cost == 0.0
    assert report.totals.priced_turns == 1
