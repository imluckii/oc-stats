"""Headless pilot tests for the TUI app (requires textual installed)."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("textual")

from oc_usage.models import UsageRow  # noqa: E402
from oc_usage.tui import OcStatsApp  # noqa: E402
from oc_usage.tui_config import TuiPrefs  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
BASE = 1_750_000_000_000  # 2025-06-15 18:26:40 UTC


def _row(ts: int, provider: str = "openai", model: str = "gpt-5.6-sol", output: int = 10):
    return UsageRow(
        provider=provider,
        model=model,
        variant="",
        input=100,
        cache_read=50,
        cache_write=0,
        output=output,
        reasoning=0,
        cost=0.01,
        time_created=ts,
    )


def _loader(rows):
    calls = {"n": 0}

    def load():
        calls["n"] += 1
        return list(rows), "test data"

    load.calls = calls
    return load


@pytest.fixture
def rows():
    day = 86_400_000
    return [
        _row(BASE, provider="openai", model="gpt-5.6-sol", output=500),
        _row(BASE + day, provider="zai-coding-plan", model="glm-5.3", output=300),
        _row(BASE + 2 * day, provider="zai-coding-plan", model="glm-5.3", output=200),
    ]


async def test_app_boots_and_populates(rows):
    app = OcStatsApp(_loader(rows), prefs=TuiPrefs())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.report is not None
        assert app.report.totals.turns == 3
        models = app.query_one("#models-table")
        assert models.row_count >= 2
        daily = app.query_one("#daily-table")
        assert daily.row_count == 3
        hourly = app.query_one("#hourly-table")
        assert hourly.row_count >= 1


async def test_range_cycle_filters_daily(rows):
    app = OcStatsApp(_loader(rows), prefs=TuiPrefs())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#daily-table").row_count == 3  # all time
        app.action_cycle_range()  # -> today
        await pilot.pause()
        # no rows are "today" relative to the real clock (2025 timestamps)
        assert app.query_one("#daily-table").row_count == 0
        assert app.oc_stats_visible_rows() == []


async def test_sort_keys_reorder_models(rows):
    app = OcStatsApp(_loader(rows), prefs=TuiPrefs())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_sort("provider")
        await pilot.pause()
        first = app.query_one("#models-table").get_row_at(0)
        assert str(first[0].plain) == "openai"  # alphabetical first
        app.action_sort("tokens")
        await pilot.pause()
        first = app.query_one("#models-table").get_row_at(0)
        # glm-5.3 aggregates two rows (800 tokens) vs gpt-5.6-sol's single 650
        assert str(first[1].plain) == "glm-5.3"


async def test_refresh_calls_loader_again(rows):
    loader = _loader(rows)
    app = OcStatsApp(loader, prefs=TuiPrefs())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert loader.calls["n"] == 1
        app.action_refresh()
        await pilot.pause()
        assert loader.calls["n"] == 2


async def test_export_writes_json(rows, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = OcStatsApp(_loader(rows), prefs=TuiPrefs())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_export()
        await pilot.pause()
        exports = list(tmp_path.glob("oc-stats-export-*.json"))
        assert len(exports) == 1
        import json

        data = json.loads(exports[0].read_text())
        assert data["totals"]["turns"] == 3
        assert len(data["daily"]) == 3


async def test_toggle_numbers_changes_formatting(rows):
    app = OcStatsApp(_loader(rows), prefs=TuiPrefs())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_toggle_numbers()
        await pilot.pause()
        assert app.prefs.full_numbers is True


async def test_interval_bump_and_clamp(rows):
    app = OcStatsApp(_loader(rows), prefs=TuiPrefs())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_interval_down()  # 120 -> 90
        assert app.prefs.refresh_interval_s == 90
        for _ in range(10):
            app.action_interval_down()
        assert app.prefs.refresh_interval_s == 30  # clamped at min


async def test_auto_toggle_starts_and_stops_timer(rows):
    app = OcStatsApp(_loader(rows), prefs=TuiPrefs(refresh_interval_s=30))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._refresh_timer is None
        app.action_toggle_auto()
        assert app.auto is True
        assert app._refresh_timer is not None
        app.action_toggle_auto()
        assert app.auto is False
        assert app._refresh_timer is None


async def test_today_range_includes_recent_rows():
    now = int(datetime.now(IST).timestamp() * 1000)
    rows = [_row(now), _row(now - 7 * 86_400_000)]
    app = OcStatsApp(_loader(rows), prefs=TuiPrefs())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_cycle_range()  # today
        await pilot.pause()
        visible = app.oc_stats_visible_rows()
        assert len(visible) == 1
        assert visible[0].time_created == now
