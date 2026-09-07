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
@pytest.mark.parametrize("key", ["hidden_providers", "hidden_models"])
def test_wrong_type_raises(key, value, tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, f"{key} = {value}")
    with pytest.raises(ConfigError, match=f"{key} .* must be a list of strings"):
        load_settings()


def test_filter_drops_rows_and_reports_display_names():
    rows = [row("zai"), row("OpenAI"), row("anthropic")]
    kept, dropped, models = filter_hidden(rows, Settings(frozenset({"zai", "openai"})))
    assert [r.provider for r in kept] == ["anthropic"]
    # Display names keep the data's casing, sorted for a stable note.
    assert dropped == ["OpenAI", "zai"]
    assert models == []


def test_filter_without_hidden_settings_keeps_everything():
    rows = [row("zai"), row("anthropic")]
    kept, dropped, models = filter_hidden(rows, Settings())
    assert kept == rows
    assert dropped == []
    assert models == []


def test_filter_drops_unknown_names_that_never_appeared():
    # A config listing providers absent from the data hides nothing.
    kept, dropped, models = filter_hidden([row("zai")], Settings(frozenset({"openai"})))
    assert [r.provider for r in kept] == ["zai"]
    assert dropped == []
    assert models == []


def test_bare_model_name_hides_under_every_provider():
    rows = [
        UsageRow("openai", "gpt-4o", "", 1, 0, 0, 1, 0, 0.0, 0),
        UsageRow("zai-coding-plan", "gpt-4o", "", 1, 0, 0, 1, 0, 0.0, 0),
        UsageRow("openai", "gpt-4o-mini", "", 1, 0, 0, 1, 0, 0.0, 0),
    ]
    kept, dropped, models = filter_hidden(rows, Settings(hidden_models=frozenset({"gpt-4o"})))
    assert [r.model for r in kept] == ["gpt-4o-mini"]
    assert dropped == []
    assert models == ["openai/gpt-4o", "zai-coding-plan/gpt-4o"]


def test_provider_qualified_model_hides_only_that_provider():
    rows = [
        UsageRow("openai", "gpt-4o", "", 1, 0, 0, 1, 0, 0.0, 0),
        UsageRow("zai-coding-plan", "gpt-4o", "", 1, 0, 0, 1, 0, 0.0, 0),
    ]
    kept, dropped, models = filter_hidden(
        rows, Settings(hidden_models=frozenset({"openai/gpt-4o"}))
    )
    assert [(r.provider, r.model) for r in kept] == [("zai-coding-plan", "gpt-4o")]
    assert models == ["openai/gpt-4o"]


def test_variant_qualified_entry_hides_only_that_variant():
    rows = [
        UsageRow("zai-coding-plan", "glm-5.2", "max", 1, 0, 0, 1, 0, 0.0, 0),
        UsageRow("zai-coding-plan", "glm-5.2", "default", 1, 0, 0, 1, 0, 0.0, 0),
        UsageRow("zai-coding-plan", "glm-5.2", "", 1, 0, 0, 1, 0, 0.0, 0),
    ]
    kept, _, models = filter_hidden(
        rows, Settings(hidden_models=frozenset({"zai-coding-plan/glm-5.2/default"}))
    )
    assert [r.variant for r in kept] == ["max", ""]
    assert models == ["zai-coding-plan/glm-5.2"]


def test_trailing_slash_entry_hides_only_untagged_rows():
    rows = [
        UsageRow("zai-coding-plan", "glm-5.2", "max", 1, 0, 0, 1, 0, 0.0, 0),
        UsageRow("zai-coding-plan", "glm-5.2", "", 1, 0, 0, 1, 0, 0.0, 0),
    ]
    kept, _, models = filter_hidden(
        rows, Settings(hidden_models=frozenset({"zai-coding-plan/glm-5.2/"}))
    )
    assert [r.variant for r in kept] == ["max"]
    assert models == ["zai-coding-plan/glm-5.2"]


def test_provider_model_entry_hides_every_variant():
    rows = [
        UsageRow("zai-coding-plan", "glm-5.2", "max", 1, 0, 0, 1, 0, 0.0, 0),
        UsageRow("zai-coding-plan", "glm-5.2", "", 1, 0, 0, 1, 0, 0.0, 0),
    ]
    kept, _, models = filter_hidden(
        rows, Settings(hidden_models=frozenset({"zai-coding-plan/glm-5.2"}))
    )
    assert kept == []
    assert models == ["zai-coding-plan/glm-5.2"]


def test_provider_hidden_wins_over_its_models():
    # A row already dropped by its provider is not also counted as a model.
    rows = [UsageRow("openai", "gpt-4o", "", 1, 0, 0, 1, 0, 0.0, 0)]
    kept, dropped, models = filter_hidden(
        rows, Settings(frozenset({"openai"}), frozenset({"gpt-4o"}))
    )
    assert kept == []
    assert dropped == ["openai"]
    assert models == []
