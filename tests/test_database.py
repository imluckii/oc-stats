"""Tests for the local read-only database fast path."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from oc_usage.database import (
    DatabaseClient,
    DatabaseError,
    discover_database,
    discover_databases,
    load_databases,
)


def create_database(path, messages):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE session_message (id TEXT, type TEXT, data TEXT)")
    connection.executemany(
        "INSERT INTO session_message (id, type, data) VALUES (?, ?, ?)",
        [
            (f"msg-{index}", message.get("type", "assistant"), json.dumps(message))
            for index, message in enumerate(messages)
        ],
    )
    connection.commit()
    connection.close()


def create_v1_database(path, messages):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE message (data TEXT)")
    connection.executemany(
        "INSERT INTO message (data) VALUES (?)", [(json.dumps(m),) for m in messages]
    )
    connection.commit()
    connection.close()


def assistant(model="gpt-4o"):
    return {
        "type": "assistant",
        "model": {"providerID": "openai", "id": model, "variant": "high"},
        "tokens": {
            "input": 10,
            "output": 2,
            "reasoning": 1,
            "cache": {"read": 20, "write": 0},
        },
        "cost": 99,
        "time": {"created": 1_000},
    }


def test_database_client_reads_assistant_rows(tmp_path):
    path = tmp_path / "opencode-next.db"
    create_database(path, [assistant(), {"type": "user", "time": {"created": 2_000}}])
    rows = list(DatabaseClient(path).rows())
    assert len(rows) == 1
    assert rows[0].model == "gpt-4o"
    assert rows[0].total == 33


def test_database_client_skips_malformed_rows(tmp_path):
    path = tmp_path / "opencode-next.db"
    create_database(path, [{"type": "assistant"}, assistant("gpt-4o-mini")])
    rows = list(DatabaseClient(path).rows())
    assert [row.model for row in rows] == ["gpt-4o-mini"]


def test_database_client_reads_legacy_opencode_database(tmp_path):
    path = tmp_path / "opencode.db"
    create_v1_database(
        path,
        [
            {"role": "user"},
            {
                "role": "assistant",
                "providerID": "openai",
                "modelID": "gpt-4o",
                "variant": "high",
                "tokens": {
                    "input": 10,
                    "output": 2,
                    "reasoning": 1,
                    "cache": {"read": 20, "write": 3},
                },
                "time": {"created": 1_000},
            },
        ],
    )
    rows = list(DatabaseClient(path).rows())
    assert len(rows) == 1
    assert rows[0].provider == "openai"
    assert rows[0].model == "gpt-4o"
    assert rows[0].total == 36


def test_database_client_rejects_wrong_schema(tmp_path):
    path = tmp_path / "opencode-next.db"
    sqlite3.connect(path).close()
    with pytest.raises(DatabaseError):
        list(DatabaseClient(path).rows())


def test_discover_database_uses_xdg_data_home(tmp_path, monkeypatch):
    path = tmp_path / "opencode" / "opencode-next.db"
    path.parent.mkdir()
    path.touch()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert discover_database() == path


def test_discover_database_uses_windows_user_profile(tmp_path, monkeypatch):
    path = tmp_path / ".local" / "share" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True)
    path.touch()
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert discover_database() == path


def test_discover_databases_returns_all_newest_first(tmp_path, monkeypatch):
    root = tmp_path / "opencode"
    root.mkdir()
    older = root / "opencode-next.db"
    newer = root / "opencode.db"
    older.touch()
    newer.touch()
    older.touch()
    newer.touch()
    older_time = 1_000_000_000
    newer_time = older_time + 100
    os.utime(older, (older_time, older_time))
    os.utime(newer, (newer_time, newer_time))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    databases = discover_databases()
    assert newer in databases and older in databases
    assert databases.index(newer) < databases.index(older)


def test_load_databases_deduplicates_shared_message_ids(tmp_path):
    older = tmp_path / "opencode-next.db"
    newer = tmp_path / "opencode.db"
    create_database(older, [assistant("gpt-4o"), assistant("gpt-4o-mini"), assistant("o3")])
    create_database(newer, [assistant("gpt-5.6-sol"), assistant("gpt-4o-mini")])

    rows, used = load_databases([newer, older])

    assert used == [newer, older]
    assert [row.model for row in rows] == ["gpt-5.6-sol", "gpt-4o-mini", "o3"]


# ── full V2 schema: session ledger, forks ─────────────────────────────────────


def create_full_database(path, sessions, messages):
    """A database with the real ``session_v2`` + ``session_message`` layout."""
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE session_v2 (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            parent_id TEXT,
            fork_session_id TEXT,
            fork_boundary TEXT,
            directory TEXT,
            cost REAL DEFAULT 0 NOT NULL,
            tokens_input INTEGER DEFAULT 0 NOT NULL,
            tokens_output INTEGER DEFAULT 0 NOT NULL,
            tokens_reasoning INTEGER DEFAULT 0 NOT NULL,
            tokens_cache_read INTEGER DEFAULT 0 NOT NULL,
            tokens_cache_write INTEGER DEFAULT 0 NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL
        )"""
    )
    connection.executemany(
        "INSERT INTO session_v2 (id, project_id, fork_session_id, cost, tokens_input, "
        "tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, "
        "time_created, time_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        sessions,
    )
    connection.execute(
        """CREATE TABLE session_message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            seq INTEGER NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        )"""
    )
    connection.executemany(
        "INSERT INTO session_message (id, session_id, type, seq, time_created, "
        "time_updated, data) VALUES (?,?,?,?,?,?,?)",
        messages,
    )
    connection.commit()
    connection.close()


