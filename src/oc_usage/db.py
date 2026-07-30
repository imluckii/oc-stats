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
import ntpath
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any

from oc_usage.models import UNKNOWN, UsageRow

CHANNEL_DB_CANDIDATES = (
    "opencode-next.db",
    "opencode.db",
    "opencode-dev.db",
    "opencode-local.db",
)
# Public compatibility name retained for callers that used the original
# two-entry tuple.  It now covers every supported OpenCode channel.
DEFAULT_DB_CANDIDATES = CHANNEL_DB_CANDIDATES


def _xdg_data_root() -> str:
    """Resolve the current xdg-basedir data root on the host platform."""
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        return configured
    if os.name == "nt" or sys.platform.startswith("win"):
        profile = os.environ.get("USERPROFILE")
        if profile:
            return os.path.join(profile, ".local", "share")
    return os.path.expanduser("~/.local/share")


def default_db_dirs() -> tuple[str, ...]:
    """Return platform-appropriate directories in discovery priority order.

    OpenCode core uses xdg-basedir on Linux, macOS, and Windows: the current
    directory is ``${XDG_DATA_HOME:-~/.local/share}/opencode``. Older native
    macOS/Windows locations remain compatibility fallbacks, after the current
    XDG location, and the tuple is always deterministic.
    """
    # OpenCode core uses xdg-basedir on every desktop platform.  Keep this
    # current directory first even on macOS and Windows; the native locations
    # below are compatibility fallbacks for older installations.
    if os.name == "nt" or sys.platform.startswith("win"):
        # OpenCode/xdg-basedir has used both the XDG-style and native Windows
        # locations over time.  A configured XDG root wins, followed by the
        # xdg-basedir home fallback, LOCALAPPDATA, and the conventional native
        # fallback.  Deduplication matters when those roots are the same.
        user_profile = os.environ.get("USERPROFILE")
        home_data = (
            os.path.join(user_profile, ".local", "share")
            if user_profile
            else os.path.expanduser("~/.local/share")
        )
        xdg_root = os.environ.get("XDG_DATA_HOME") or home_data
        home_local_app_data = (
            os.path.join(user_profile, "AppData", "Local")
            if user_profile
            else os.path.expanduser("~/AppData/Local")
        )
        roots = [xdg_root]
        if home_data not in roots:
            roots.append(home_data)
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            roots.append(local_app_data)
        roots.append(home_local_app_data)

        unique_roots = []
        seen = set()
        for root in roots:
            if not root:
                continue
            key = os.path.normcase(os.path.normpath(root))
            if key in seen:
                continue
            seen.add(key)
            unique_roots.append(root)
        directories = [os.path.join(root, "opencode") for root in unique_roots]
        return tuple(_append_compatibility_dirs(directories))

    xdg_root = _xdg_data_root()
    directories = [os.path.join(xdg_root, "opencode")]
    if sys.platform == "darwin":
        directories.append(
            os.path.join(os.path.expanduser("~/Library/Application Support"), "opencode")
        )
    return tuple(_dedupe_dirs(directories))


def _dedupe_dirs(directories: list[str]) -> list[str]:
    """Return directories in order, removing spelling-equivalent duplicates."""
    unique: list[str] = []
    seen: set[str] = set()
    for directory in directories:
        if not directory:
            continue
        key = os.path.normcase(os.path.normpath(directory))
        if key in seen:
            continue
        seen.add(key)
        unique.append(directory)
    return unique


def _append_compatibility_dirs(directories: list[str]) -> list[str]:
    """Append native Windows locations after the current XDG locations."""
    user_profile = os.environ.get("USERPROFILE")
    home_native = (
        os.path.join(user_profile, "AppData", "Local", "opencode")
        if user_profile
        else os.path.join(os.path.expanduser("~/AppData/Local"), "opencode")
    )
    local_app_data = os.environ.get("LOCALAPPDATA")
    compatibility = []
    if local_app_data:
        compatibility.append(os.path.join(local_app_data, "opencode"))
    compatibility.append(home_native)
    return _dedupe_dirs(directories + compatibility)


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

    An explicit ``--db`` argument always wins, followed by ``OPENCODE_DB``.
    Otherwise filenames are considered in channel order (v2 next, stable v1,
    dev, local) across directories in deterministic priority order. If none
    exists we return the (non-existent) first candidate path so the caller can
    emit a clear "not found" error.
    """
    if arg:
        return _expand_db_path(arg, explicit=True)
    configured = os.environ.get("OPENCODE_DB")
    if configured:
        return _expand_db_path(configured, explicit=False)
    # Honor the legacy constant when a caller explicitly overrides it, while
    # otherwise resolving the platform/environment at call time.
    directories = (
        (DEFAULT_DB_DIR,) if DEFAULT_DB_DIR != _INITIAL_DEFAULT_DB_DIR else default_db_dirs()
    )
    # Check one filename at a time across directories.  This makes precedence
    # deterministic (channel preference before directory preference) while
    # still selecting exactly one path; we never merge databases.
    for name in DEFAULT_DB_CANDIDATES:
        for directory in directories:
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                return candidate
    return os.path.join(directories[0], DEFAULT_DB_CANDIDATES[0])


def _is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def _expand_db_path(value: str, *, explicit: bool) -> str:
    """Expand ``--db`` or ``OPENCODE_DB`` according to OpenCode's data root.

    ``OPENCODE_DB`` is special: a relative value is relative to the current
    XDG OpenCode data directory, not the process working directory.  CLI paths
    retain normal command-line semantics and are relative to the working
    directory.  ``:memory:`` is returned unchanged so the caller can provide a
    useful post-process-exit error rather than opening a transient database.
    """
    if value == ":memory:":
        return value
    expanded = os.path.expanduser(value)
    is_absolute = ntpath.isabs(expanded) if _is_windows() else os.path.isabs(expanded)
    if not explicit and not is_absolute:
        current_root = _xdg_data_root()
        expanded = os.path.join(current_root, "opencode", expanded)
    return expanded


# ── schema detection ─────────────────────────────────────────────────────────


def _sqlite_uri(db_path: str) -> str:
    """Return a correctly escaped read-only SQLite URI for ``db_path``.

    ``file:`` URIs are URLs, not ordinary filesystem paths. In particular,
    spaces, ``#``, ``?``, ``%``, Unicode, and Windows drive letters all need
    handling before the query string is appended. ``Path.as_uri`` delegates
    that job to the platform-aware pathlib implementation instead of relying
    on a partial hand-rolled replacement.

    The Windows branch uses ``PureWindowsPath`` deliberately. Besides being
    the path class used by pathlib on Windows, it keeps this conversion
    deterministic in tests that exercise Windows paths on a non-Windows host.
    """
    if os.name == "nt" or sys.platform.startswith("win"):
        absolute = ntpath.abspath(os.path.expanduser(db_path))
        path_uri = PureWindowsPath(absolute).as_uri()
    else:
        path_uri = Path(db_path).expanduser().resolve(strict=False).as_uri()
    return f"{path_uri}?mode=ro"


def _connect(db_path: str) -> sqlite3.Connection:
    """Open the database read-only via a SQLite URI (safe with an active WAL)."""
    uri = _sqlite_uri(db_path)
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
