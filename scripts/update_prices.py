#!/usr/bin/env python3
"""Regenerate ``src/oc_usage/prices.toml`` from the models.dev catalog.

Usage::

    python scripts/update_prices.py            # fetch and write
    python scripts/update_prices.py --check    # exit 1 if the file is stale

Every model that carries ``input``/``output`` pricing in
https://models.dev/api.json is emitted under its provider, in US dollars per
1M tokens. Missing ``cache_read``/``cache_write`` are omitted (they bill as 0
in oc-usage — never defaulted to the input rate). Context tiers become
``[provider."model".long_context]`` tables.

Entries in :data:`OVERRIDES` below replace or add to the fetched data. Each
one was verified against the provider's official pricing page; keep this list
short and cite the source. Manual edits to generated sections are
overwritten on the next run — put corrections here instead.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from datetime import date
from pathlib import Path

SOURCE = "https://models.dev/api.json"
USER_AGENT = "oc-stats-price-sync/1.0 (+https://github.com/imluckii/oc-stats)"
REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "src" / "oc_usage" / "prices.toml"

# Hand-verified corrections, applied after fetching. Keys are
# (provider, model); values use the same shape as the TOML entries.
OVERRIDES: dict[tuple[str, str], dict] = {
    # Zen's own price list (opencode.ai/docs/zen, checked 2026-08-14) prices
    # Terra at $2/$12 with a >272K tier of $4/$18; models.dev carried 2.5/15.
    ("opencode", "gpt-5.6-terra"): {
        "input": 2,
        "output": 12,
        "cache_read": 0.2,
        "cache_write": 2.5,
        "long_context": {
            "threshold": 272000,
            "input": 4,
            "output": 18,
            "cache_read": 0.4,
            "cache_write": 5,
        },
    },
    # DeepSeek switches to peak/off-peak billing on 2026-08-16
    # (api-docs.deepseek.com). Off-peak covers 17 of 24 hours; peak is 2x.
    ("deepseek", "deepseek-v4-flash"): {
        "input": 0.22,
        "output": 0.66,
        "cache_read": 0.007,
    },
    ("deepseek", "deepseek-v4-pro"): {
        "input": 0.66,
        "output": 1.98,
        "cache_read": 0.022,
    },
    # models.dev dropped Mistral's cached-input rate; official pricing page
    # (docs.mistral.ai/inference/pricing, checked 2026-08-14) lists $0.05.
    ("mistral", "mistral-large-latest"): {
        "input": 0.5,
        "output": 1.5,
        "cache_read": 0.05,
    },
    # OpenAI "fast" service-tier variants (platform.openai.com/docs/pricing,
    # Fast mode table, checked 2026-08-14). models.dev does not list them.
    ("openai", "gpt-5.6-sol-fast"): {
        "input": 10,
        "output": 60,
        "cache_read": 1,
        "cache_write": 12.5,
        "long_context": {
            "threshold": 272000,
            "input": 20,
            "output": 90,
            "cache_read": 2,
            "cache_write": 25,
        },
    },
    ("openai", "gpt-5.6-terra-fast"): {
        "input": 4,
        "output": 24,
        "cache_read": 0.4,
        "cache_write": 5,
        "long_context": {
            "threshold": 272000,
            "input": 8,
            "output": 36,
            "cache_read": 0.8,
            "cache_write": 10,
        },
    },
    ("openai", "gpt-5.6-luna-fast"): {
        "input": 0.4,
        "output": 2.4,
        "cache_read": 0.04,
        "cache_write": 0.5,
        "long_context": {
            "threshold": 272000,
            "input": 0.8,
            "output": 3.6,
            "cache_read": 0.08,
            "cache_write": 1,
        },
    },
    # GLM-5.3 released 2026-08-14 with models.dev listing $0; now listed at
    # the official rate (docs.z.ai, verified 2026-09-02) — override dropped.
    # Cursor serves xAI's grok-4.5 under its own variant name and publishes no
    # token pricing (subscription product). Priced at the underlying model's
    # API rate (docs.x.ai, checked 2026-08-14): $2 in / $0.30 cached / $6 out.
    ("cursor-acp", "cursor-grok-4.5-high-fast"): {
        "input": 2,
        "output": 6,
        "cache_read": 0.3,
    },
}

HEADER = """\
# ═══════════════════════════════════════════════════════════════════════════
#  oc-stats price list
#
#  GENERATED from https://models.dev/api.json by scripts/update_prices.py —
#  do not edit provider sections by hand; they are overwritten on the next
#  refresh. To correct or pin a price, add it to OVERRIDES in that script.
#  Hand-verified corrections carry a "(verified …)" note below.
#
#  HOW TO CUSTOMIZE (you only need the entries you want to change):
#    1. Create this file (it may contain ONLY the entries you edit):
#         Linux : ~/.config/oc-usage/prices.toml
#         macOS : ~/.config/oc-usage/prices.toml
#         or set $XDG_CONFIG_HOME, or point OC_USAGE_PRICES at any file
#    2. Copy the [provider."model"] block you want from below and edit the
#       numbers. Your file is merged over this one and always wins.
#
#  PRICES: US dollars per 1,000,000 tokens.
#    input       — required, non-cached input tokens
#    output      — required, output tokens (reasoning tokens are billed as
#                  output by every provider here, so they use this rate too)
#    cache_read  — optional, cached input tokens (0 if omitted)
#    cache_write — optional, writing to the prompt cache (0 if omitted)
#
#    Some models bill long requests at a higher rate. Add a sub-table:
#      [openai."gpt-5.6-sol".long_context]
#      threshold = 272000        # input + cache tokens that switch the tier
#      input = 10                # same fields as above
#      output = 45
#
#  HOW MODELS ARE MATCHED (first hit wins):
#    1. exact provider + model (case-insensitive)
#    2. "vendor/model" ids: a model like anthropic/claude-opus-5 also matches
#       the "anthropic" provider entry claude-opus-5 (gateway-style ids)
#    3. the bare model name, only when every provider prices it identically
#       (ambiguity ⇒ no estimate — resellers differ, so most common names
#       need a provider or vendor prefix)
#
#  CAVEAT: reseller/gateway prices are copied from models.dev as-is and may
#  trail or shade the provider's own list. First-party sections (anthropic,
#  openai, google, xai, deepseek, mistral, moonshotai, zai, minimax, groq,
#  cohere, perplexity, alibaba, opencode, …) are the reference.
# ═══════════════════════════════════════════════════════════════════════════

