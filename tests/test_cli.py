"""Tests for the CLI entry point, exit codes, and flags."""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO

import pytest

from oc_usage import __version__
from oc_usage.cli import main

ANSI = "\x1b["


# ── happy paths ───────────────────────────────────────────────────────────────


def test_json_output_is_valid_and_complete(v2_db, capsys):
    rc = main(["--db", v2_db, "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["totals"]["turns"] == 4
    assert data["totals"]["cost_tracked"] is True
    assert "zai-coding-plan" in data["providers"]
    assert {"provider", "model", "variant"} <= set(data["models"][0])


def test_human_report_exits_zero(v2_db, capsys):
    rc = main(["--db", v2_db])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OpenCode Usage" in out
    assert "By Provider" in out


def test_v1_database_works(v1_db, capsys):
    rc = main(["--db", v1_db, "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["totals"]["turns"] == 3
    assert "cerebras" in data["providers"]


def test_full_mode_shows_unrounded_numbers(v2_db, capsys):
    # zai-coding-plan/glm-4.7 aggregates cache_read 8000+6000 = 14000.
    main(["--db", v2_db, "--full"])
    out = capsys.readouterr().out
    assert "14,000" in out


def test_compact_mode_shows_rounded_numbers(v2_db, capsys):
    main(["--db", v2_db])  # default = compact; 14000 -> "14.0K"
    out = capsys.readouterr().out
    assert "14.0K" in out


# ── error / no-data paths ─────────────────────────────────────────────────────


def test_missing_db_exits_1(tmp_path, capsys):
    missing = str(tmp_path / "absent.db")
    rc = main(["--db", missing])
    err = capsys.readouterr().err
    assert rc == 1
    assert "database not found" in err


def test_empty_db_exits_1(empty_db, capsys):
    rc = main(["--db", empty_db])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no assistant messages" in err


# ── flags ─────────────────────────────────────────────────────────────────────


def test_version_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_help_prints_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "OpenCode token usage" in out
    assert "--db" in out
    assert "--json" in out


def test_plain_flag_produces_no_ansi(v2_db, capsys):
    # capsys replaces stdout with a non-tty, so color is already off; this still
    # confirms the captured stream carries no escape codes.
    main(["--db", v2_db, "--plain"])
    out = capsys.readouterr().out
    assert ANSI not in out


def test_no_color_is_alias_for_plain(v2_db, capsys):
    main(["--db", v2_db, "--no-color"])
    out = capsys.readouterr().out
    assert ANSI not in out


def test_ascii_flag_uses_ascii_only_rendering(v2_db, capsys):
    rc = main(["--db", v2_db, "--ascii"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OpenCode Usage" in out
    assert "=" in out
    assert all(ord(char) < 128 for char in out), repr([(c, ord(c)) for c in out if ord(c) >= 128])


def test_legacy_console_encoding_automatically_uses_ascii(v2_db, monkeypatch):
    class LegacyTTY(StringIO):
        encoding = "ascii"

        def isatty(self):
            return True

    stream = LegacyTTY()
    monkeypatch.setattr(sys, "stdout", stream)

    assert main(["--db", v2_db]) == 0
    out = stream.getvalue()
    assert "OpenCode Usage" in out
    assert all(ord(char) < 128 for char in out), repr([(c, ord(c)) for c in out if ord(c) >= 128])


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
