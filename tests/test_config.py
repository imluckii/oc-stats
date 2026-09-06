"""Tests for user settings (hidden providers in the config file)."""

from __future__ import annotations

import pytest

from oc_usage.config import ConfigError, Settings, filter_hidden, load_settings
from oc_usage.models import UsageRow


def row(provider: str) -> UsageRow:
    return UsageRow(provider, "m", "", 1, 0, 0, 1, 0, 0.0, 0)


def use_config(tmp_path, monkeypatch, text: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(text)
    monkeypatch.setenv("OC_STATS_CONFIG", str(path))


def test_missing_file_yields_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_STATS_CONFIG", str(tmp_path / "none.toml"))
    assert load_settings() == Settings()


def test_names_are_trimmed_and_lowercased(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'hidden_providers = ["  Zai ", "OpenAI", ""]\n')
    assert load_settings().hidden_providers == frozenset({"zai", "openai"})


def test_file_without_the_key_yields_defaults(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'sort = "nothing relevant"\n')
    assert load_settings() == Settings()


def test_invalid_toml_raises(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, "hidden_providers = [")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings()


@pytest.mark.parametrize("value", ['"zai"', "[1, 2]", "[true]"])
def test_wrong_type_raises(value, tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, f"hidden_providers = {value}")
    with pytest.raises(ConfigError, match="list of provider names"):
        load_settings()


def test_filter_drops_rows_and_reports_display_names():
    rows = [row("zai"), row("OpenAI"), row("anthropic")]
    kept, dropped = filter_hidden(rows, Settings(frozenset({"zai", "openai"})))
    assert [r.provider for r in kept] == ["anthropic"]
    # Display names keep the data's casing, sorted for a stable note.
    assert dropped == ["OpenAI", "zai"]


def test_filter_without_hidden_settings_keeps_everything():
    rows = [row("zai"), row("anthropic")]
    kept, dropped = filter_hidden(rows, Settings())
    assert kept == rows
    assert dropped == []


def test_filter_drops_unknown_names_that_never_appeared():
    # A config listing providers absent from the data hides nothing.
    kept, dropped = filter_hidden([row("zai")], Settings(frozenset({"openai"})))
    assert [r.provider for r in kept] == ["zai"]
    assert dropped == []
