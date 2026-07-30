"""Shared pytest fixtures built on the synthetic DB helpers in ``tests.helpers``."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tests.helpers import T0, RowSpec, create_both_tables, insert_v1, insert_v2

# Baseline ms timestamps (mid-2025) for deterministic span checks.
__all__ = ["T0", "RowSpec", "v1_rows", "v2_rows", "v1_db", "v2_db", "empty_db"]


@pytest.fixture()
def v2_rows() -> list[RowSpec]:
    return [
        # provider,         model,         variant,  inp,  cr,    cw, out, reas, cost,   created
        ("zai-coding-plan", "glm-4.7", "default", 500, 8000, 0, 40, 10, 0.0123, T0),
        ("zai-coding-plan", "glm-4.7", "default", 300, 6000, 0, 20, 0, 0.0, T0 + 1000),
        ("openai", "gpt-4o", "high", 1000, 0, 0, 200, 50, 0.5, T0 + 2000),
        ("openai", "gpt-4o-mini", "low", 100, 0, 0, 10, 0, 0.0, T0 + 3000),
    ]


@pytest.fixture()
def v1_rows() -> list[RowSpec]:
    return [
        # provider,         model,        variant, inp, cr,    cw, out, reas, cost,   created
        ("zai-coding-plan", "glm-5.2", "max", 581, 7168, 0, 25, 57, 0.0, T0 + 5000),
        ("cerebras", "llama-3.1", "fast", 200, 1024, 128, 30, 0, 0.002, T0 + 6000),
        ("cerebras", "llama-3.1", "fast", 200, 1024, 0, 30, 0, 0.0, T0 + 7000),
    ]


@pytest.fixture()
def v2_db(tmp_path, v2_rows) -> str:
    """A v2-shaped database (assistant rows in session_message, empty message)."""
    path = str(tmp_path / "opencode-next.db")
    con = sqlite3.connect(path)
    try:
        create_both_tables(con)
        insert_v2(con, v2_rows)
        con.commit()
    finally:
        con.close()
    return path


@pytest.fixture()
def v1_db(tmp_path, v1_rows) -> str:
    """A v1-shaped database (assistant rows in message; session_message only has
    non-assistant control rows, mimicking a migrated v1 DB)."""
    path = str(tmp_path / "opencode.db")
    con = sqlite3.connect(path)
    try:
        create_both_tables(con)
        insert_v1(con, v1_rows)
        con.execute(
            "INSERT INTO session_message(id, session_id, type, seq, time_created, time_updated, data)"
            " VALUES('ctrl-1', 's1', 'model-switched', 0, ?, ?, ?)",
            (T0, T0, json.dumps({"time": {"created": T0}})),
        )
        con.commit()
    finally:
        con.close()
    return path


@pytest.fixture()
def empty_db(tmp_path) -> str:
    """A database that has both tables but no assistant rows in either."""
    path = str(tmp_path / "empty.db")
    con = sqlite3.connect(path)
    try:
        create_both_tables(con)
        con.commit()
    finally:
        con.close()
    return path
