#!/usr/bin/env python3
"""Refresh the bundled first-party model prices from models.dev."""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

SOURCE = "https://models.dev/api.json"
PROVIDERS = ("openai", "anthropic", "zai", "deepseek", "moonshotai")
OUTPUT = Path(__file__).parents[1] / "src" / "oc_usage" / "prices.json"


def number(value, fallback=None):
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return value
    return fallback


def normalize(cost: dict) -> dict[str, object] | None:
    input_rate = number(cost.get("input"))
    output_rate = number(cost.get("output"))
    if input_rate is None or output_rate is None:
        return None

    result: dict[str, object] = {
        "input": input_rate,
        "cache_read": number(cost.get("cache_read"), input_rate),
        "cache_write": number(cost.get("cache_write"), input_rate),
        "output": output_rate,
    }
    for tier in cost.get("tiers", []):
        rule = tier.get("tier", {})
        if rule.get("type") != "context" or number(rule.get("size")) is None:
            continue
        result["long_context"] = {
            "threshold": rule["size"],
            "input": number(tier.get("input"), input_rate),
            "cache_read": number(tier.get("cache_read"), result["cache_read"]),
            "cache_write": number(tier.get("cache_write"), result["cache_write"]),
            "output": number(tier.get("output"), output_rate),
        }
        break
    return result


def build_catalog(data: dict) -> dict[str, object]:
    models: dict[str, dict[str, object]] = {}
    for provider in PROVIDERS:
        for model_id, model in data[provider]["models"].items():
            pricing = normalize(model.get("cost", {}))
            if pricing is None:
                continue
            existing = models.get(model_id)
            if existing is not None and existing != pricing:
                raise ValueError(f"conflicting first-party prices for {model_id}")
            models[model_id] = pricing
    return {"source": SOURCE, "models": dict(sorted(models.items()))}


def main() -> None:
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "oc-stats-price-updater"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    payload = build_catalog(data)
    if len(payload["models"]) < 40:
        raise RuntimeError("price source returned too few models; refusing to overwrite catalog")
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(OUTPUT)


if __name__ == "__main__":
    main()
