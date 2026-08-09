"""Rendering for ``oc-stats``: a Rich human report and a JSON payload.

Width strategy
--------------
When stdout is a TTY, Rich auto-detects the terminal width. Text columns
(provider/model/variant) use ``overflow="fold"`` so long names wrap to the next
line instead of being cut; numeric columns are ``no_wrap`` and right-aligned so
token suffixes are never truncated.

When stdout is **not** a TTY (piped, captured, ``!oc-stats``, ``/stats``,
redirection) Rich would otherwise default to a narrow 80 columns and crop the
tables. We force a generous fixed width in that case so every value renders in
full, and we disable ANSI color so captured output is clean plain text.
"""

from __future__ import annotations

import codecs
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
NON_TTY_WIDTH = 180

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


def make_console() -> Console:
    """Build a Rich console tuned for TTY vs non-TTY output.

    Rich still receives the real stdout stream so callers can redirect or
    capture output normally. ``ascii`` is a rendering choice, not a Rich
    terminal choice, and is passed to :func:`render_rich` separately.
    """
    is_tty = _is_tty(sys.stdout)
    width = None if is_tty else NON_TTY_WIDTH
    return Console(no_color=not is_tty, highlight=False, width=width)


def _is_tty(stream) -> bool:
    """Return ``stream.isatty()`` without trusting every file-like object."""
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def needs_ascii(stream=None) -> bool:
    """Whether a stream's declared encoding cannot safely carry Unicode.

    UTF-8 (including Windows' ``cp65001`` alias) keeps the normal Unicode Rich
    report. For a legacy code page or an unknown encoding we proactively use
    the ASCII report; this avoids relying on a late ``UnicodeEncodeError``
    after Rich has already written part of a report. Streams such as
    ``StringIO`` with no encoding declaration are treated as Unicode-capable.
    The renderer also has a retry fallback for streams that lie about their
    encoding.
    """
    stream = sys.stdout if stream is None else stream
    try:
        encoding = getattr(stream, "encoding", None)
    except (AttributeError, OSError, ValueError):
        return True
    if not encoding:
        return False
    try:
        return codecs.lookup(encoding).name != "utf-8"
    except LookupError:
        return True


# ── small renderable helpers ──────────────────────────────────────────────────


def _bar(frac: float, width: int = 30, *, ascii: bool = False) -> Text:
    """A colored Unicode (or ASCII-safe) progress bar as a Text renderable."""
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * width)
    full_char, empty_char = ("=", "-") if ascii else ("━", "━")
    t = Text()
    t.append(full_char * filled, style="bold green")
    t.append(empty_char * (width - filled), style="grey42")
    return t


def _display(value: str, ascii: bool) -> str:
    """Keep data labels printable on an ASCII-only console."""
    if not ascii:
        return value
    return value.encode("ascii", errors="backslashreplace").decode("ascii")


def _provider_color_map(by_provider: dict[str, Bucket]) -> dict[str, str]:
    names = sorted(by_provider.keys())
    return {name: PROVIDER_COLORS[i % len(PROVIDER_COLORS)] for i, name in enumerate(names)}


def _section(title: str, style: str = "cyan", *, ascii: bool = False) -> Rule:
    return Rule(
        title=Text(f"  {title}  ", style=f"bold {style}"),
        style=style,
        align="left",
        characters="-" if ascii else "─",
    )


def _make_table(
    columns: list[tuple[str, str, str]],
    box_style: box.Box = box.SIMPLE_HEAVY,
    header_style: str = "bold cyan",
    *,
    ascii: bool = False,
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
            overflow="fold" if not is_numeric else ("crop" if ascii else "ellipsis"),
        )
    return t


# ── report sections ───────────────────────────────────────────────────────────


