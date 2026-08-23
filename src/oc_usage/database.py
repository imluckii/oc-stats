"""Fast, read-only access to local OpenCode session databases.

Usage model
-----------
Assistant messages are the per-model detail, but they are not a complete
billing ledger. Three storage semantics are reconciled here:

* Forks copy the parent transcript into the fork with fresh message ids and a
  zeroed usage ledger. Those rows are context, not new provider calls, and are
  excluded by comparing each copied row's creation time against the fork
  session's creation time (copies always predate the fork).
* Title generation, compaction, and requests removed by a committed revert are
  added to the session's aggregate usage without a retained assistant message.
  The positive difference between a session's ledger and its counted messages
  is reported as one ``(unattributed)`` internal-usage row.
* Mixed databases can hold both the current ``session_message`` projection and
  the legacy ``message`` table; both are read and deduplicated by id.

Deleted sessions are gone from the database (OpenCode cascades the delete), so
every total is "retained usage", not all-time usage.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from pathlib import Path

from oc_usage.models import UNKNOWN, SessionLedger, UsageRow, reconcile_ledger
from oc_usage.service import NoUsageDataError, ServiceSchemaError, _assistant_row

# Current projection table first; the legacy v1 ``session`` table in mixed
# databases carries stale copies of the same sessions.
SESSION_TABLES = ("session_v2", "session")

LEDGER_COLUMNS = frozenset(
    {
        "id",
        "fork_session_id",
        "tokens_input",
        "tokens_output",
        "tokens_reasoning",
        "tokens_cache_read",
        "tokens_cache_write",
        "cost",
        "time_created",
    }
)


def discover_databases() -> list[Path]:
    """Return every local OpenCode database, newest first.

    Matches ``opencode*.db`` directly under each data root so channel
    databases (``opencode-beta.db``) and custom ``OPENCODE_DB`` names are
    picked up alongside the standard ones. Backup files (``.db.bak``) and
    nested snapshot directories are never matched.
    """
    roots = []
    if xdg_data := os.environ.get("XDG_DATA_HOME"):
        roots.append(Path(xdg_data) / "opencode")
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        roots.append(Path(local_app_data) / "opencode")
    if user_profile := os.environ.get("USERPROFILE"):
        roots.append(Path(user_profile) / ".local" / "share" / "opencode")
    roots.append(Path.home() / ".local" / "share" / "opencode")

    databases = []
    for root in dict.fromkeys(roots):
        if not root.is_dir():
            continue
        databases.extend(path for path in root.glob("opencode*.db") if path.is_file())
    return sorted(dict.fromkeys(databases), key=lambda path: path.stat().st_mtime, reverse=True)


def discover_database() -> Path | None:
    """Compatibility helper returning the newest local OpenCode database."""
    databases = discover_databases()
    return databases[0] if databases else None


class DatabaseError(RuntimeError):
    """A discovered database could not be read using a known schema."""


def _as_int(value) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


def _as_cost(value) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return 0.0
    return max(0.0, float(value))


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {column[1] for column in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


class DatabaseClient:
    """Read-only client for one local OpenCode database."""

    def __init__(self, path: Path):
        self.path = path

    def rows(self) -> list[UsageRow]:
        return [row for _identity, _session_id, row in self.records()]

    def records(self, skip_ids: set[str] | None = None) -> list[tuple[str, str | None, UsageRow]]:
        """Yield ``(message identity, session id, row)`` for retained assistant turns.

        ``skip_ids`` hides messages a newer database already contributed.
        Fork-copied history is dropped (see module docstring). The current
        ``session_message`` projection and the legacy ``message`` table are
        both read when present, deduplicated by id.
        """
        try:
            connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        try:
            records: list[tuple[str, str | None, UsageRow]] = []
            seen: set[str] = set()
            fork_times = self._fork_times(connection)
            message_columns = _table_columns(connection, "session_message")
            if message_columns:
                for record in self._session_message_records(
                    connection, message_columns, skip_ids, fork_times
                ):
                    seen.add(record[0])
                    records.append(record)
            legacy_columns = _table_columns(connection, "message")
            if legacy_columns:
                records.extend(
                    record
                    for record in self._legacy_message_records(connection, legacy_columns, skip_ids)
                    if record[0] not in seen
                )
            if not message_columns and not legacy_columns:
                raise DatabaseError(f"unsupported OpenCode database schema: {self.path}")
            return records
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        finally:
            connection.close()

    def ledger(self) -> dict[str, SessionLedger]:
        """Per-session aggregate usage from the current session projection."""
        try:
            connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        try:
            for table in SESSION_TABLES:
                if not LEDGER_COLUMNS.issubset(_table_columns(connection, table)):
                    continue
                cursor = connection.execute(
                    "SELECT id, tokens_input, tokens_output, tokens_reasoning, "
                    "tokens_cache_read, tokens_cache_write, cost, time_created "
                    f"FROM {table}"  # noqa: S608
                )
                return {
                    str(session_id): SessionLedger(
                        input=_as_int(values[0]),
                        cache_read=_as_int(values[3]),
                        cache_write=_as_int(values[4]),
                        output=_as_int(values[1]),
                        reasoning=_as_int(values[2]),
                        cost=_as_cost(values[5]),
                        time_created=_as_int(values[6]),
                    )
                    for session_id, *values in cursor
                }
            return {}
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        finally:
            connection.close()

    # ── table readers ─────────────────────────────────────────────────────

    def _fork_times(self, connection: sqlite3.Connection) -> dict[str, int]:
        """Map fork session id → creation time, for copied-history exclusion."""
        for table in SESSION_TABLES:
            columns = _table_columns(connection, table)
            if not {"fork_session_id", "time_created"} <= columns:
                continue
            cursor = connection.execute(
                f"SELECT id, time_created FROM {table} WHERE fork_session_id IS NOT NULL"  # noqa: S608
            )
            return {str(session_id): _as_int(created) for session_id, created in cursor}
        return {}

    def _session_message_records(
        self,
        connection: sqlite3.Connection,
        columns: set[str],
        skip_ids: set[str] | None,
        fork_times: dict[str, int],
    ) -> list[tuple[str, str | None, UsageRow]]:
        identity = "id" if "id" in columns else "NULL"
        session_ref = "session_id" if "session_id" in columns else "NULL"
        exclusion = ""
        if identity == "id" and skip_ids:
            connection.execute(
                "CREATE TEMP TABLE IF NOT EXISTS excluded_message (id TEXT PRIMARY KEY)"
            )
            connection.executemany(
                "INSERT OR IGNORE INTO excluded_message (id) VALUES (?)",
                ((message_id,) for message_id in skip_ids),
            )
            exclusion = " AND id NOT IN (SELECT id FROM excluded_message)"

        if {"id", "session_id", "type", "data"} <= columns:
            cursor = connection.execute(
                "SELECT id, session_id, "
                "json_extract(data, '$.model.providerID'), "
                "json_extract(data, '$.model.id'), "
                "coalesce(json_extract(data, '$.model.variant'), ''), "
                "coalesce(json_extract(data, '$.tokens.input'), 0), "
                "coalesce(json_extract(data, '$.tokens.cache.read'), 0), "
                "coalesce(json_extract(data, '$.tokens.cache.write'), 0), "
                "coalesce(json_extract(data, '$.tokens.output'), 0), "
                "coalesce(json_extract(data, '$.tokens.reasoning'), 0), "
                "coalesce(json_extract(data, '$.cost'), 0), "
                "coalesce(json_extract(data, '$.time.created'), 0), "
                "json_type(data, '$.tokens') IS NOT NULL "
                f"FROM session_message WHERE type = 'assistant' AND json_valid(data){exclusion}"  # noqa: S608
            )
            records = []
            for values in cursor:
                row = _v2_values_row(values[2:])
                if row is None:
                    continue
                if _is_fork_copy(fork_times, values[1], row.time_created):
                    continue
                records.append((str(values[0]), values[1], row))
            return records

        query = (
            f"SELECT {identity}, {session_ref}, data FROM session_message "  # noqa: S608
            f"WHERE type = 'assistant'{exclusion}"
        )
        cursor = connection.execute(query)
        records = []
        for message_id, session_id, raw in cursor:
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                row = _assistant_row(message)
            except (json.JSONDecodeError, TypeError, ServiceSchemaError):
                continue
            if _is_fork_copy(fork_times, session_id, row.time_created):
                continue
            records.append((_stable_id(message_id, raw), session_id, row))
        return records

    def _legacy_message_records(
        self,
        connection: sqlite3.Connection,
        columns: set[str],
        skip_ids: set[str] | None,
    ) -> list[tuple[str, str | None, UsageRow]]:
        identity = "id" if "id" in columns else "NULL"
        exclusion = ""
        if identity == "id" and skip_ids:
            connection.execute(
                "CREATE TEMP TABLE IF NOT EXISTS excluded_message (id TEXT PRIMARY KEY)"
            )
            connection.executemany(
                "INSERT OR IGNORE INTO excluded_message (id) VALUES (?)",
                ((message_id,) for message_id in skip_ids),
            )
            exclusion = " WHERE id NOT IN (SELECT id FROM excluded_message)"
        cursor = connection.execute(
            f"SELECT {identity}, data FROM message{exclusion}"  # noqa: S608
        )
        records = []
        for message_id, raw in cursor:
            stable_id = _stable_id(message_id, raw)
            if stable_id in (skip_ids or ()):
                continue
            try:
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                records.append((stable_id, None, _v1_row(message)))
            except (json.JSONDecodeError, TypeError):
                continue
        return records


def _stable_id(message_id, raw: str) -> str:
    if message_id is not None:
        return str(message_id)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_fork_copy(fork_times: dict[str, int], session_id, time_created: int) -> bool:
    """True for history a fork copied from its parent (it predates the fork)."""
    if session_id is None or time_created <= 0:
        return False
    started = fork_times.get(str(session_id))
    return started is not None and started > 0 and time_created < started


def load_databases(
    paths: list[Path], *, skip_errors: bool = False
) -> tuple[list[UsageRow], list[Path]]:
    """Merge databases, deduplicate messages, and reconcile session ledgers.

    Messages shared by migrations deduplicate by id (the newest database
    wins). After the merge, each session's ledger is compared against its
    counted messages and any positive difference — title generation,
    compaction, or reverted requests — is added back as an
    ``(unattributed)`` internal-usage row so totals match OpenCode's ledger.
    """
    rows: list[UsageRow] = []
    used: list[Path] = []
    seen: set[str] = set()
    kept: dict[str, list[float]] = {}
    ledgers: dict[str, SessionLedger] = {}
    for path in paths:
        try:
            client = DatabaseClient(path)
            records = client.records(seen)
            sessions = client.ledger()
        except DatabaseError:
            if skip_errors:
                continue
            raise
        used.append(path)
        for identity, session_id, row in records:
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
            if session_id is not None:
                totals = kept.setdefault(str(session_id), [0.0] * 6)
                totals[0] += row.input
                totals[1] += row.cache_read
                totals[2] += row.cache_write
                totals[3] += row.output
                totals[4] += row.reasoning
                totals[5] += row.cost
        for session_id, ledger in sessions.items():
            ledgers.setdefault(session_id, ledger)
    for session_id in sorted(ledgers):
        row = reconcile_ledger(tuple(kept.get(session_id, (0.0,) * 6)), ledgers[session_id])
        if row is not None:
            rows.append(row)
    if not rows and not skip_errors:
        # Explicit databases that produced nothing are an error; discovered
        # ones simply hand control back to the service fallback in the CLI.
        raise NoUsageDataError("No assistant messages found. Run a session in OpenCode and retry.")
    return rows, used


def _v2_values_row(values) -> UsageRow | None:
    (
        provider,
        model,
        variant,
        input_,
        cache_read,
        cache_write,
        output,
        reasoning,
        cost,
        created,
        tokens_known,
    ) = values
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        return None
    return UsageRow(
        provider=provider,
        model=model,
        variant=str(variant or ""),
        input=_as_int(input_),
        cache_read=_as_int(cache_read),
        cache_write=_as_int(cache_write),
        output=_as_int(output),
        reasoning=_as_int(reasoning),
        cost=_as_cost(cost),
        time_created=_as_int(created),
        tokens_known=bool(tokens_known),
    )


def _v1_row(message: dict) -> UsageRow:
    tokens = message.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, dict) else {}

    model = message.get("modelID") or message.get("model") or UNKNOWN
    if isinstance(model, dict):
        model = model.get("id") or UNKNOWN
    provider = message.get("providerID") or message.get("provider") or UNKNOWN
    variant = message.get("variant") or ""
    time = message.get("time")
    created = _as_int(time.get("created")) if isinstance(time, dict) else 0

    return UsageRow(
        provider=str(provider),
        model=str(model),
        variant=str(variant),
        input=_as_int(tokens.get("input")),
        cache_read=_as_int(cache.get("read")),
        cache_write=_as_int(cache.get("write")),
        output=_as_int(tokens.get("output")),
        reasoning=_as_int(tokens.get("reasoning")),
        cost=_as_cost(message.get("cost", 0.0)),
        time_created=created,
        tokens_known=isinstance(message.get("tokens"), dict),
    )
