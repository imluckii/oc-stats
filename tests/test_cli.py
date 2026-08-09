"""Tests for the CLI entry point, exit codes, flags, and JSON output."""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO

import pytest

from oc_usage import __version__, cli
from oc_usage.cli import main
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
    monkeypatch.setattr(cli, "discover_database", lambda: None)
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
    monkeypatch.setattr(cli, "discover_database", lambda: None)
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
    assert "--version" in out
    # Removed options must not appear in help.
    for removed in ("--db", "--server", "--username", "--password", "--full", "--ascii"):
        assert removed not in out


@pytest.mark.parametrize(
    "flag",
    [
        "--db",
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
    assert "--db" not in result.stdout