def _build_header(report: Report, fmt_num, *, ascii: bool = False) -> Panel:
    bits = [
        f"[dim]Source: {report.source}[/]",
        f"[bold]{fmt_num(report.totals.turns)}[/] turns",
        f"[bold]{len(report.by_provider)}[/] providers",
        f"[bold]{len(report.by_model)}[/] models",
    ]
    if report.span:
        lo, hi = report.span
        arrow = "->" if ascii else "→"
        bits.append(f"[dim]{lo:%Y-%m-%d} {arrow} {hi:%Y-%m-%d}[/]")
    separator = "  |  " if ascii else "  ·  "
    body = Text.from_markup(separator.join(bits))
    return Panel(
        Align.left(body, vertical="middle"),
        title=Text(
            "  *  OpenCode Usage - All Time  " if ascii else "  ◇  OpenCode Usage · All Time  ",
            style="bold white on blue",
        ),
        title_align="left",
        border_style="blue",
        box=box.ASCII if ascii else box.DOUBLE_EDGE,
        padding=(0, 1),
    )


def _build_totals(report: Report, fmt_num, *, ascii: bool = False) -> Table.grid:  # type: ignore[valid-type]
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
    overflow = "crop" if ascii else "ellipsis"
    t.add_column(no_wrap=True, overflow=overflow)  # label
    t.add_column(
        no_wrap=True, width=widest, overflow=overflow
    )  # value, fixed width → clean right-align
    t.add_column(no_wrap=True, overflow=overflow)  # annotation

    def row(label, value, annot="", label_style="", value_style="", end=False):
        t.add_row(
            Text(label, style=label_style or None),
            Text(value, style=value_style or None, justify="right"),
            Text(annot, style="dim"),
            end_section=end,
        )

    row("Input (non-cache)", fmt_num(totals.input), f"{pct(totals.input, all_input):.0f}% of input")
    row("Cache", fmt_num(totals.cache_read), "cached", value_style="bold green")
    row("Output", fmt_num(totals.output))
    row("Reasoning", fmt_num(totals.reasoning), end=True)
    row("Total", fmt_num(totals.total), "", label_style="bold", value_style="bold white")
    if totals.priced_turns:
        annotation = "current API list rates"
        if not totals.estimate_complete:
            annotation += f" · {fmt_full(totals.turns - totals.priced_turns)} unpriced turns"
        row("Estimated cost", money(totals.estimated_cost), annotation, label_style="bold")
    else:
        row(
            "Estimated cost",
            "-" if ascii else "—",
            "model pricing unavailable",
            label_style="dim",
            value_style="dim",
        )
    return t


def _build_cache(report: Report, fmt_num, *, ascii: bool = False) -> Table:
    totals = report.totals
    all_input = totals.input + totals.cache_read
    hit = pct(totals.cache_read, all_input)

    t: Table = Table.grid(padding=(0, 1))
    overflow = "crop" if ascii else "ellipsis"
    t.add_column(no_wrap=True, min_width=32, overflow=overflow)
    t.add_column(justify="right", no_wrap=True, overflow=overflow)
    t.add_column(no_wrap=True, overflow=overflow)
    t.add_row(
        _bar(hit / 100, ascii=ascii),
        Text(f"{hit:.1f}%", style="bold green", justify="right"),
        Text("cache hit", style="dim"),
    )
    return t


def _build_providers(
    report: Report, colors: dict[str, str], fmt_num, *, ascii: bool = False
) -> Table:
    cols = [
        ("Provider", "left", ""),
        ("Turns", "right", ""),
        ("Input", "right", ""),
        ("Cache", "right", "green"),
        ("Output", "right", ""),
        ("Reasoning", "right", ""),
        ("Total", "right", "bold"),
        ("Estimate", "right", ""),
    ]
    t = _make_table(
        cols,
        box_style=box.ASCII if ascii else box.HEAVY_HEAD,
        header_style="bold blue",
        ascii=ascii,
    )
    for name, bucket in sorted(
        report.by_provider.items(), key=lambda kv: kv[1].total, reverse=True
    ):
        color = colors[name]
        cells = [
            Text(_display(name, ascii), style=f"bold {color}"),
            Text(fmt_num(bucket.turns), justify="right"),
            Text(fmt_num(bucket.input), justify="right"),
            Text(fmt_num(bucket.cache_read), justify="right", style="green"),
            Text(fmt_num(bucket.output), justify="right"),
            Text(fmt_num(bucket.reasoning), justify="right"),
            Text(fmt_num(bucket.total), justify="right", style="bold"),
            Text(
                money(bucket.estimated_cost) if bucket.priced_turns else ("-" if ascii else "—"),
                justify="right",
                style="dim",
            ),
        ]
        t.add_row(*cells)
    return t


