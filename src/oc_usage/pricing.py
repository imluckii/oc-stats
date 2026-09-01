"""Provider-aware cost estimates from the bundled ``prices.toml`` catalog.

The bundled price list (US dollars per 1M tokens) is human-editable and every
entry was verified against the provider's official pricing page. A user
override at ``~/.config/oc-usage/prices.toml`` (or ``$OC_USAGE_PRICES``) is
merged over the bundled one, so a personal file only needs the entries being
changed. Models whose pricing could not be verified are deliberately absent —
they get no estimate rather than a guess.

Matching order (case-insensitive, first hit wins):
    1. exact ``(provider, model)``, with custom provider ids falling back to
       their vendor prefix (``openai-anchit`` → ``openai``;
       ``zai-coding-plan-cn`` → ``zai-coding-plan`` → ``zai``)
    2. ``vendor/model`` ids — ``anthropic/claude-opus-5`` under any provider
       matches the ``anthropic`` entry ``claude-opus-5``
    3. the same two lookups with dated release suffixes (``-20241120``,
       ``-2026-04``) stripped from the model name
    4. the bare model name, but only when every provider in the merged list
       prices that model identically (ambiguity ⇒ no estimate; entries that
       agree on input/output are merged by majority vote on the cache tiers)

Models with a ``[long_context]`` table bill requests whose cached+uncached
input exceeds ``threshold`` tokens at the higher tier.
"""

from __future__ import annotations

import math
import os
import re
import sys
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oc_usage.models import UsageRow

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

TOKENS_PER_UNIT = 1_000_000
APP_DIR = "oc-usage"
PRICE_FILE = "prices.toml"
# Trailing release-date suffixes: gpt-4o-20241120, kimi-k2-0905, glm-5-2026-04.
_DATED_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|\d{4})$")


class PricingError(RuntimeError):
    """The price list exists but cannot be used (unreadable or malformed)."""


