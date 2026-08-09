"""Tests for the local read-only database fast path."""

from __future__ import annotations

import json
import sqlite3

import pytest

from oc_usage.database import DatabaseClient, DatabaseError, discover_database


def create_database(path, messages):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE session_message (type TEXT, data TEXT)")
    connection.executemany(
        "INSERT INTO session_message (type, data) VALUES (?, ?)",
        [(message.get("type", "assistant"), json.dumps(message)) for message in messages],
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
