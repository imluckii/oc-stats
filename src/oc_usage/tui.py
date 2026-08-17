"""Interactive TUI for oc-stats (optional extra: ``oc-stats[tui]``).

Run with ``oc-stats tui``. The app loads usage once on startup; ``R``
toggles auto-refresh which re-reads the databases on an interval.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane

from oc_usage.models import Bucket, Report, UsageRow
from oc_usage.render import fmt_compact, fmt_full, money
from oc_usage.stats import bar, busiest, cache_hit_rate, provider_rows, unpriced_ratio
from oc_usage.timegroups import DateRange, daily_buckets, filter_range, hourly_buckets
from oc_usage.tui_config import (
    REFRESH_STEP_S,
    TuiPrefs,
    clamp_interval,
    load_prefs,
    save_prefs,
)

RowsLoader = Callable[[], tuple[list[UsageRow], str]]

SortKey = Literal["cost", "tokens", "provider"]


def fmt_pct(fraction: float | None) -> str:
    return "n/a" if fraction is None else f"{fraction * 100:.1f}%"


def fmt_hour(hour: int) -> str:
    return f"{hour:02d}:00"


class TotalsBlock(Static):
    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh)
        self.border_title = "totals"

    def render(self) -> RenderableType:
        app = self.app
        assert isinstance(app, OcStatsApp)
        return _totals_renderable(app.oc_stats_totals(), app.oc_stats_source(), app)


def _totals_renderable(totals: Bucket, source: str, app: OcStatsApp) -> Table:
    grid = Table.grid(padding=(0, 2), expand=False)
    grid.add_column(justify="left", no_wrap=True)
    grid.add_column(justify="left", no_wrap=True)

    def label(text: str) -> Text:
        return Text(text, style="dim", no_wrap=True)

    rate = cache_hit_rate(totals)
    rate_style = "green" if rate and rate >= 0.8 else ("yellow" if rate else "dim")
    span_days = app.oc_stats_span_days()
    per_day = totals.total / span_days if span_days else totals.total

    grid.add_row(
        label("tokens"),
        Text.assemble(
            (fmt_full(totals.total), "bold"),
            "  ",
            (f"in {fmt_full(totals.input)}", "cyan"),
            "  ",
            (f"cache r {fmt_full(totals.cache_read)}", "cyan"),
            "  ",
            (f"cache w {fmt_full(totals.cache_write)}", "cyan"),
            "  ",
            (f"out {fmt_full(totals.output)}", "magenta"),
            "  ",
            (f"reasoning {fmt_full(totals.reasoning)}", "magenta"),
        ),
    )
    grid.add_row(
        label("cost"),
        Text.assemble(
            (money(totals.estimated_cost), "bold yellow"),
            "   ",
            ("cache hit", "dim"),
            (f" {fmt_pct(rate)}", rate_style),
            "   ",
            ("turns", "dim"),
            f" {fmt_full(totals.turns)}",
            "   ",
            ("avg/day", "dim"),
            f" {fmt_compact(per_day)}",
        ),
    )
    grid.add_row(label("source"), Text(source, style="dim"))
    return grid


def _totals_text(totals: Bucket, source: str) -> str:
    rate = cache_hit_rate(totals)
    return (
        f"tokens  {fmt_full(totals.total)}  "
        f"(in {fmt_full(totals.input)} · cache r {fmt_full(totals.cache_read)} · "
        f"cache w {fmt_full(totals.cache_write)} · out {fmt_full(totals.output)} · "
        f"reasoning {fmt_full(totals.reasoning)})\n"
        f"cost    {money(totals.estimated_cost)}   "
        f"cache hit  {fmt_pct(rate)}   turns  {fmt_full(totals.turns)}\n"
        f"source  {source}"
    )


class StatsBlock(Static):
    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh)

    def render(self) -> RenderableType:
        app = self.app
        assert isinstance(app, OcStatsApp)
        return _stats_renderable(app.report, app.oc_stats_visible_rows(), app.oc_stats_tz_name())


def report_tz():
    from oc_usage.timegroups import get_tz

    return get_tz()


def _stats_renderable(report: Report | None, rows: list[UsageRow], tz_name: str) -> RenderableType:
    if report is None:
        return Text("no data", style="dim")
    day, hour = busiest(rows, tz=report_tz())
    grid = Table.grid(padding=(0, 3), expand=False)
    grid.add_column(justify="left", no_wrap=True)
    grid.add_column(justify="left", no_wrap=True)

    def kv(label: str, value: Text | str) -> None:
        grid.add_row(Text(label, style="dim"), value)

    avg = report.totals.total / report.totals.turns if report.totals.turns else None
    kv("cache hit rate", Text(fmt_pct(cache_hit_rate(report.totals)), style="cyan"))
    kv(
        "avg tokens/turn",
        Text(fmt_full(avg) if avg is not None else "n/a", style="cyan"),
    )
    unpriced = unpriced_ratio(report)
    kv(
        "unpriced turns",
        Text(
            fmt_pct(unpriced),
            style="red" if unpriced and unpriced > 0.25 else ("yellow" if unpriced else "dim"),
        ),
    )
    kv(
        "busiest day",
        Text(
            f"{day[0] if day else 'n/a'}  ({fmt_full(day[1]) if day else '-'})",
            style="cyan",
        ),
    )
    kv(
        "busiest hour",
        Text(
            f"{fmt_hour(hour[0]) if hour else 'n/a'}  ({fmt_full(hour[1]) if hour else '-'})",
            style="cyan",
        ),
    )
    kv(
        "timezone",
        Text(f"{tz_name} ", style="dim") + Text("(OC_STATS_TZ to override)", style="dim italic"),
    )

    grid.add_row("", "")
    grid.add_row(Text("providers by cost", style="bold"), "")
    for name, bucket, token_share, cost_share in provider_rows(report):
        grid.add_row(
            Text(name, style="cyan"),
            Text.assemble(
                (bar(cost_share, cells=14), "yellow"),
                f" {fmt_pct(cost_share):>6} cost",
                (" · ", "dim"),
                f"{fmt_pct(token_share):>6} tokens",
                (" · ", "dim"),
                (money(bucket.estimated_cost), "bold yellow"),
            ),
        )
    return grid


class CostChart(Static):
    """Last 14 days of estimated cost, one bar per day."""

    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh)
        self.border_title = "daily cost · last 14 days"

    def render(self) -> RenderableType:
        app = self.app
        assert isinstance(app, OcStatsApp)
        buckets = daily_buckets(app.oc_stats_visible_rows(), tz=report_tz())
        days = sorted(buckets)[-14:]
        if not days:
            return Text("no data", style="dim")
        peak = max(buckets[day].estimated_cost for day in days) or 1.0
        grid = Table.grid(padding=(0, 1), expand=False)
        grid.add_column(justify="right", no_wrap=True)
        grid.add_column(justify="left", no_wrap=True)
        for day in days:
            bucket = buckets[day]
            share = bucket.estimated_cost / peak
            grid.add_row(
                Text(day[5:], style="dim"),
                Text(bar(share, cells=24) + f"  {money(bucket.estimated_cost)}", style="yellow"),
            )
        return grid


class OcStatsApp(App):
    TITLE = "󰚩 oc-stats"
    SUB_TITLE = "opencode usage"
    DARK = True
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen { layout: vertical; }

    Header {
        dock: top;
        background: $panel;
    }

    #status {
        dock: bottom; height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }

    Footer { dock: bottom; }

    TabbedContent { height: 1fr; }

    TotalsBlock {
        height: auto;
        background: $panel;
        border: round $primary;
        border-title-align: left;
        margin: 0 1;
        padding: 0 1;
    }

    StatsBlock { padding: 0 1; }

    DataTable { height: 1fr; }

    #chart-block {
        height: auto;
        margin: 0 1 1 1;
        padding: 0 1;
        background: $surface;
        border: round $primary;
        border-title-align: left;
    }

    #hint {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "cycle_range", "Range"),
        Binding("r", "refresh", "Reload"),
        Binding("R", "toggle_auto", "Auto"),
        Binding("plus", "interval_up", "+30s"),
        Binding("minus", "interval_down", "-30s"),
        Binding("i", "toggle_numbers", "Numbers"),
        Binding("e", "export", "Export"),
        Binding("c", "sort", "Cost"),
        Binding("t", "sort_tokens", "Tokens"),
        Binding("p", "sort_provider", "Provider"),
        Binding("left,right,tab,shift+tab", "cycle_tab", "Tabs"),
    ]

    def __init__(self, rows_loader: RowsLoader, prefs: TuiPrefs | None = None) -> None:
        super().__init__()
        self._loader = rows_loader
        self.prefs = prefs or load_prefs()
        self.report: Report | None = None
        self.rows: list[UsageRow] = []
        self.range = DateRange.ALL
        self.sort: SortKey = "cost"  # type: ignore[assignment]
        if self.prefs.sort_key in ("cost", "tokens", "provider"):
            self.sort = self.prefs.sort_key  # type: ignore[assignment]
        self.auto = False
        self.last_refresh: datetime | None = None
        self._refresh_timer = None

    # ------------------------------------------------------------------
    # helpers used by the plain Static blocks

    def oc_stats_totals(self) -> Bucket:
        if self.report is None:
            return Bucket()
        return self._visible_report().totals

    def oc_stats_source(self) -> str:
        if self.report is None:
            return "loading"
        return f"{self.report.source} · range {self.range.label} · sort {self.sort}"

    def oc_stats_visible_rows(self) -> list[UsageRow]:
        if self.report is None:
            return []
        return filter_range(self.rows, self.range, tz=report_tz())

    def oc_stats_tz_name(self) -> str:
        return report_tz().tzname(None) or "IST"

    def oc_stats_span_days(self) -> int:
        """Days between first and last visible row, at least 1."""
        rows = self.oc_stats_visible_rows()
        if not rows:
            return 1
        lo = min(row.time_created for row in rows)
        hi = max(row.time_created for row in rows)
        return max(1, round((hi - lo) / 86_400_000) + 1)

    def _visible_report(self) -> Report:
        from oc_usage.models import aggregate

        assert self.report is not None
        return aggregate(self.oc_stats_visible_rows(), source=self.report.source)

    # ------------------------------------------------------------------

    def _col(
        self,
        table: DataTable,
        label: str,
        justify: Literal["left", "center", "right"],
        style: str,
        width: int | None = None,
    ) -> None:
        table.add_column(Text(label, justify=justify, style=style), width=width)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            with TabPane("Overview", id="overview"):
                yield TotalsBlock(id="overview-totals")
                chart = CostChart(id="chart-block")
                yield chart
                table = DataTable(id="overview-models")
                yield table
            with TabPane("Models", id="models"):
                yield DataTable(id="models-table")
            with TabPane("Daily", id="daily"):
                yield DataTable(id="daily-table")
            with TabPane("Hourly", id="hourly"):
                yield DataTable(id="hourly-table")
            with TabPane("Stats", id="stats"), VerticalScroll():
                yield StatsBlock(id="stats-block")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._build_tables()
        self._apply_prefs()
        self.load_data(initial=True)

    def _apply_prefs(self) -> None:
        try:
            tabs = self.query_one(TabbedContent)
            tabs.active = self.prefs.active_tab
        except Exception:
            pass

    # ------------------------------------------------------------------
    # data

    def load_data(self, initial: bool = False) -> None:
        try:
            rows, source = self._loader()
            from oc_usage.models import aggregate

            self.rows = rows
            self.report = aggregate(rows, source=source)
            self.last_refresh = datetime.now()
            self.populate()
            self._update_prefs_from_state()
        except Exception as exc:  # noqa: BLE001 - surface any load failure in-app
            self._set_status(f"load failed: {exc}")

    # ------------------------------------------------------------------
    # table construction

    def _num(self, value: int | float) -> str:
        return fmt_full(value) if self.prefs.full_numbers else fmt_compact(value)

    def _build_tables(self) -> None:
        overview = self.query_one("#overview-models", DataTable)
        overview.clear(columns=True)
        self._col(overview, "Model", "left", "bold")
        self._col(overview, "Provider", "left", "dim")
        self._col(overview, "Cost", "right", "yellow")
        self._col(overview, "Share of cost", "left", "cyan")
        self._col(overview, "Tokens", "right", "cyan")

        models = self.query_one("#models-table", DataTable)
        models.clear(columns=True)
        self._col(models, "Provider", "left", "dim")
        self._col(models, "Model", "left", "bold")
        self._col(models, "Variant", "left", "dim")
        self._col(models, "Input", "right", "")
        self._col(models, "CacheR", "right", "")
        self._col(models, "CacheW", "right", "")
        self._col(models, "Output", "right", "")
        self._col(models, "Reasoning", "right", "")
        self._col(models, "Total", "right", "bold")
        self._col(models, "Cost", "right", "yellow")
        self._col(models, "Priced", "center", "")

        daily = self.query_one("#daily-table", DataTable)
        daily.clear(columns=True)
        self._col(daily, "Date", "left", "bold")
        self._col(daily, "Input", "right", "")
        self._col(daily, "CacheR", "right", "")
        self._col(daily, "CacheW", "right", "")
        self._col(daily, "Output", "right", "")
        self._col(daily, "Reasoning", "right", "")
        self._col(daily, "Total", "right", "bold")
        self._col(daily, "Cost", "right", "yellow")
        self._col(daily, "Turns", "right", "")

        hourly = self.query_one("#hourly-table", DataTable)
        hourly.clear(columns=True)
        self._col(hourly, "Hour", "left", "bold")
        self._col(hourly, "Input", "right", "")
        self._col(hourly, "CacheR", "right", "")
        self._col(hourly, "CacheW", "right", "")
        self._col(hourly, "Output", "right", "")
        self._col(hourly, "Reasoning", "right", "")
        self._col(hourly, "Total", "right", "bold")
        self._col(hourly, "Cost", "right", "yellow")
        self._col(hourly, "Turns", "right", "")

    # ------------------------------------------------------------------
    # population

    def populate(self) -> None:
        if self.report is None:
            return
        self._populate_overview()
        self._populate_models()
        self._populate_daily()
        self._populate_hourly()
        self._refresh_stats()
        self._set_status(self._status_line())

    def _visible_by_model(self):
        return self._visible_report().by_model

    def _populate_overview(self) -> None:
        block = self.query_one("#overview-totals", TotalsBlock)
        block.refresh()
        table = self.query_one("#overview-models", DataTable)
        table.clear()
        entries = sorted(
            self._visible_by_model().items(),
            key=lambda kv: kv[1].estimated_cost,
            reverse=True,
        )[:5]
        total_cost = self._visible_report().totals.estimated_cost
        for (provider, model, _variant), bucket in entries:
            share = bucket.estimated_cost / total_cost if total_cost else 0.0
            table.add_row(
                Text(model, style="bold"),
                Text(provider, style="dim"),
                Text(money(bucket.estimated_cost), style="yellow"),
                Text(bar(share, cells=10) + f" {fmt_pct(share)}", style="cyan"),
                Text(self._num(bucket.total), style="cyan"),
            )

    def _populate_models(self) -> None:
        table = self.query_one("#models-table", DataTable)
        table.clear()
        by_model = self._visible_by_model()
        if self.sort == "cost":
            entries = sorted(by_model.items(), key=lambda kv: kv[1].estimated_cost, reverse=True)
        elif self.sort == "tokens":
            entries = sorted(by_model.items(), key=lambda kv: kv[1].total, reverse=True)
        else:
            entries = sorted(by_model.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        for (provider, model, variant), bucket in entries:
            table.add_row(
                Text(provider, style="dim"),
                Text(model, style="bold"),
                Text(variant, style="dim"),
                self._num(bucket.input),
                self._num(bucket.cache_read),
                self._num(bucket.cache_write),
                self._num(bucket.output),
                self._num(bucket.reasoning),
                Text(self._num(bucket.total), style="bold"),
                Text(money(bucket.estimated_cost), style="yellow"),
                Text(
                    "yes" if bucket.estimate_complete else "partial",
                    style="green" if bucket.estimate_complete else "yellow",
                ),
            )

    def _populate_daily(self) -> None:
        table = self.query_one("#daily-table", DataTable)
        table.clear()
        buckets = daily_buckets(self.oc_stats_visible_rows(), tz=report_tz())
        for day in sorted(buckets, reverse=True):
            bucket = buckets[day]
            table.add_row(
                Text(day, style="bold"),
                self._num(bucket.input),
                self._num(bucket.cache_read),
                self._num(bucket.cache_write),
                self._num(bucket.output),
                self._num(bucket.reasoning),
                Text(self._num(bucket.total), style="bold"),
                Text(money(bucket.estimated_cost), style="yellow"),
                str(bucket.turns),
            )

    def _populate_hourly(self) -> None:
        table = self.query_one("#hourly-table", DataTable)
        table.clear()
        buckets = hourly_buckets(self.oc_stats_visible_rows(), tz=report_tz())
        for hour in range(24):
            bucket = buckets.get(hour)
            if bucket is None:
                continue
            table.add_row(
                Text(fmt_hour(hour), style="bold"),
                self._num(bucket.input),
                self._num(bucket.cache_read),
                self._num(bucket.cache_write),
                self._num(bucket.output),
                self._num(bucket.reasoning),
                Text(self._num(bucket.total), style="bold"),
                Text(money(bucket.estimated_cost), style="yellow"),
                str(bucket.turns),
            )

    def _refresh_stats(self) -> None:
        block = self.query_one("#stats-block", StatsBlock)
        block.refresh()

    # ------------------------------------------------------------------
    # status / prefs

    def _status_line(self) -> Text:
        auto = (
            Text(f"auto {self.prefs.refresh_interval_s}s", style="green")
            if self.auto
            else Text("one-shot", style="dim")
        )
        last = self.last_refresh.strftime("%H:%M:%S") if self.last_refresh else "never"
        return Text.assemble(
            (self.range.label.upper(), "bold cyan"),
            ("  ·  ", "dim"),
            ("numbers ", "dim"),
            ("full" if self.prefs.full_numbers else "compact", "cyan"),
            ("  ·  ", "dim"),
            ("sort ", "dim"),
            (self.sort, "cyan"),
            ("  ·  ", "dim"),
            auto,
            ("  ·  ", "dim"),
            ("last refresh ", "dim"),
            (last, "cyan"),
        )

    def _set_status(self, text: str | Text) -> None:
        self.query_one("#status", Static).update(text)

    def _update_prefs_from_state(self) -> None:
        try:
            tabs = self.query_one(TabbedContent)
            self.prefs.active_tab = tabs.active or "overview"
        except Exception:
            pass

    # ------------------------------------------------------------------
    # actions (key bindings)

    def action_cycle_tab(self, direction: int = 1) -> None:
        tabs = self.query_one(TabbedContent)
        order = ["overview", "models", "daily", "hourly", "stats"]
        current = tabs.active or "overview"
        if current in order:
            idx = (order.index(current) + direction) % len(order)
            tabs.active = order[idx]
        self._update_prefs_from_state()

    def action_cycle_range(self) -> None:
        self.range = self.range.next
        self.populate()

    def action_refresh(self) -> None:
        self.load_data()

    def action_toggle_auto(self) -> None:
        self.auto = not self.auto
        if self.auto:
            self._start_refresh_timer()
        else:
            self._stop_refresh_timer()
        self._set_status(self._status_line())

    def action_interval_up(self) -> None:
        self._bump_interval(+REFRESH_STEP_S)

    def action_interval_down(self) -> None:
        self._bump_interval(-REFRESH_STEP_S)

    def _bump_interval(self, delta: int) -> None:
        self.prefs.refresh_interval_s = clamp_interval(self.prefs.refresh_interval_s + delta)
        if self.auto:
            self._stop_refresh_timer()
            self._start_refresh_timer()
        self._set_status(self._status_line())

    def action_toggle_numbers(self) -> None:
        self.prefs.full_numbers = not self.prefs.full_numbers
        self.populate()

    def action_sort(self, key: str = "cost") -> None:
        self.sort = key  # type: ignore[assignment]
        self.prefs.sort_key = key
        self._populate_models()
        self._set_status(self._status_line())

    def action_sort_tokens(self) -> None:
        self.action_sort("tokens")

    def action_sort_provider(self) -> None:
        self.action_sort("provider")

    def action_export(self) -> None:
        if self.report is None:
            return
        visible = self._visible_report()
        payload = {
            "range": self.range.value,
            "sort": self.sort,
            "totals": visible.totals.__dict__,
            "by_provider": {name: bucket.__dict__ for name, bucket in visible.by_provider.items()},
            "by_model": {
                "/".join(key): bucket.__dict__ for key, bucket in visible.by_model.items()
            },
            "daily": {
                day: bucket.__dict__
                for day, bucket in daily_buckets(
                    self.oc_stats_visible_rows(), tz=report_tz()
                ).items()
            },
            "hourly": {
                str(hour): bucket.__dict__
                for hour, bucket in hourly_buckets(
                    self.oc_stats_visible_rows(), tz=report_tz()
                ).items()
            },
        }
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = f"oc-stats-export-{stamp}.json"
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        self._set_status(f"exported {path}")

    # ------------------------------------------------------------------
    # auto-refresh

    def _start_refresh_timer(self) -> None:
        self._stop_refresh_timer()
        self._refresh_timer = self.set_interval(
            self.prefs.refresh_interval_s, self._auto_refresh_tick
        )

    def _stop_refresh_timer(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def _auto_refresh_tick(self) -> None:
        self.load_data()

    # ------------------------------------------------------------------
    # lifecycle

    def on_unmount(self) -> None:
        self._stop_refresh_timer()
        save_prefs(self.prefs)


MODEL_COLUMNS = [
    "Provider",
    "Model",
    "Variant",
    "Input",
    "CacheR",
    "CacheW",
    "Output",
    "Reasoning",
    "Total",
    "Cost",
    "Priced",
]

TIME_COLUMNS = [
    "When",
    "Input",
    "CacheR",
    "CacheW",
    "Output",
    "Reasoning",
    "Total",
    "Cost",
    "Turns",
]

DAILY_COLUMNS = ["Date", *TIME_COLUMNS[1:]]
HOURLY_COLUMNS = ["Hour", *TIME_COLUMNS[1:]]