@dataclass(frozen=True)
class Rates:
    """USD per 1M tokens for one model. Missing cache tiers bill as 0."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class ModelPrice:
    """Standard rates plus an optional long-context tier for one model."""

    rates: Rates
    long_context: tuple[int, Rates] | None = None  # (threshold, rates)


def _rates_from(raw: object, where: str) -> Rates:
    if not isinstance(raw, dict):
        raise PricingError(f"{where}: model entry must be a table")
    try:
        r = Rates(
            input=float(raw["input"]),
            output=float(raw["output"]),
            cache_read=float(raw.get("cache_read", 0.0)),
            cache_write=float(raw.get("cache_write", 0.0)),
        )
    except KeyError as exc:
        raise PricingError(f"{where}: missing required key '{exc.args[0]}'") from exc
    except (TypeError, ValueError) as exc:
        raise PricingError(f"{where}: prices must be numbers") from exc
    values = (r.input, r.output, r.cache_read, r.cache_write)
    if min(values) < 0 or not all(math.isfinite(v) for v in values):
        raise PricingError(f"{where}: prices must be finite and not negative")
    return r


def _model_price_from(raw: dict, where: str) -> ModelPrice:
    rates = _rates_from(raw, where)
    long_raw = raw.get("long_context")
    if long_raw is None:
        return ModelPrice(rates, None)
    if not isinstance(long_raw, dict):
        raise PricingError(f"{where}: long_context must be a table")
    where += ".long_context"
    if "threshold" not in long_raw:
        raise PricingError(f"{where}: missing required key 'threshold'")
    try:
        threshold_number = float(long_raw["threshold"])
    except (TypeError, ValueError) as exc:
        raise PricingError(f"{where}: threshold must be a number") from exc
    if (
        not math.isfinite(threshold_number)
        or threshold_number <= 0
        or threshold_number != int(threshold_number)
    ):
        raise PricingError(f"{where}: threshold must be a positive finite integer")
    threshold = int(threshold_number)
    return ModelPrice(rates, (threshold, _rates_from(long_raw, where)))


def _aliases(raw: dict, provider: str, model: str) -> list[str]:
    aliases = raw.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise PricingError(f'[{provider}."{model}"]: aliases must be a list of strings')
    return [a.strip().lower() for a in aliases if a.strip()]


def _consensus_price(options: dict[ModelPrice, int]) -> ModelPrice | None:
    """Merge same-model entries that agree on what actually matters.

    Resellers resync at different times, so cache tiers drift a step while
    input/output (the bulk of every bill) stay in lockstep — e.g. a model
    newly listed under its gateway name (``anthropic/claude-opus-5``) is
    quoted identically everywhere except one stale cache_write. When every
    entry agrees on input/output, vote each remaining field weighted by how
    many providers back it instead of refusing to estimate; a lone outlier
    loses to the consensus.
    """
    if len({(p.rates.input, p.rates.output) for p in options}) != 1:
        return None

    def majority(field: Callable[[ModelPrice], Hashable]) -> Hashable:
        counts: dict[Hashable, int] = {}
        for price, weight in options.items():
            key = field(price)
            counts[key] = counts.get(key, 0) + weight
        best = max(counts.values())
        return next(k for k, v in counts.items() if v == best)

    rates = majority(lambda p: p.rates)
    long_context = majority(lambda p: p.long_context)
    return ModelPrice(rates, long_context)  # type: ignore[arg-type]


class Pricing:
    """A merged, lookup-ready price table."""

    def __init__(self, tables: Iterable[tuple[str, dict]]) -> None:
        self._by_provider: dict[str, dict[str, ModelPrice]] = {}
        for provider, models in tables:
            bucket = self._by_provider.setdefault(provider.strip().lower(), {})
            for model, raw in models.items():
                where = f'[{provider}."{model}"]'
                price = _model_price_from(raw, where)
                key = model.strip().lower()
                bucket[key] = price
                for alias in _aliases(raw, provider, model):
                    bucket[alias] = price
        self._global: dict[str, dict[ModelPrice, int]] = {}
        for models in self._by_provider.values():
            for model, price in models.items():
                bucket = self._global.setdefault(model, {})
                bucket[price] = bucket.get(price, 0) + 1

    def _provider_candidates(self, provider: str) -> list[str]:
        """Provider buckets to try, longest first.

        OpenCode lets users name custom providers freely, and subscriptions
        get their own gateway ids — ``openai-anchit``, ``zai-coding-plan-cn``.
        After the exact id, progressively trim trailing hyphen segments so a
        custom id falls back to the vendor table it was derived from
        (``openai-anchit`` → ``openai``; ``zai-coding-plan-cn`` →
        ``zai-coding-plan`` → ``zai``).
        """
        parts = provider.split("-")
        return ["-".join(parts[:i]) for i in range(len(parts), 0, -1)]

    def lookup(self, provider: str, model: str) -> ModelPrice | None:
        """Resolve pricing for a ``(provider, model)`` pair, or ``None``."""
        provider = provider.strip().lower()
        model = model.strip().lower()
        # Try the exact name, then the dated-suffix-stripped name
        # (gpt-4o-20241120 → gpt-4o), each against the provider's own table
        # (and trimmed vendor prefixes for custom provider ids), any
        # "vendor/" gateway prefix in the model, and finally an unambiguous
        # global name (rare now that resellers are included — most common
        # names differ per provider and correctly refuse to guess).
        for candidate in (model, _DATED_SUFFIX.sub("", model)):
            for bucket_id in self._provider_candidates(provider):
                bucket = self._by_provider.get(bucket_id)
                if bucket is not None:
                    price = bucket.get(candidate)
                    if price is not None:
                        return price
                    if "/" in candidate:  # gateway ids like "anthropic/claude-opus-5"
                        vendor, rest = candidate.split("/", 1)
                        price = bucket.get(rest)
                        if price is not None:
                            return price
                        price = self._by_provider.get(vendor, {}).get(rest)
                        if price is not None:
                            return price
            options = self._global.get(candidate)
            if options is None:
                continue
            if len(options) == 1:
                return next(iter(options))
            consensus = _consensus_price(options)
            if consensus is not None:
                return consensus
        return None

    def estimate(self, row: UsageRow) -> float | None:
        """Estimated USD cost of one row, or ``None`` when unpriced.

        Reasoning tokens are billed as output by every provider in the list.
        """
        price = self.lookup(row.provider, row.model)
        if price is None:
            return None
        rates = price.rates
        input_tokens = row.input + row.cache_read + row.cache_write
        if price.long_context is not None:
            threshold, long_rates = price.long_context
            if input_tokens > threshold:
                rates = long_rates
        return (
            row.input * rates.input
            + row.cache_read * rates.cache_read
            + row.cache_write * rates.cache_write
            + (row.output + row.reasoning) * rates.output
        ) / TOKENS_PER_UNIT

    def covers(self, provider: str, model: str) -> bool:
        return self.lookup(provider, model) is not None


# ── loading ───────────────────────────────────────────────────────────────────


def _strip_meta(data: dict) -> dict[str, dict]:
    return {k: v for k, v in data.items() if k not in ("schema", "updated")}


def _bundled_path() -> resources.abc.Traversable:
    return resources.files("oc_usage").joinpath(PRICE_FILE)


def _bundled_tables() -> dict[str, dict]:
    if tomllib is None:  # pragma: no cover - 3.10 without tomli installed
        raise PricingError("parsing TOML needs Python 3.11+, or 'pip install tomli' on 3.10")
    try:
        data = tomllib.loads(_bundled_path().read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - shipped file
        raise PricingError(f"bundled {PRICE_FILE} is invalid: {exc}") from exc
    return _strip_meta(data)


def load_bundled() -> Pricing:
    """Load the price list shipped inside the package."""
    return Pricing(_bundled_tables().items())


def user_price_path(environ: dict[str, str] | None = None) -> Path:
    """Where the user's override file lives (may not exist)."""
    env = os.environ if environ is None else environ
    override = env.get("OC_USAGE_PRICES")
    if override:
        return Path(override)
    config_root = env.get("XDG_CONFIG_HOME")
    base = Path(config_root) if config_root else Path.home() / ".config"
    return base / APP_DIR / PRICE_FILE


