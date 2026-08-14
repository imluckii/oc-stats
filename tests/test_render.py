"""Tests for JSON output and Rich rendering (incl. non-truncation guarantees)."""

from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from oc_usage.models import UsageRow, aggregate
from oc_usage.render import (
    NON_TTY_WIDTH,
    fmt_compact,
    fmt_full,
    make_console,
    money,
    render_json,
    render_rich,
)


def _rows():
    return [
        UsageRow("p1", "gpt-4o", "high", 10, 100, 0, 5, 2, 0.0, 1_000),
        UsageRow("p1", "gpt-4o", "high", 20, 200, 0, 6, 3, 1.5, 2_000),
        UsageRow("p2", "gpt-4o-mini", "low", 5, 0, 0, 1, 0, 0.0, 3_000),
    ]


# ── number formatting ─────────────────────────────────────────────────────────


def test_fmt_full_uses_thousands_separators():
    assert fmt_full(1234567) == "1,234,567"
    assert fmt_full(0) == "0"


def test_fmt_compact_rounds():
    assert fmt_compact(1500) == "1.5K"
    assert fmt_compact(2_500_000) == "2.50M"
    assert fmt_compact(999) == "999"


def test_money_handles_zero_small_and_large():
    assert money(0) == "$0.00"
    assert money(1.234) == "$1.23"
    assert money(1500) == "$1,500"


# ── JSON ──────────────────────────────────────────────────────────────────────


def test_json_totals_and_components():
    report = aggregate(_rows())
    data = json.loads(render_json(report))
    t = data["totals"]
    assert t["input"] == 35
    assert t["cache_read"] == 300
    assert t["output"] == 12
    assert t["reasoning"] == 5
    assert t["cache_write"] == 0
    assert t["total"] == 35 + 300 + 0 + 12 + 5
    assert t["turns"] == 3
    assert t["estimated_cost"] > 0
    assert t["estimate_complete"] is True


def test_json_estimate_unavailable_for_unknown_model():
    rows = [
        UsageRow("p", "m", "", 1, 0, 0, 1, 0, 0.0, 1),
        UsageRow("p", "m", "", 1, 0, 0, 1, 0, 0.0, 2),
    ]
    data = json.loads(render_json(aggregate(rows)))
    assert data["totals"]["estimate_complete"] is False
    assert data["totals"]["estimated_cost"] is None


def test_json_providers_and_models_sorted_by_total_desc():
    report = aggregate(_rows())
    data = json.loads(render_json(report))

    prov_totals = [v["total"] for v in data["providers"].values()]
    assert prov_totals == sorted(prov_totals, reverse=True)

    model_totals = [v["total"] for v in data["models"]]
    assert model_totals == sorted(model_totals, reverse=True)

    assert {"provider", "model", "variant", "input", "total", "estimated_cost"} <= set(
        data["models"][0]
    )


def test_json_models_include_variant():
    report = aggregate(_rows())
    data = json.loads(render_json(report))
    variants = {(m["provider"], m["model"], m["variant"]) for m in data["models"]}
    assert ("p1", "gpt-4o", "high") in variants
    assert ("p2", "gpt-4o-mini", "low") in variants


def test_json_span_iso8601():
    report = aggregate(_rows())
    data = json.loads(render_json(report))
    assert data["span"]["from"].endswith("Z")
    assert data["span"]["to"].endswith("Z")
    assert data["span"]["from"] < data["span"]["to"]


def test_json_span_absent_without_timestamps():
    report = aggregate([UsageRow("p", "m", "", 1, 0, 0, 1, 0, 0.0, 0)])
    data = json.loads(render_json(report))
    assert "span" not in data


# ── Rich rendering & non-truncation ───────────────────────────────────────────


def _capture(report, width, fmt_num=fmt_full, no_color=True) -> str:
    buf = StringIO()
    console = Console(
        file=buf, width=width, no_color=no_color, highlight=False, force_terminal=False
    )
    render_rich(report, fmt_num=fmt_num, console=console)
    return buf.getvalue()


def test_rich_report_contains_full_totals_non_tty_width():
    report = aggregate(_rows())
    out = _capture(report, NON_TTY_WIDTH)
    # The grand total (352) must appear in full, not truncated.
    assert fmt_full(352) in out
    assert "Token Totals" in out
    assert "By Provider" in out
    assert "By Model" in out


