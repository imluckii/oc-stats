"""User settings for ``oc-stats`` (``~/.config/oc-usage/config.toml``).

The file is optional and changes what is *shown*, never what is loaded:
providers listed under ``hidden_providers`` are dropped after loading, so
every total, cost, and time grouping describes the remaining rows only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from oc_usage.models import UsageRow

if TYPE_CHECKING:
    from collections.abc import Iterable

CONFIG_ENV = "OC_STATS_CONFIG"


class ConfigError(Exception):
    """The user config file exists but cannot be used."""


@dataclass(frozen=True)
class Settings:
    hidden_providers: frozenset[str] = frozenset()


def settings_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "oc-usage" / "config.toml"


def load_settings() -> Settings:
    """Load user settings; a missing file yields the defaults.

    Raises :class:`ConfigError` for a file that exists but is unreadable, is
    not valid TOML, or carries a mistyped setting — a config that silently
    does nothing is worse than a loud failure.
    """
    path = settings_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return Settings()
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except ValueError as exc:  # tomllib.TOMLDecodeError
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    names = data.get("hidden_providers", [])
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ConfigError(f"hidden_providers in {path} must be a list of provider names")
    return Settings(frozenset(name.strip().lower() for name in names if name.strip()))


def filter_hidden(rows: Iterable[UsageRow], settings: Settings) -> tuple[list[UsageRow], list[str]]:
    """Drop rows whose provider is hidden.

    Returns the kept rows plus the hidden providers' display names as they
    appeared in the data (sorted), so callers can note what was excluded.
    Matching is case-insensitive on the full provider id; the synthetic
    ``(unknown)`` and ``(unattributed)`` rows hide the same way.
    """
    if not settings.hidden_providers:
        return list(rows), []
    kept: list[UsageRow] = []
    dropped: set[str] = set()
    for row in rows:
        if row.provider.lower() in settings.hidden_providers:
            dropped.add(row.provider)
        else:
            kept.append(row)
    return kept, sorted(dropped)