T0 = 1_750_000_000_000


def _msg(
    message_id,
    session_id,
    seq,
    created,
    *,
    model="gpt-4o",
    input_=10,
    cache_read=0,
    output=2,
    reasoning=1,
    cost=0.05,
):
    data = {
        "model": {"providerID": "openai", "id": model, "variant": "high"},
        "tokens": {
            "input": input_,
            "output": output,
            "reasoning": reasoning,
            "cache": {"read": cache_read, "write": 0},
        },
        "cost": cost,
        "time": {"created": created},
    }
    return (message_id, session_id, "assistant", seq, created, created, json.dumps(data))


def _ses(session_id, *, fork=None, created=T0, ledger=(0, 0, 0, 0, 0), cost=0.0):
    return (
        session_id,
        "prj",
        fork,
        cost,
        ledger[0],
        ledger[1],
        ledger[2],
        ledger[3],
        ledger[4],
        created,
        created,
    )


def test_fork_copied_history_is_excluded(tmp_path):
    path = tmp_path / "opencode.db"
    # The fork copies the parent's early turn (fresh id, earlier timestamp)
    # and then adds one turn of its own.
    create_full_database(
        path,
        [
            _ses("ses_parent", created=T0),
            _ses("ses_fork", fork="ses_parent", created=T0 + 10_000),
        ],
        [
            _msg("m_parent", "ses_parent", 1, T0 + 1_000),
            _msg("m_copy", "ses_fork", 1, T0 + 1_000, input_=500),
            _msg("m_own", "ses_fork", 2, T0 + 20_000, input_=7),
        ],
    )
    rows = list(DatabaseClient(path).rows())
    assert sorted(row.input for row in rows) == [7, 10]  # parent's 10 + fork's own 7


def test_fork_ledger_reconciliation_uses_kept_messages_only(tmp_path):
    path = tmp_path / "opencode.db"
    # Fork ledger counts only post-fork usage; the copy must not offset it.
    create_full_database(
        path,
        [
            _ses("ses_parent", created=T0),
            _ses(
                "ses_fork",
                fork="ses_parent",
                created=T0 + 10_000,
                ledger=(7, 2, 1, 0, 0),
                cost=0.05,
            ),
        ],
        [
            _msg("m_copy", "ses_fork", 1, T0 + 1_000, input_=500),
            _msg("m_own", "ses_fork", 2, T0 + 20_000, input_=7, output=2, reasoning=1),
        ],
    )
    rows, used = load_databases([path])
    assert used == [path]
    # The fork's own message exactly covers its ledger: no unattributed row.
    assert len(rows) == 1
    assert rows[0].input == 7


def test_ledger_difference_becomes_unattributed_row(tmp_path):
    path = tmp_path / "opencode.db"
    # Title/compaction/reverted usage: ledger exceeds retained messages.
    create_full_database(
        path,
        [_ses("ses_x", created=T0, ledger=(25, 4, 2, 5, 0), cost=0.30)],
        [
            _msg(
                "m1",
                "ses_x",
                1,
                T0 + 1_000,
                input_=10,
                cache_read=5,
                output=4,
                reasoning=2,
                cost=0.05,
            )
        ],
    )
    rows, _used = load_databases([path])
    assert len(rows) == 2
    internal = rows[1]
    assert internal.provider == "(unattributed)"
    assert internal.model == "(internal usage)"
    assert (internal.input, internal.cache_read, internal.output, internal.reasoning) == (
        15,
        0,
        0,
        0,
    )
    assert internal.cost == 0.25
    assert internal.time_created == T0


def test_zero_ledger_with_messages_adds_nothing(tmp_path):
    path = tmp_path / "opencode.db"
    create_full_database(
        path,
        [_ses("ses_legacy", created=T0, ledger=(0, 0, 0, 0, 0), cost=0.0)],
        [_msg("m1", "ses_legacy", 1, T0 + 1_000, input_=10)],
    )
    rows, _used = load_databases([path])
    assert len(rows) == 1
    assert rows[0].input == 10


def test_mixed_schema_merges_current_and_legacy_messages(tmp_path):
    path = tmp_path / "opencode.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE session_message (id TEXT, type TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO session_message (id, type, data) VALUES (?, ?, ?)",
        ("sm1", "assistant", json.dumps(assistant())),
    )
    connection.execute("CREATE TABLE message (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO message (id, data) VALUES (?, ?)",
        ("sm1", json.dumps({"role": "assistant", "providerID": "openai", "modelID": "gpt-4o"})),
    )
    connection.execute(
        "INSERT INTO message (id, data) VALUES (?, ?)",
        ("lg1", json.dumps({"role": "assistant", "providerID": "openai", "modelID": "o3"})),
    )
    connection.commit()
    connection.close()
    rows = list(DatabaseClient(path).rows())
    assert sorted(row.model for row in rows) == ["gpt-4o", "o3"]


def test_discover_databases_matches_channel_databases(tmp_path, monkeypatch):
    root = tmp_path / "opencode"
    root.mkdir()
    standard = root / "opencode.db"
    channel = root / "opencode-beta.db"
    backup = root / "opencode-next.db.bak"
    for path in (standard, channel, backup):
        path.touch()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    databases = discover_databases()
    assert standard in databases and channel in databases
    assert backup not in databases
