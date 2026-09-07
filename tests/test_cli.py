"""Tests for the CLI entry point, exit codes, flags, and JSON output."""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO

import pytest

from oc_usage import __version__, cli
from oc_usage.cli import main
from oc_usage.models import UsageRow
from oc_usage.service import ServiceClient
from tests.helpers import T0, FakeService, assistant_message


def _fake_with_data():
    return FakeService(
        ["s1"],
        {
            "s1": [
                assistant_message(
                    "a0", ("zai", "glm-4.7", "default", 800, 14000, 0, 60, 10, 0.0123, T0)
                ),
                assistant_message(
                    "a1", ("openai", "gpt-4o", "high", 1000, 0, 0, 200, 50, 0.5, T0 + 1)
                ),
                assistant_message(
                    "a2", ("openai", "gpt-4o-mini", "low", 100, 0, 0, 10, 0, 0.0, T0 + 2)
                ),
            ]
        },
    )


def patch_service(monkeypatch, fake):
    """Make ``main()`` build a client that talks to ``fake`` instead of subprocess."""
    monkeypatch.setattr(cli, "discover_databases", list)
    monkeypatch.setattr(
        cli, "ServiceClient", lambda: ServiceClient(executable="opencode2", runner=fake)
    )


# ── happy paths ───────────────────────────────────────────────────────────────


