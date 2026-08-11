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