def _parse_user(path: Path) -> dict[str, dict]:
    if tomllib is None:  # pragma: no cover - 3.10 without tomli installed
        raise PricingError("parsing TOML needs Python 3.11+, or 'pip install tomli' on 3.10")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise PricingError(f"cannot read user price file ({path}): {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PricingError(f"invalid TOML in user price file ({path}): {exc}") from exc
    return _strip_meta(data)


def load(environ: dict[str, str] | None = None) -> tuple[Pricing, str | None]:
    """Bundled prices merged with the user's override, when present.

    A user entry with the same provider+model fully replaces the bundled one
    (its aliases included). Returns the merged pricing plus a label describing
    the override in use (``None`` when only bundled prices apply).
    """
    merged = _bundled_tables()
    user_path = user_price_path(environ)
    if user_path.exists():
        user = _parse_user(user_path)
        for provider, models in user.items():
            bucket = dict(merged.get(provider, {}))
            bucket.update(models)
            merged[provider] = bucket
        return Pricing(merged.items()), str(user_path)
    return Pricing(merged.items()), None


# ── default instance used by aggregation ──────────────────────────────────────

_default: Pricing | None = None


def default_pricing() -> Pricing:
    """The process-wide pricing (bundled + user override), loaded lazily."""
    global _default
    if _default is None:
        _default, _ = load()
    return _default


def estimate_row(row: UsageRow) -> float | None:
    """Estimate one turn at list prices; ``None`` when the model is unpriced."""
    return default_pricing().estimate(row)


def debug(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI helper
    """Tiny ``python -m oc_usage.pricing <provider> <model>`` lookup."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2:
        pricing, src = load()
        price = pricing.lookup(argv[0], argv[1])
        if price is None:
            print(f"no price for {argv[0]}/{argv[1]}" + (f" (override: {src})" if src else ""))
            return 1
        r = price.rates
        print(
            f"{argv[0]}/{argv[1]}: input={r.input} cache_read={r.cache_read} "
            f"cache_write={r.cache_write} output={r.output} (per 1M tokens)"
        )
        if price.long_context is not None:
            threshold, lr = price.long_context
            print(
                f"  > {threshold} input tokens: input={lr.input} "
                f"cache_read={lr.cache_read} cache_write={lr.cache_write} output={lr.output}"
            )
        return 0
    print("usage: python -m oc_usage.pricing PROVIDER MODEL", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - manual lookup helper
    raise SystemExit(debug())
