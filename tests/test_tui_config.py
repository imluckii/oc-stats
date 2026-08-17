"""Tests for TUI preference persistence."""

from pathlib import Path

from oc_usage.tui_config import (
    REFRESH_DEFAULT_S,
    TuiPrefs,
    clamp_interval,
    load_prefs,
    save_prefs,
)


def test_round_trip(tmp_path: Path, monkeypatch):
    config = tmp_path / "tui.toml"
    monkeypatch.setenv("OC_STATS_TUI_CONFIG", str(config))
    prefs = TuiPrefs(
        active_tab="daily", sort_key="tokens", full_numbers=True, refresh_interval_s=300
    )
    save_prefs(prefs)
    assert config.is_file()
    loaded = load_prefs()
    assert loaded == prefs


def test_missing_file_gives_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OC_STATS_TUI_CONFIG", str(tmp_path / "nope.toml"))
    assert load_prefs() == TuiPrefs()


def test_malformed_toml_gives_defaults(tmp_path: Path, monkeypatch):
    config = tmp_path / "tui.toml"
    config.write_text("active_tab = [unclosed\n")
    monkeypatch.setenv("OC_STATS_TUI_CONFIG", str(config))
    assert load_prefs() == TuiPrefs()


def test_unknown_values_fall_back(tmp_path: Path, monkeypatch):
    config = tmp_path / "tui.toml"
    config.write_text(
        'active_tab = "leaderboard"\nsort_key = "vibes"\nfull_numbers = 3\nrefresh_interval_s = 5\n'
    )
    monkeypatch.setenv("OC_STATS_TUI_CONFIG", str(config))
    prefs = load_prefs()
    assert prefs.active_tab == "overview"
    assert prefs.sort_key == "cost"
    assert prefs.full_numbers is False
    assert prefs.refresh_interval_s == REFRESH_DEFAULT_S


def test_clamp_interval():
    assert clamp_interval(5) == 30
    assert clamp_interval(45) == 45
    assert clamp_interval(9_999) == 600


def test_save_prefs_swallows_unwritable_path(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OC_STATS_TUI_CONFIG", str(tmp_path))  # a directory
    save_prefs(TuiPrefs())  # must not raise
