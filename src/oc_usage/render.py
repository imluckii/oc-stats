"""Rendering for ``oc-usage``: a Rich human report and a JSON payload.

Width strategy
--------------
When stdout is a TTY, Rich auto-detects the terminal width. Text columns
(provider/model/variant) use ``overflow="fold"`` so long names wrap to the next
line instead of being cut; numeric columns are ``no_wrap`` and right-aligned so
token suffixes are never truncated.

When stdout is **not** a TTY (piped, captured, ``!oc-usage``, ``/stats``,
redirection) Rich would otherwise default to a narrow 80 columns and crop the
tables. We force a generous fixed width in that case so every value renders in
full, and we disable ANSI color so captured output is clean plain text.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from oc_usage.models import Bucket, Report

if TYPE_CHECKING:
    from rich.console import RenderableType

# Width used when stdout is not a TTY. Comfortably fits the per-model table
# (longest model name + 8 numeric columns) so nothing is truncated.
NON_TTY_WIDTH = 165

# Distinct colors cycled across providers for visual grouping.
PROVIDER_COLORS = [
    "bright_cyan",
    "bright_magenta",
    "bright_green",
    "bright_yellow",
    "bright_blue",
    "bright_red",
    "cyan",
    "magenta",
]


# ── number formatting ─────────────────────────────────────────────────────────


def fmt_full(n: int | float) -> str:
    """Integer with thousands separators."""
    return f"{int(n or 0):,}"


def fmt_compact(n: int | float) -> str:
    """Short human-readable: 1.2K / 3.4M / 5.6B."""
    n = float(n or 0)
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{int(n)}"


def money(c: float) -> str:
    if c is None or c == 0:
        return "$0.00"
    if c >= 1000:
        return f"${c:,.0f}"
    if c >= 1:
        return f"${c:,.2f}"
    return f"${c:.4f}".rstrip("0").rstrip(".")


def pct(part: float, whole: float) -> float:
    if not whole:
        return 0.0
    return part / whole * 100


# ── console construction ──────────────────────────────────────────────────────


def make_console(no_color: bool) -> Console:
    """Build a Rich console tuned for TTY vs non-TTY output."""
    is_tty = sys.stdout.isatty()
    width = None if is_tty else NON_TTY_WIDTH
    return Console(no_color=no_color or not is_tty, highlight=False, width=width)


# ── small renderable helpers ──────────────────────────────────────────────────


def _bar(frac: float, width: int = 30) -> Text:
    """A colored unicode progress bar as a Text renderable."""
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * width)
    t = Text()
    t.append("━" * filled, style="bold green")
    t.append("━" * (width - filled), style="grey42")
    return t


def _provider_color_map(by_provider: dict[str, Bucket]) -> dict[str, str]:
    names = sorted(by_provider.keys())
    return {name: PROVIDER_COLORS[i % len(PROVIDER_COLORS)] for i, name in enumerate(names)}


def _section(title: str, style: str = "cyan") -> Rule:
    return Rule(title=Text(f"  {title}  ", style=f"bold {style}"), style=style, align="left")


def _make_table(
    columns: list[tuple[str, str, str]],
    box_style: box.Box = box.SIMPLE_HEAVY,
    header_style: str = "bold cyan",
) -> Table:
    t = Table(
        box=box_style,
        header_style=header_style,
        border_style="grey50",
        pad_edge=False,
        padding=(0, 1),
    )
    for title, justify, _style in columns:
        # Numeric columns never wrap (so suffixes survive); text columns fold
        # long names instead of being cut.
        is_numeric = justify == "right"
        t.add_column(
            title,
            justify=justify,
            no_wrap=is_numeric,
            overflow="fold" if not is_numeric else "ellipsis",
        )
    return t


# ── report sections ───────────────────────────────────────────────────────────


def _build_header(report: Report, fmt_num) -> Panel:
    bits = [
        f"[bold]{fmt_num(report.totals.turns)}[/] turns",
        f"[bold]{len(report.by_provider)}[/] providers",
        f"[bold]{len(report.by_model)}[/] models",
    ]
    if report.span:
        lo, hi = report.span
        bits.append(f"[dim]{lo:%Y-%m-%d} → {hi:%Y-%m-%d}[/]")
    body = Text.from_markup("  ·  ".join(bits))
    return Panel(
        Align.left(body, vertical="middle"),
        title=Text("  ◇  OpenCode Usage · All Time  ", style="bold white on blue"),
        title_align="left",
        border_style="blue",
        box=box.DOUBLE_EDGE,
        padding=(0, 1),
    )


def _build_totals(report: Report, fmt_num) -> Table.grid:  # type: ignore[valid-type]
    totals = report.totals
    all_input = totals.input + totals.cache_read

    # Value field width: widest of all numeric values we'll show, so the grid
    # right-aligns cleanly without manual ANSI padding.
    widest = max(
        len(fmt_num(totals.total)),
        len(fmt_num(totals.cache_read)),
        len(fmt_num(totals.input)),
    )

    t: Table = Table.grid(padding=(0, 2))
    t.add_column(no_wrap=True)  # label
    t.add_column(no_wrap=True, width=widest)  # value, fixed width → clean right-align
    t.add_column(no_wrap=True)  # annotation

    def row(label, value, annot="", label_style="", value_style="", end=False):
        t.add_row(
            Text(label, style=label_style or None),
            Text(value, style=value_style or None, justify="right"),
            Text(annot, style="dim"),
            end_section=end,
        )

    row("Input (non-cache)", fmt_num(totals.input), f"{pct(totals.input, all_input):.0f}% of input")
    row("Cache read", fmt_num(totals.cache_read), "cached", value_style="bold green")
    if totals.cache_write:
        row("Cache write", fmt_num(totals.cache_write), "cached", value_style="green")
    row("Output", fmt_num(totals.output))
    row("Reasoning", fmt_num(totals.reasoning), end=True)
    row("Total", fmt_num(totals.total), "", label_style="bold", value_style="bold white")
    if report.cost_tracked:
        row("Cost", money(totals.cost), "", label_style="bold")
    else:
        row(
            "Cost",
            "—",
            "not tracked by these providers",
            label_style="dim",
            value_style="dim",
        )
    return t


def _build_cache(report: Report, fmt_num) -> Table:
    totals = report.totals
    all_input = totals.input + totals.cache_read
    hit = pct(totals.cache_read, all_input)

    t: Table = Table.grid(padding=(0, 1))
    t.add_column(no_wrap=True, min_width=32)
    t.add_column(justify="right", no_wrap=True)
    t.add_column(no_wrap=True)
    t.add_row(
        _bar(hit / 100),
        Text(f"{hit:.1f}%", style="bold green", justify="right"),
        Text("cache hit", style="dim"),
    )
    return t


def _build_providers(report: Report, colors: dict[str, str], fmt_num, has_cost: bool) -> Table:
    cols = [
        ("Provider", "left", ""),
        ("Turns", "right", ""),
        ("Input", "right", ""),
        ("Cache Read", "right", "green"),
        ("Cache Write", "right", "green"),
        ("Output", "right", ""),
        ("Reasoning", "right", ""),
        ("Total", "right", "bold"),
    ]
    if has_cost:
        cols.append(("Cost", "right", ""))
    t = _make_table(cols, box_style=box.HEAVY_HEAD, header_style="bold blue")
    for name, bucket in sorted(
        report.by_provider.items(), key=lambda kv: kv[1].total, reverse=True
    ):
        color = colors[name]
        cells = [
            Text(name, style=f"bold {color}"),
            Text(fmt_num(bucket.turns), justify="right"),
            Text(fmt_num(bucket.input), justify="right"),
            Text(fmt_num(bucket.cache_read), justify="right", style="green"),
            Text(fmt_num(bucket.cache_write), justify="right", style="green"),
            Text(fmt_num(bucket.output), justify="right"),
            Text(fmt_num(bucket.reasoning), justify="right"),
            Text(fmt_num(bucket.total), justify="right", style="bold"),
        ]
        if has_cost:
            # A zero-cost provider means cost was not reported for it (not that it
            # was free), so show "—" rather than a misleading "$0.00".
            cells.append(
                Text(money(bucket.cost) if bucket.cost > 0 else "—", justify="right", style="dim")
            )
        t.add_row(*cells)
    return t


def _build_models(
    report: Report, colors: dict[str, str], fmt_num, has_cost: bool
) -> list[RenderableType]:
    """One sub-table per provider, sorted by total desc."""
    groups: list[RenderableType] = []
    for name in sorted(
        report.by_provider.keys(), key=lambda n: report.by_provider[n].total, reverse=True
    ):
        color = colors[name]
        models = [(k, v) for k, v in report.by_model.items() if k[0] == name]
        models.sort(key=lambda kv: kv[1].total, reverse=True)

        cols = [
            ("Model", "left", ""),
            ("Variant", "left", ""),
            ("Turns", "right", ""),
            ("Input", "right", ""),
            ("Cache Read", "right", "green"),
            ("Cache Write", "right", "green"),
            ("Output", "right", ""),
            ("Reasoning", "right", ""),
            ("Total", "right", ""),
        ]
        if has_cost:
            cols.append(("Cost", "right", ""))
        tbl = _make_table(cols, box_style=box.ROUNDED, header_style=f"bold {color}")
        for (_prov, model, variant), bucket in models:
            cells = [
                Text(model, style=f"bold {color}"),
                Text(variant, style="dim italic") if variant else Text(""),
                Text(fmt_num(bucket.turns), justify="right"),
                Text(fmt_num(bucket.input), justify="right"),
                Text(fmt_num(bucket.cache_read), justify="right", style="green"),
                Text(fmt_num(bucket.cache_write), justify="right", style="green"),
                Text(fmt_num(bucket.output), justify="right"),
                Text(fmt_num(bucket.reasoning), justify="right"),
                Text(fmt_num(bucket.total), justify="right", style="bold white"),
            ]
            if has_cost:
                cells.append(
                    Text(
                        money(bucket.cost) if bucket.cost > 0 else "—", justify="right", style="dim"
                    )
                )
            tbl.add_row(*cells)

        header = Text(f"  {name}  ", style=f"bold white on {color}")
        groups.append(Group(header, tbl))
    return groups


def render_rich(report: Report, *, fmt_num, console: Console) -> None:
    """Render the full human report to ``console``."""
    colors = _provider_color_map(report.by_provider)
    has_cost = report.cost_tracked

    sections: list[RenderableType] = [
        _build_header(report, fmt_num),
        Text(""),
        _section("Token Totals", "cyan"),
        _build_totals(report, fmt_num),
        Text(""),
        _section("Cache", "green"),
        _build_cache(report, fmt_num),
        Text(""),
        _section("By Provider", "blue"),
        _build_providers(report, colors, fmt_num, has_cost),
        Text(""),
        _section("By Model", "magenta"),
    ]
    sections.extend(_build_models(report, colors, fmt_num, has_cost))
    console.print(Group(*sections))


# ── JSON rendering ────────────────────────────────────────────────────────────


def _pack(bucket: Bucket) -> dict[str, int | float]:
    return {
        "turns": bucket.turns,
        "input": bucket.input,
        "output": bucket.output,
        "reasoning": bucket.reasoning,
        "cache_read": bucket.cache_read,
        "cache_write": bucket.cache_write,
        "total": bucket.total,
        "cost": round(bucket.cost, 6),
    }


def render_json(report: Report) -> str:
    payload: dict[str, object] = {
        "totals": {**_pack(report.totals), "cost_tracked": report.cost_tracked},
        "providers": {
            name: _pack(b)
            for name, b in sorted(
                report.by_provider.items(), key=lambda kv: kv[1].total, reverse=True
            )
        },
        "models": [
            {"provider": k[0], "model": k[1], "variant": k[2], **_pack(v)}
            for k, v in sorted(report.by_model.items(), key=lambda kv: kv[1].total, reverse=True)
        ],
    }
    if report.span:
        lo, hi = report.span
        payload["span"] = {"from": _iso(lo), "to": _iso(hi)}
    return json.dumps(payload, indent=2)


def _iso(dt: datetime) -> str:
    # UTC ISO-8601 with a trailing ``Z`` for readability in JSON consumers.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