def test_json_output_is_valid_and_has_exact_values(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    rc = main(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)

    t = data["totals"]
    assert t["turns"] == 3
    assert t["input"] == 1900
    assert t["cache_read"] == 14000
    assert t["cache_write"] == 0
    assert t["output"] == 270
    assert t["reasoning"] == 60
    assert t["total"] == 1900 + 14000 + 0 + 270 + 60
    assert t["estimated_cost"] > 0
    assert t["estimate_complete"] is True

    assert data["source"] == "OpenCode service"
    assert data["providers"]["zai"]["total"] == 800 + 14000 + 0 + 60 + 10
    assert data["providers"]["openai"]["estimated_cost"] > 0
    assert {"provider", "model", "variant"} <= set(data["models"][0])
    assert data["span"]["from"].endswith("Z")


def test_human_report_exits_zero(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OpenCode Usage" in out
    assert "OpenCode service" in out
    assert "By Provider" in out


def test_default_compact_numbers_are_shown(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    main([])
    out = capsys.readouterr().out
    # 14000 cache read -> compact "14.0K" in the default report.
    assert "14.0K" in out


# ── error / no-data paths ─────────────────────────────────────────────────────


def test_no_executable_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_databases", list)
    monkeypatch.setattr("oc_usage.service.shutil.which", lambda _name: None)
    rc = main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not found" in err
    assert "opencode2" in err


def test_incompatible_executable_exits_1(monkeypatch, capsys):
    fake = _fake_with_data()
    fake.help_stdout = ""  # V1 binary: dumps help to stderr, empty stdout
    patch_service(monkeypatch, fake)
    rc = main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "does not provide" in err


def test_service_unavailable_exits_1(monkeypatch, capsys):
    fake = _fake_with_data()
    fake.mode = "down"
    patch_service(monkeypatch, fake)
    rc = main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "unavailable" in err
    # No DB paths or internal socket details are leaked.
    assert "opencode-next.db" not in err


def test_invalid_json_exits_1(monkeypatch, capsys):
    fake = _fake_with_data()
    fake.mode = "bad-json"
    patch_service(monkeypatch, fake)
    rc = main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "invalid JSON" in err


def test_no_assistant_messages_exits_1(monkeypatch, capsys):
    from tests.helpers import user_message

    fake = FakeService(["s1"], {"s1": [user_message("u1")]})
    patch_service(monkeypatch, fake)
    rc = main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "No assistant messages" in err


# ── flags ─────────────────────────────────────────────────────────────────────


def test_version_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_help_is_short_and_lists_only_public_options(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "OpenCode token usage" in out
    assert "--json" in out
    assert "--db" in out
    assert "--version" in out
    # Removed options must not appear in help.
    for removed in ("--server", "--username", "--password", "--full", "--ascii"):
        assert removed not in out


@pytest.mark.parametrize(
    "flag",
    [
        "--server",
        "--username",
        "--password-stdin",
        "--password-env",
        "--full",
        "--no-color",
        "--plain",
        "--ascii",
        "--bogus",
    ],
)
def test_removed_flags_are_rejected(flag, monkeypatch):
    patch_service(monkeypatch, _fake_with_data())
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 2


def test_manual_database_path_is_used(tmp_path, monkeypatch, capsys):
    path = tmp_path / "custom.db"
    path.touch()
    row = UsageRow("openai", "gpt-4o", "", 10, 0, 0, 1, 0, 0.0, 1)
    monkeypatch.setattr(cli, "load_databases", lambda paths, **_kwargs: ([row], paths))
    assert main(["--db", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["turns"] == 1


def test_missing_manual_database_fails_without_service_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ServiceClient", lambda: pytest.fail("service fallback used"))
    path = tmp_path / "missing.db"
    assert main(["--db", str(path)]) == 1
    assert f"database not found: {path}" in capsys.readouterr().err


# ── automatic ASCII fallback (no public flag) ─────────────────────────────────


def test_legacy_console_encoding_automatically_uses_ascii(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())

    class LegacyTTY(StringIO):
        encoding = "ascii"

        def isatty(self):
            return True

    stream = LegacyTTY()
    monkeypatch.setattr(sys, "stdout", stream)
    assert main([]) == 0
    out = stream.getvalue()
    assert "OpenCode Usage" in out
    assert all(ord(ch) < 128 for ch in out), repr([(c, ord(c)) for c in out if ord(c) >= 128])


# ── hidden providers (user config) ────────────────────────────────────────────


def _use_config(tmp_path, monkeypatch, text: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(text)
    monkeypatch.setenv("OC_STATS_CONFIG", str(path))


def test_hidden_provider_is_excluded_from_json(tmp_path, monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    _use_config(tmp_path, monkeypatch, 'hidden_providers = ["openai"]')
    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    # Excluded entirely: rows gone and totals recomputed from the rest.
    assert set(data["providers"]) == {"zai"}
    assert data["totals"]["turns"] == 1
    assert data["totals"]["input"] == 800
    assert all(m["provider"] == "zai" for m in data["models"])
    assert "1 provider hidden" in data["source"]


def test_hidden_provider_matches_case_insensitively(tmp_path, monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    _use_config(tmp_path, monkeypatch, 'hidden_providers = ["OPENAI"]')
    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["providers"]) == {"zai"}


def test_config_without_hidden_providers_changes_nothing(tmp_path, monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    _use_config(tmp_path, monkeypatch, 'hidden_providers = ["nobody"]')
    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["providers"]) == {"zai", "openai"}
    assert "hidden" not in data["source"]


def test_hidden_model_is_excluded_from_json(tmp_path, monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    # Bare id hides gpt-4o wherever it appears; the qualified id spares zai's.
    _use_config(tmp_path, monkeypatch, 'hidden_models = ["gpt-4o", "zai/glm-4.7"]')
    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [m["model"] for m in data["models"]] == ["gpt-4o-mini"]
    assert data["totals"]["turns"] == 1
    assert "2 models hidden" in data["source"]


def test_provider_qualified_model_only_hides_that_provider(tmp_path, monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    _use_config(tmp_path, monkeypatch, 'hidden_models = ["openai/gpt-4o"]')
    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {m["model"] for m in data["models"]} == {"glm-4.7", "gpt-4o-mini"}


def test_hidden_note_lists_providers_and_models(tmp_path, monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    _use_config(tmp_path, monkeypatch, 'hidden_providers = ["zai"]\nhidden_models = ["gpt-4o"]\n')
    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "1 provider · 1 model hidden" in data["source"]


def test_broken_config_exits_1(tmp_path, monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    _use_config(tmp_path, monkeypatch, "hidden_providers = ")
    assert main([]) == 1
    err = capsys.readouterr().err
    assert "invalid TOML" in err


# ── aborted turns (no token object) ───────────────────────────────────────────


def _fake_with_abort():
    from tests.helpers import aborted_message

    return FakeService(
        ["s1"],
        {
            "s1": [
                assistant_message(
                    "a0", ("zai", "glm-4.7", "default", 800, 14000, 0, 60, 10, 0.0123, T0)
                ),
                aborted_message("a1", ("openai", "gpt-4o", "high", 0, 0, 0, 0, 0, 0.0, T0 + 1)),
                aborted_message("a2", ("openai", "gpt-4o", "high", 0, 0, 0, 0, 0, 0.0, T0 + 2)),
            ]
        },
    )


def test_aborted_turns_are_excluded_and_noted(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_abort())
    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["totals"]["turns"] == 1
    assert set(data["providers"]) == {"zai"}
    assert "2 aborted turns excluded" in data["source"]


def test_aborted_turns_do_not_mark_estimate_incomplete(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_abort())
    assert main(["--json"]) == 0
    totals = json.loads(capsys.readouterr().out)["totals"]
    assert totals["estimate_complete"] is True


def test_failed_turns_with_recorded_tokens_still_count(monkeypatch, capsys):
    # An error turn that OpenCode still billed (tokens present) is usage.
    patch_service(monkeypatch, _fake_with_data())
    assert main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["turns"] == 3


def test_no_note_when_nothing_aborted(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    assert main(["--json"]) == 0
    assert "aborted" not in json.loads(capsys.readouterr().out)["source"]


# ── mini subcommand ───────────────────────────────────────────────────────────


def test_mini_merges_variants_into_one_model_row(monkeypatch, capsys):
    fake = FakeService(
        ["s1"],
        {
            "s1": [
                assistant_message("a0", ("openai", "gpt-4o", "high", 100, 0, 0, 10, 0, 0.1, T0)),
                assistant_message("a1", ("openai", "gpt-4o", "low", 50, 0, 0, 5, 0, 0.05, T0 + 1)),
                assistant_message("a2", ("zai", "glm-4.7", "default", 10, 0, 0, 1, 0, 0.0, T0 + 2)),
            ]
        },
    )
    patch_service(monkeypatch, fake)
    assert main(["--json", "mini"]) == 0
    data = json.loads(capsys.readouterr().out)
    models = {(m["provider"], m["model"], m["variant"]): m for m in data["models"]}
    assert ("openai", "gpt-4o", "") in models
    assert ("openai", "gpt-4o", "high") not in models
    assert models[("openai", "gpt-4o", "")]["turns"] == 2
    assert models[("openai", "gpt-4o", "")]["input"] == 150
    # One row per model, and the note says what happened.
    assert len(data["models"]) == 2
    assert "variants merged" in data["source"]


def test_mini_report_renders(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    assert main(["mini"]) == 0
    out = capsys.readouterr().out
    assert "OpenCode Usage" in out
    assert "variants merged" in out


def test_mini_keeps_hidden_config_filtering(tmp_path, monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    _use_config(tmp_path, monkeypatch, 'hidden_providers = ["zai"]\n')
    assert main(["--json", "mini"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "1 provider hidden" in data["source"]
    assert "variants merged" in data["source"]


def test_db_before_mini_subcommand_is_kept():
    args = cli.build_parser().parse_args(["--db", "custom.db", "mini"])
    assert args.command == "mini"
    assert [path.name for path in args.db] == ["custom.db"]


# ── python -m & entry point ───────────────────────────────────────────────────


def test_python_m_module_runs():
    result = subprocess.run(
        [sys.executable, "-m", "oc_usage", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_python_m_module_help():
    result = subprocess.run(
        [sys.executable, "-m", "oc_usage", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "OpenCode token usage" in result.stdout
    assert "--db" in result.stdout


def test_db_before_tui_subcommand_is_kept():
    # ``--db`` is a global option; it must survive when placed before ``tui``
    # instead of being silently overwritten by the subparser default.
    args = cli.build_parser().parse_args(["--db", "custom.db", "tui"])
    assert args.command == "tui"
    assert [path.name for path in args.db] == ["custom.db"]


def test_json_includes_recorded_cost(monkeypatch, capsys):
    patch_service(monkeypatch, _fake_with_data())
    assert main(["--json"]) == 0
    totals = json.loads(capsys.readouterr().out)["totals"]
    assert totals["recorded_cost"] == pytest.approx(0.5123)