def test_rich_report_never_truncates_large_numbers_with_long_model_names():
    # Worst case for truncation: a very long model name plus a 9-digit total,
    # rendered in full-integer mode at the non-TTY width.
    big = 1_234_567_890
    long_name = "a-really-long-model-identifier-that-exceeds-typical-column-widths"
    report = aggregate([UsageRow("provider-one", long_name, "high", big, 0, 0, 0, 0, 0.0, 1_000)])
    out = _capture(report, NON_TTY_WIDTH)
    # The full number must survive intact (no ellipsis cutting the suffix).
    assert fmt_full(big) in out
    # No ellipsis truncation anywhere in the report.
    assert "…" not in out
    # The model name is fully rendered — it may line-WRAP (fold), but it is never
    # cut, so both its head and its tail must be present.
    assert "a-really-long-model-identifier" in out
    assert "exceeds-typical-column-" in out
    assert out.rstrip().endswith("widths") or "widths" in out


def test_rich_report_shows_model_hit_percentage_inside_cache_column():
    report = aggregate([UsageRow("p", "m", "", 10, 1_234, 5_678, 9, 2, 0.0, 1_000)])
    for width in (NON_TTY_WIDTH, 200):
        out = _capture(report, width)
        assert "Cache Read" not in out
        assert "Cache Write" not in out
        assert "Cache Hit" not in out
        assert fmt_full(1_234) in out
        assert "1,234 (99.2%)" in out


def test_rich_report_inlines_gray_variant_in_model_cell():
    out = _capture(aggregate(_rows()), NON_TTY_WIDTH)
    assert "Variant" not in out
    assert "gpt-4o · high" in out


def test_rich_report_degrades_safely_at_narrow_width():
    # At a narrow width the report must still render without raising and keep its
    # section structure; numeric columns are sized to their content so the small
    # fixture totals remain intact.
    report = aggregate(_rows())
    out = _capture(report, 80)
    assert "OpenCode Usage" in out
    assert "By Provider" in out
    # Grand total renders in full even at 80 columns.
    assert fmt_full(352) in out


def test_rich_report_shows_unavailable_for_unknown_model():
    report = aggregate([UsageRow("p", "m", "", 10, 0, 0, 1, 0, 0.0, 1)])
    out = _capture(report, NON_TTY_WIDTH)
    assert "pricing unavailable" in out.lower()


def test_rich_report_shows_estimated_cost():
    report = aggregate([UsageRow("p", "gpt-4o", "", 1_000_000, 0, 0, 0, 0, 2.5, 1)])
    out = _capture(report, NON_TTY_WIDTH)
    assert "$2.50" in out
    assert "Estimated cost" in out


def test_rich_ascii_mode_uses_ascii_chrome_and_escapes_labels():
    report = aggregate([UsageRow("提供者", "模型", "高", 10, 20, 0, 1, 0, 0.0, 1)])
    out = _capture(report, NON_TTY_WIDTH)
    # Keep the regular render assertion separate to prove Unicode remains the
    # default for a Unicode-capable captured stream.
    assert "提供者" in out

    buf = StringIO()
    console = Console(file=buf, width=NON_TTY_WIDTH, no_color=True, highlight=False)
    render_rich(report, fmt_num=fmt_full, console=console, ascii=True)
    ascii_out = buf.getvalue()
    assert "提供者" not in ascii_out
    assert "\\u63d0\\u4f9b\\u8005" in ascii_out
    assert "=" in ascii_out
    assert "━" not in ascii_out
    assert all(ord(char) < 128 for char in ascii_out)

    narrow = StringIO()
    render_rich(
        report,
        fmt_num=fmt_full,
        console=Console(file=narrow, width=40, no_color=True, highlight=False),
        ascii=True,
    )
    assert all(ord(char) < 128 for char in narrow.getvalue())


def test_rich_report_marks_unknown_provider_as_unpriced():
    report = aggregate(
        [
            UsageRow("p1", "gpt-4o", "", 10, 0, 0, 1, 0, 2.5, 1),
            UsageRow("p2", "unknown", "", 10, 0, 0, 1, 0, 0.0, 2),
        ]
    )
    out = _capture(report, NON_TTY_WIDTH)
    # The unpriced provider gets an em-dash cell, and the totals annotation
    # counts its turns as unpriced. (Tiny priced estimates like "$0.000035"
    # legitimately start with "$0.00", so no substring ban here.)
    assert "—" in out
    assert "unpriced" in out


def test_make_console_non_tty_uses_fixed_width_and_disables_color(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "stdout", StringIO())  # not a tty
    console = make_console()
    assert console.width == NON_TTY_WIDTH
    assert console.no_color is True  # color auto-disabled when not a tty


def test_make_console_tty_keeps_color_on(monkeypatch):
    import sys

    class FakeTTY:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdout", FakeTTY())
    console = make_console()
    assert console.no_color is False  # color stays on for a real TTY
