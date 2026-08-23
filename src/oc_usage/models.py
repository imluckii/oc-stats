"""Normalized data models and aggregation for OpenCode usage rows.

The service transport (:mod:`oc_usage.service`) converts assistant messages
returned by OpenCode's running V2 service into a single :class:`UsageRow` shape.
This module is transport-agnostic and responsible only for summing those rows
into buckets.

Token total semantics
---------------------
The component total of a turn is::

    total = input + cache_read + cache_write + output + reasoning

We always compute ``total`` from the explicit components and never estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from oc_usage.pricing import estimate_row

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# Sentinel for rows whose provider/model could not be determined.
UNKNOWN = "(unknown)"

# Reconciliation rows: usage OpenCode recorded on the session ledger without a
# retained assistant message (title generation, compaction, reverted requests).
UNATTRIBUTED = "(unattributed)"
INTERNAL_USAGE = "(internal usage)"

# (provider, model, variant) — the composite key used to group model rows.
ModelKey = tuple[str, str, str]


def component_total(
    input: int,
    cache_read: int,
    cache_write: int,
    output: int,
    reasoning: int,
) -> int:
    """Raw component total: input + both cache buckets + output + reasoning."""
    return input + cache_read + cache_write + output + reasoning


@dataclass(frozen=True)
class UsageRow:
    """A single normalized assistant turn.

    ``cost`` is the cost OpenCode recorded for the turn (0.0 when absent).
    ``time_created`` is the Unix epoch in milliseconds, or ``0`` when unknown.
    ``tokens_known`` is False when the source message carried no token object,
    so the zero components mean "unaccounted", not "free".
    """

    provider: str
    model: str
    variant: str
    input: int
    cache_read: int
    cache_write: int
    output: int
    reasoning: int
    cost: float
    time_created: int
    tokens_known: bool = True

    @property
    def total(self) -> int:
        return component_total(
            self.input, self.cache_read, self.cache_write, self.output, self.reasoning
        )


@dataclass
class Bucket:
    """An aggregated group (totals, a provider, or a model/variant)."""

    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0
    recorded_cost: float = 0.0
    estimated_cost: float = 0.0
    priced_turns: int = 0
    turns: int = 0

    def add(self, row: UsageRow) -> None:
        self.input += row.input
        self.cache_read += row.cache_read
        self.cache_write += row.cache_write
        self.output += row.output
        self.reasoning += row.reasoning
        self.recorded_cost += row.cost
        estimate = estimate_row(row)
        # A turn without token accounting cannot be priced honestly, even when
        # the model has a rate: its zero components are unknown, not free.
        if estimate is not None and row.tokens_known:
            self.estimated_cost += estimate
            self.priced_turns += 1
        self.turns += 1

    @property
    def total(self) -> int:
        return component_total(
            self.input, self.cache_read, self.cache_write, self.output, self.reasoning
        )

    @property
    def estimate_complete(self) -> bool:
        return self.priced_turns == self.turns


@dataclass
class Report:
    """Fully aggregated usage for the running OpenCode service."""

    totals: Bucket
    by_provider: dict[str, Bucket]
    by_model: dict[ModelKey, Bucket]
    span: tuple[datetime, datetime] | None
    source: str


def aggregate(rows: Iterable[UsageRow], *, source: str = "OpenCode data") -> Report:
    """Sum rows into totals, per-provider, and per-model buckets."""
    totals = Bucket()
    by_provider: dict[str, Bucket] = {}
    by_model: dict[ModelKey, Bucket] = {}
    times: list[int] = []
    for row in rows:
        totals.add(row)
        by_provider.setdefault(row.provider, Bucket()).add(row)
        by_model.setdefault((row.provider, row.model, row.variant), Bucket()).add(row)
        if row.time_created:
            times.append(row.time_created)

    span: tuple[datetime, datetime] | None = None
    if times:
        lo = datetime.fromtimestamp(min(times) / 1000, tz=timezone.utc)
        hi = datetime.fromtimestamp(max(times) / 1000, tz=timezone.utc)
        span = (lo, hi)

    return Report(
        totals=totals,
        by_provider=by_provider,
        by_model=by_model,
        span=span,
        source=source,
    )


@dataclass(frozen=True)
class SessionLedger:
    """OpenCode's per-session aggregate usage — its authoritative retained ledger.

    The session ledger counts every provider call billed to the session:
    assistant turns, title generation, compaction, and requests that a
    committed revert later removed from the transcript. It deliberately
    excludes history a fork copied from its parent (the fork starts at zero).
    """

    input: int
    cache_read: int
    cache_write: int
    output: int
    reasoning: int
    cost: float
    time_created: int


def reconcile_ledger(kept: Sequence[float], ledger: SessionLedger) -> UsageRow | None:
    """Cover ledger usage that retained assistant messages do not represent.

    ``kept`` is ``(input, cache_read, cache_write, output, reasoning, cost)``
    summed over the session's counted messages. The positive per-component
    difference becomes one internal-usage row attributed to no model; negative
    differences are clamped away because legacy sessions can carry messages
    without a populated ledger. Returns ``None`` when messages already cover
    the ledger.
    """
    ledger_components = (
        ledger.input,
        ledger.cache_read,
        ledger.cache_write,
        ledger.output,
        ledger.reasoning,
    )
    diffs = [
        max(0, int(ledger_value) - int(kept_value))
        for ledger_value, kept_value in zip(ledger_components, kept[:5], strict=True)
    ]
    cost = max(0.0, ledger.cost - float(kept[5]))
    if not any(diffs) and cost <= 0:
        return None
    return UsageRow(
        provider=UNATTRIBUTED,
        model=INTERNAL_USAGE,
        variant="",
        input=diffs[0],
        cache_read=diffs[1],
        cache_write=diffs[2],
        output=diffs[3],
        reasoning=diffs[4],
        cost=cost,
        time_created=ledger.time_created,
    )