def _build_models(
    report: Report,
    colors: dict[str, str],
    fmt_num,
    *,
    ascii: bool = False,
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
            ("Cache", "right", "green"),
            ("Cache Hit", "right", "green"),
            ("Output", "right", ""),
            ("Reasoning", "right", ""),
            ("Total", "right", ""),
            ("Estimate", "right", ""),
        ]
        tbl = _make_table(
            cols,
            box_style=box.ASCII if ascii else box.ROUNDED,
            header_style=f"bold {color}",
            ascii=ascii,
        )
        for (_prov, model, variant), bucket in models:
            cells = [
                Text(_display(model, ascii), style=f"bold {color}"),
                Text(_display(variant, ascii), style="dim italic") if variant else Text(""),
                Text(fmt_num(bucket.turns), justify="right"),
                Text(fmt_num(bucket.input), justify="right"),
                Text(fmt_num(bucket.cache_read), justify="right", style="green"),
                Text(
                    f"{pct(bucket.cache_read, bucket.input + bucket.cache_read):.1f}%",
                    justify="right",
                    style="green",
                ),
                Text(fmt_num(bucket.output), justify="right"),
                Text(fmt_num(bucket.reasoning), justify="right"),
                Text(fmt_num(bucket.total), justify="right", style="bold white"),
                Text(
                    money(bucket.estimated_cost)
                    if bucket.priced_turns
                    else ("-" if ascii else "—"),
                    justify="right",
                    style="dim",
                ),
            ]
            tbl.add_row(*cells)

        header = Text(f"  {_display(name, ascii)}  ", style=f"bold white on {color}")
        groups.append(Group(header, tbl))
    return groups


def render_rich(report: Report, *, fmt_num, console: Console, ascii: bool = False) -> None:
    """Render the full human report to ``console``."""
    colors = _provider_color_map(report.by_provider)
    sections: list[RenderableType] = [
        _build_header(report, fmt_num, ascii=ascii),
        Text(""),
        _section("Token Totals", "cyan", ascii=ascii),
        _build_totals(report, fmt_num, ascii=ascii),
        Text(""),
        _section("Cache", "green", ascii=ascii),
        _build_cache(report, fmt_num, ascii=ascii),
        Text(""),
        _section("By Provider", "blue", ascii=ascii),
        _build_providers(report, colors, fmt_num, ascii=ascii),
        Text(""),
        _section("By Model", "magenta", ascii=ascii),
    ]
    sections.extend(_build_models(report, colors, fmt_num, ascii=ascii))
    console.print(Group(*sections))


# ── JSON rendering ────────────────────────────────────────────────────────────


def _pack(bucket: Bucket) -> dict[str, int | float | bool | None]:
    return {
        "turns": bucket.turns,
        "input": bucket.input,
        "output": bucket.output,
        "reasoning": bucket.reasoning,
        "cache_read": bucket.cache_read,
        "cache_write": bucket.cache_write,
        "total": bucket.total,
        "estimated_cost": round(bucket.estimated_cost, 6) if bucket.priced_turns else None,
        "estimate_complete": bucket.estimate_complete,
    }


def render_json(report: Report) -> str:
    payload: dict[str, object] = {
        "source": report.source,
        "totals": _pack(report.totals),
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
