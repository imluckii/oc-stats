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

if TYPE_CHECKING:
    from collections.abc import Iterable

# Sentinel for rows whose provider/model could not be determined.
UNKNOWN = "(unknown)"

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

    ``cost`` defaults to ``0.0`` when the source message did not carry a cost.
    ``time_created`` is the Unix epoch in milliseconds, or ``0`` when unknown.
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
    cost: float = 0.0
    turns: int = 0

    def add(self, row: UsageRow) -> None:
        self.input += row.input
        self.cache_read += row.cache_read
        self.cache_write += row.cache_write
        self.output += row.output
        self.reasoning += row.reasoning
        self.cost += row.cost
        self.turns += 1

    @property
    def total(self) -> int:
        return component_total(
            self.input, self.cache_read, self.cache_write, self.output, self.reasoning
        )


@dataclass
class Report:
    """Fully aggregated usage for the running OpenCode service."""

    totals: Bucket
    by_provider: dict[str, Bucket]
    by_model: dict[ModelKey, Bucket]
    span: tuple[datetime, datetime] | None
    cost_tracked: bool


def aggregate(rows: Iterable[UsageRow]) -> Report:
    """Sum rows into totals, per-provider, and per-model buckets."""
    totals = Bucket()
    by_provider: dict[str, Bucket] = {}
    by_model: dict[ModelKey, Bucket] = {}
    times: list[int] = []
    any_cost = False

    for row in rows:
        totals.add(row)
        by_provider.setdefault(row.provider, Bucket()).add(row)
        by_model.setdefault((row.provider, row.model, row.variant), Bucket()).add(row)
        if row.time_created:
            times.append(row.time_created)
        if row.cost > 0:
            any_cost = True

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
        cost_tracked=any_cost,
    )
