"""Builders for synthetic OpenCode v1/v2 SQLite databases used in tests.

All data here is **synthetic**. The shapes mirror the exact on-disk schemas
documented in ``oc_usage.db``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

# A flat, human-friendly row spec used to generate fixtures:
#   (provider, model, variant, input, cache_read, cache_write,
#    output, reasoning, cost, created_ms)
RowSpec = tuple[str, str, str, int, int, int, int, int, float, int]

# Baseline ms timestamps (mid-2025) for deterministic span checks.
T0 = 1_750_000_000_000


def v2_data(spec: RowSpec) -> dict[str, Any]:
    provider, model, variant, inp, cr, cw, out, reas, cost, created = spec
    return {
        "model": {"id": model, "providerID": provider, "variant": variant},
        "tokens": {
            "input": inp,
            "output": out,
            "reasoning": reas,
            "cache": {"read": cr, "write": cw},
        },
        "cost": cost,
        "time": {"created": created},
    }


def v1_data(spec: RowSpec) -> dict[str, Any]:
    provider, model, variant, inp, cr, cw, out, reas, cost, created = spec
    total = inp + cr + cw + out + reas  # validated to match the stored total
    return {
        "role": "assistant",
        "modelID": model,
        "providerID": provider,
        "variant": variant,
        "tokens": {
            "total": total,
            "input": inp,
            "output": out,
            "reasoning": reas,
            "cache": {"read": cr, "write": cw},
        },
        "cost": cost,
        "time": {"created": created},
    }


def create_both_tables(con: sqlite3.Connection) -> None:
    """Create both known tables so schema detection must rely on row content."""
    con.execute(
        """
        CREATE TABLE session_message (
            id text PRIMARY KEY,
            session_id text NOT NULL,
            type text NOT NULL,
            seq integer NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE message (
            id text PRIMARY KEY,
            session_id text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        )
        """
    )


def insert_v2(con: sqlite3.Connection, specs: Iterable[RowSpec]) -> None:
    for i, spec in enumerate(specs):
        con.execute(
            "INSERT INTO session_message(id, session_id, type, seq, time_created, time_updated, data)"
            " VALUES(?, 's1', 'assistant', ?, ?, ?, ?)",
            (f"v2-{i}", i, T0 + i, T0 + i, json.dumps(v2_data(spec))),
        )


def insert_v1(con: sqlite3.Connection, specs: Iterable[RowSpec]) -> None:
    for i, spec in enumerate(specs):
        con.execute(
            "INSERT INTO message(id, session_id, time_created, time_updated, data)"
            " VALUES(?, 's1', ?, ?, ?)",
            (f"v1-{i}", T0 + i, T0 + i, json.dumps(v1_data(spec))),
        )


def build_v2_db(path: str, specs: Iterable[RowSpec]) -> str:
    con = sqlite3.connect(path)
    try:
        create_both_tables(con)
        insert_v2(con, specs)
        con.commit()
    finally:
        con.close()
    return str(path)


def build_v1_db(path: str, specs: Iterable[RowSpec]) -> str:
    con = sqlite3.connect(path)
    try:
        create_both_tables(con)
        insert_v1(con, specs)
        con.commit()
    finally:
        con.close()
    return str(path)
