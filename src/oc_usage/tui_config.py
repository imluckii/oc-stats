"""Persistence for TUI preferences (tab, sort, number format, refresh)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

TABS = ("overview", "models", "daily", "hourly", "stats")
SORTS = ("cost", "tokens", "provider")
REFRESH_MIN_S = 30
REFRESH_MAX_S = 600
REFRESH_STEP_S = 30
REFRESH_DEFAULT_S = 120


@dataclass
class TuiPrefs:
    active_tab: str = "overview"
    sort_key: str = "cost"
    full_numbers: bool = False
    refresh_interval_s: int = REFRESH_DEFAULT_S

    def path(self) -> Path:
        base = os.environ.get("OC_STATS_TUI_CONFIG")
        if base:
            return Path(base)
        return Path.home() / ".config" / "oc-usage" / "tui.toml"


def load_prefs() -> TuiPrefs:
    """Load preferences, falling back to defaults on any problem."""
    prefs = TuiPrefs()
    path = prefs.path()
    if not path.is_file():
        return prefs
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return prefs
    tab = data.get("active_tab")
    if tab in TABS:
        prefs.active_tab = tab
    sort = data.get("sort_key")
    if sort in SORTS:
        prefs.sort_key = sort
    if isinstance(data.get("full_numbers"), bool):
        prefs.full_numbers = data["full_numbers"]
    interval = data.get("refresh_interval_s")
    if isinstance(interval, int) and REFRESH_MIN_S <= interval <= REFRESH_MAX_S:
        prefs.refresh_interval_s = interval
    return prefs


def save_prefs(prefs: TuiPrefs) -> None:
    """Write preferences as a small TOML file, best-effort."""
    path = prefs.path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{key} = {value!r}" for key, value in asdict(prefs).items()]
        path.write_text("\n".join(lines) + "\n")
    except OSError:
        pass  # preferences are cosmetic; never crash the TUI over them


def clamp_interval(seconds: int) -> int:
    return max(REFRESH_MIN_S, min(REFRESH_MAX_S, seconds))
