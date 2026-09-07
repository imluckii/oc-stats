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
    hidden_models: frozenset[str] = frozenset()


def settings_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "oc-usage" / "config.toml"


def _name_list(data: dict, key: str, path: Path) -> list[str]:
    names = data.get(key, [])
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ConfigError(f"{key} in {path} must be a list of strings")
    return [name.strip().lower() for name in names if name.strip()]


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

    return Settings(
        hidden_providers=frozenset(_name_list(data, "hidden_providers", path)),
        hidden_models=frozenset(_name_list(data, "hidden_models", path)),
    )


def filter_hidden(
    rows: Iterable[UsageRow], settings: Settings
) -> tuple[list[UsageRow], list[str], list[str]]:
    """Drop rows whose provider or model is hidden.

    Returns the kept rows plus the display names of what was actually
    dropped: providers, and models as ``provider/model`` pairs (both sorted),
    so callers can note what was excluded. Matching is case-insensitive on
    the full ids; the synthetic ``(unknown)``, ``(unattributed)``, and
    ``(internal usage)`` rows hide the same way.

    A ``hidden_models`` entry takes one of three forms, matched exactly:

    - ``model`` — that model under every provider and variant
    - ``provider/model`` — that model only under that provider, any variant
    - ``provider/model/variant`` — one variant only; a trailing slash
      (``provider/model/``) selects rows recorded with no variant at all
    """
    kept: list[UsageRow] = []
    providers: set[str] = set()
    models: set[tuple[str, str]] = set()
    for row in rows:
        provider = row.provider.lower()
        model = row.model.lower()
        variant = row.variant.lower()
        if provider in settings.hidden_providers:
            providers.add(row.provider)
        elif (
            f"{provider}/{model}/{variant}" in settings.hidden_models
            or f"{provider}/{model}" in settings.hidden_models
            or model in settings.hidden_models
        ):
            models.add((row.provider, row.model))
        else:
            kept.append(row)
    return kept, sorted(providers), [f"{p}/{m}" for p, m in sorted(models)]
