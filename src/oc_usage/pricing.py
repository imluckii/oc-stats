"""API-equivalent cost estimates from the generated model price catalog."""

from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oc_usage.models import UsageRow


def _load_prices() -> dict[str, dict[str, object]]:
    path = files("oc_usage").joinpath("prices.json")
    return json.loads(path.read_text(encoding="utf-8"))["models"]


MODEL_PRICES = _load_prices()


def model_price(model: str) -> dict[str, object] | None:
    """Find an exact model price, accepting gateway prefixes and dated aliases."""
    model = model.rsplit("/", 1)[-1].lower()
    if model in MODEL_PRICES:
        return MODEL_PRICES[model]
    undated = re.sub(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$", "", model)
    return MODEL_PRICES.get(undated)


def estimate_row(row: UsageRow) -> float | None:
    """Estimate one turn at standard first-party API list prices."""
    pricing = model_price(row.model)
    if pricing is None:
        return None

    rates = pricing
    long_context = pricing.get("long_context")
    input_tokens = row.input + row.cache_read + row.cache_write
    if isinstance(long_context, dict) and input_tokens > int(long_context["threshold"]):
        rates = long_context

    return (
        row.input * float(rates["input"])
        + row.cache_read * float(rates["cache_read"])
        + row.cache_write * float(rates["cache_write"])
        + (row.output + row.reasoning) * float(rates["output"])
    ) / 1_000_000