schema = 1
updated = "{updated}"
"""


def number(value):
    """A usable non-negative finite number, or ``None``."""
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return value
    return None


def fmt(v) -> str:
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    return s if s else "0"


def rates_lines(e: dict, indent: str = "") -> list[str]:
    lines = [f"{indent}input = {fmt(e['input'])}"]
    if "cache_read" in e:
        lines.append(f"{indent}cache_read = {fmt(e['cache_read'])}")
    if "cache_write" in e:
        lines.append(f"{indent}cache_write = {fmt(e['cache_write'])}")
    lines.append(f"{indent}output = {fmt(e['output'])}")
    return lines


def long_context_from(tiers: list) -> dict | None:
    """The single context tier, if models.dev publishes one."""
    best = None
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        meta = tier.get("tier") or {}
        if meta.get("type") != "context":
            continue
        size = number(meta.get("size"))
        if size is None or size <= 0:
            continue
        entry = {
            "threshold": int(size),
            "input": number(tier.get("input")),
            "output": number(tier.get("output")),
        }
        for key in ("cache_read", "cache_write"):
            v = number(tier.get(key))
            if v is not None:
                entry[key] = v
        if entry["input"] is None or entry["output"] is None:
            continue
        if best is None or entry["threshold"] > best["threshold"]:
            best = entry
    return best


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def build_tables(catalog: dict) -> dict[str, dict[str, dict]]:
    tables: dict[str, dict[str, dict]] = {}
    for provider, info in sorted(catalog.items()):
        if not isinstance(info, dict):
            continue
        bucket: dict[str, dict] = {}
        for model, meta in sorted(info.get("models", {}).items()):
            if not isinstance(meta, dict):
                continue
            cost = meta.get("cost") or {}
            entry = {
                "input": number(cost.get("input")),
                "output": number(cost.get("output")),
            }
            for key in ("cache_read", "cache_write"):
                v = number(cost.get(key))
                if v is not None:
                    entry[key] = v
            if entry["input"] is None or entry["output"] is None:
                continue
            tiers = cost.get("tiers")
            if isinstance(tiers, list):
                long = long_context_from(tiers)
                if long is not None:
                    entry["long_context"] = long
            bucket[model] = entry
        if bucket:
            tables[provider] = bucket

    for (provider, model), override in OVERRIDES.items():
        bucket = tables.setdefault(provider, {})
        # Full replacement (like user overrides): verified values win and any
        # stale generated fields — tiers included — are dropped.
        bucket[model] = dict(override)

    fill_subscription_prices(tables)
    return tables


def _entry_key(entry: dict) -> tuple:
    return (
        entry["input"],
        entry["output"],
        entry.get("cache_read", 0),
        entry.get("cache_write", 0),
        json.dumps(entry.get("long_context"), sort_keys=True),
    )


def fill_subscription_prices(tables: dict[str, dict[str, dict]]) -> int:
    """Reprice $0 subscription-gateway entries at the vendor's API rate.

    models.dev lists coding-plan gateways (``zai-coding-plan``,
    ``minimax-coding-plan``, …) at $0/token because they are subscription
    products. That shadows the real per-token price of the same model from
    the first-party API, so every all-zero entry is backfilled from the same
    model under a hyphen-prefix of the provider (``zai-coding-plan`` →
    ``zai``) or, failing that, an unambiguous non-zero listing elsewhere.
    Genuinely free models (``big-pickle``, ``*-free`` tiers with no paid
    counterpart) keep their $0.
    """
    filled = 0
    for provider, models in tables.items():
        zero = [m for m, e in models.items() if e["input"] == 0 and e["output"] == 0]
        if not zero:
            continue
        parts = provider.split("-")
        prefixes = ["-".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]
        for model in zero:
            price = None
            for prefix in prefixes:
                candidate = tables.get(prefix, {}).get(model)
                if candidate and (candidate["input"] or candidate["output"]):
                    price = candidate
                    break
            if price is None:
                nonzero = {
                    _entry_key(e): e
                    for other, ms in tables.items()
                    for name, e in ms.items()
                    if name == model and (e["input"] or e["output"])
                }
                if len(nonzero) == 1:
                    price = next(iter(nonzero.values()))
            if price is not None:
                models[model] = deepcopy_entry(price)
                filled += 1
    return filled


def deepcopy_entry(entry: dict) -> dict:
    copy = dict(entry)
    if "long_context" in copy:
        copy["long_context"] = dict(copy["long_context"])
    return copy


def render(tables: dict[str, dict[str, dict]]) -> str:
    parts = [HEADER.format(updated=date.today().isoformat())]
    for provider, models in tables.items():
        # Quote the header: provider ids can contain dots ("wafer.ai").
        parts.append(f'\n# ── {provider} ── source: models.dev\n["{provider}"]')
        for model, entry in models.items():
            if (provider, model) in OVERRIDES:
                parts.append("# verified - see OVERRIDES in scripts/update_prices.py")
            parts.append(f'\n["{provider}"."{model}"]')
            parts.extend(rates_lines(entry))
            if "long_context" in entry:
                lc = entry["long_context"]
                parts.append(
                    f'\n["{provider}"."{model}".long_context]  # > {lc["threshold"]} input+cache tokens'
                )
                parts.append(f"threshold = {lc['threshold']}")
                parts.extend(rates_lines(lc, indent=""))
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 (with a diff summary) if prices.toml is out of date",
    )
    args = ap.parse_args(argv)

    tables = build_tables(fetch(SOURCE))
    text = render(tables)
    models_n = sum(len(b) for b in tables.values())
    zero_left = sum(
        1 for ms in tables.values() for e in ms.values() if e["input"] == 0 and e["output"] == 0
    )

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        # Compare content, ignoring the generated-date line so a day rollover
        # alone does not count as staleness.
        strip_date = lambda s: "\n".join(  # noqa: E731
            line for line in s.splitlines() if not line.startswith("updated =")
        )
        if strip_date(current) == strip_date(text):
            print(f"prices.toml up to date ({models_n} models)")
            return 0
        print(f"prices.toml is stale: {models_n} models upstream")
        return 1

    OUTPUT.write_text(text, encoding="utf-8")
    print(
        f"wrote {OUTPUT}: {models_n} models, {len(tables)} providers "
        f"({zero_left} priced at $0 - genuinely free tiers)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
