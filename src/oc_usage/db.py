"""Database discovery, schema detection, and row loading.

OpenCode stores session data in a SQLite database. Two schemas are supported:

* **v2** (``opencode-next.db``) — canonical rows live in ``session_message`` and
  are filtered by the SQL column ``type = 'assistant'``. The ``data`` JSON holds
  a nested ``model: {id, providerID, variant}`` and
  ``tokens: {input, output, reasoning, cache: {read, write}}``.
* **v1** (``opencode.db``) — canonical rows live in ``message`` and are filtered
  by the JSON path ``$.role == 'assistant'``. The ``data`` JSON holds flat
  ``modelID`` / ``providerID`` / ``variant`` fields and a ``tokens`` object that
  also carries a ``total`` (which we validate but do not rely on).

Detection is row-based, not table-based: a migrating v1 database can contain an
(almost empty) ``session_message`` table, and a v2 database can contain an empty
``message`` table. We pick the schema whose assistant query actually returns
rows.

All database access is strictly read-only (``mode=ro`` URI), so an active WAL is
safe to read concurrently.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from typing import Any

from oc_usage.models import UNKNOWN, UsageRow

DEFAULT_DB_CANDIDATES = ("opencode-next.db", "opencode.db")


def default_db_dirs() -> tuple[str, ...]:
    """Return platform-appropriate directories in discovery priority order.

    OpenCode follows the platform's conventional per-user data directory. On
    Unix, ``XDG_DATA_HOME`` takes precedence over the standard
    ``~/.local/share`` fallback; macOS and Windows use their native application
    support directories. The tuple leaves room for additional fallbacks while
    keeping discovery deterministic.
    """
    if os.name == "nt" or sys.platform.startswith("win"):
        data_root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    elif sys.platform == "darwin":
        data_root = os.path.expanduser("~/Library/Application Support")
    else:
        data_root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return (os.path.join(data_root, "opencode"),)


# Kept as a public compatibility alias for callers/tests that used the old
# Linux-only constant. ``find_db`` recalculates the default so environment and
# platform changes made after import are still respected.
DEFAULT_DB_DIR = default_db_dirs()[0]
_INITIAL_DEFAULT_DB_DIR = DEFAULT_DB_DIR

V2 = "v2"
V1 = "v1"


class DatabaseNotFoundError(FileNotFoundError):
    """The requested database file does not exist."""


class NoUsageDataError(RuntimeError):
    """The database exists but contains no assistant rows in a known schema."""


# ── discovery ─────────────────────────────────────────────────────────────────


def find_db(arg: str | None) -> str:
    """Resolve the database path to read.

    An explicit ``--db`` argument always wins. Otherwise we prefer the v2
    ``opencode-next.db`` when present and fall back to the legacy
    ``opencode.db``. If neither exists we return the (non-existent) v2 path so
    the caller can emit a clear "not found" error.
    """
    if arg:
        return os.path.expanduser(arg)
    # Honor the legacy constant when a caller explicitly overrides it, while
    # otherwise resolving the platform/environment at call time.
    directories = (
        (DEFAULT_DB_DIR,) if DEFAULT_DB_DIR != _INITIAL_DEFAULT_DB_DIR else default_db_dirs()
    )
    # Check the preferred filename across all candidate directories before
    # falling back to v1. We read exactly one path and never merge databases.
    for name in DEFAULT_DB_CANDIDATES:
        for directory in directories:
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                return candidate
    return os.path.join(directories[0], DEFAULT_DB_CANDIDATES[0])


# ── schema detection ─────────────────────────────────────────────────────────


def _connect(db_path: str) -> sqlite3.Connection:
    """Open the database read-only via a SQLite URI (safe with an active WAL)."""
    # Normalize to an absolute path and URL-encode it minimally for the URI.
    abs_path = os.path.abspath(db_path)
    uri = "file:" + abs_path.replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    cur = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
    return cur.fetchone() is not None


def _count_where(con: sqlite3.Connection, table: str, where: str) -> int:
    """Count rows matching ``where``; return 0 on any database error."""
    try:
        cur = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")  # noqa: S608
        row = cur.fetchone()
    except sqlite3.DatabaseError:
        return 0
    return int(row[0]) if row else 0


def _has_v1_assistant_rows(con: sqlite3.Connection) -> bool:
    """Return whether ``message`` contains a valid assistant JSON object.

    Do not use ``json_extract`` here: SQLite raises ``malformed JSON`` for an
    invalid row, which would hide otherwise valid v1 rows from detection.
    Parsing in Python also gives loading and detection identical malformed-row
    behavior.
    """
    try:
        cur = con.execute("SELECT data FROM message")
    except sqlite3.DatabaseError:
        return False
    for (raw,) in cur:
        if not isinstance(raw, str):
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("role") == "assistant":
            return True
    return False


def detect_schema(con: sqlite3.Connection) -> str | None:
    """Detect which schema actually carries assistant rows.

    Returns ``"v2"``, ``"v1"``, or ``None`` when no assistant rows are found in
    either known table. A single database is read from exactly one source table
    so history is never double-counted.
    """
    # v2 first: session_message with a type column = 'assistant'.
    if (
        _table_exists(con, "session_message")
        and _count_where(con, "session_message", "type = 'assistant'") > 0
    ):
        return V2
    # v1 next: parse the JSON in Python so one malformed row cannot abort the
    # query and hide valid assistant rows later in the table.
    if _table_exists(con, "message") and _has_v1_assistant_rows(con):
        return V1
    return None


# ── row loading ───────────────────────────────────────────────────────────────


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_tokens(data: dict[str, Any]) -> dict[str, int]:
    """Pull the token components shared by both schemas."""
    tokens = data.get("tokens") or {}
    if not isinstance(tokens, dict):
        return {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "reasoning": 0}
    cache = tokens.get("cache") or {}
    if not isinstance(cache, dict):
        cache = {}
    return {
        "input": _as_int(tokens.get("input")),
        "cache_read": _as_int(cache.get("read")),
        "cache_write": _as_int(cache.get("write")),
        "output": _as_int(tokens.get("output")),
        "reasoning": _as_int(tokens.get("reasoning")),
    }


def _model_from_v2(data: dict[str, Any]) -> tuple[str, str, str]:
    model = data.get("model")
    if isinstance(model, str):
        return model, UNKNOWN, ""
    if isinstance(model, dict):
        return (
            model.get("id") or UNKNOWN,
            model.get("providerID") or model.get("provider") or UNKNOWN,
            model.get("variant") or "",
        )
    return UNKNOWN, UNKNOWN, ""


def _model_from_v1(data: dict[str, Any]) -> tuple[str, str, str]:
    return (
        data.get("modelID") or data.get("model") or UNKNOWN,
        data.get("providerID") or data.get("provider") or UNKNOWN,
        data.get("variant") or "",
    )


def _time_created(data: dict[str, Any]) -> int:
    time_obj = data.get("time")
    if isinstance(time_obj, dict):
        return _as_int(time_obj.get("created"))
    return 0


def _row_from_data(data: dict[str, Any], schema: str) -> UsageRow:
    if schema == V2:
        model_id, provider, variant = _model_from_v2(data)
    else:
        model_id, provider, variant = _model_from_v1(data)
    tokens = _extract_tokens(data)
    return UsageRow(
        provider=provider,
        model=model_id,
        variant=variant,
        input=tokens["input"],
        cache_read=tokens["cache_read"],
        cache_write=tokens["cache_write"],
        output=tokens["output"],
        reasoning=tokens["reasoning"],
        cost=_as_float(data.get("cost")),
        time_created=_time_created(data),
    )


def _select_query(schema: str) -> tuple[str, str]:
    """Return (table, where-clause) used to stream assistant rows for a schema."""
    if schema == V2:
        return "session_message", "type = 'assistant'"
    # v1 JSON is filtered after parsing in Python. SQLite's json_extract raises
    # on malformed data, which must be skipped rather than aborting the load.
    return "message", "1"


def load_rows(db_path: str) -> Iterator[UsageRow]:
    """Yield normalized assistant turns from ``db_path``.

    Raises :class:`DatabaseNotFoundError` if the file is absent,
    :class:`NoUsageDataError` if the database exists but has no assistant rows
    in any supported schema. Malformed JSON in individual rows is skipped.
    """
    if not os.path.exists(db_path):
        raise DatabaseNotFoundError(db_path)

    con = _connect(db_path)
    try:
        schema = detect_schema(con)
        if schema is None:
            raise NoUsageDataError(db_path)
        table, where = _select_query(schema)
        cur = con.execute(f"SELECT data FROM {table} WHERE {where}")  # noqa: S608
        for (raw,) in cur:
            if not isinstance(raw, str):
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            if schema == V1 and data.get("role") != "assistant":
                continue
            yield _row_from_data(data, schema)
    finally:
        con.close()
