"""Fast, read-only access to a local OpenCode V2 session database."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from oc_usage.models import UNKNOWN, UsageRow
from oc_usage.service import NoUsageDataError, ServiceSchemaError, _assistant_row

DATABASE_NAMES = ("opencode-next.db", "opencode.db", "opencode-dev.db", "opencode-local.db")


def discover_databases() -> list[Path]:
    """Return every local OpenCode database, newest first."""
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
        for name in DATABASE_NAMES:
            path = root / name
            if path.is_file():
                databases.append(path)
    return sorted(dict.fromkeys(databases), key=lambda path: path.stat().st_mtime, reverse=True)


def discover_database() -> Path | None:
    """Compatibility helper returning the newest local OpenCode database."""
    databases = discover_databases()
    return databases[0] if databases else None


class DatabaseError(RuntimeError):
    """A discovered database could not be read using the V2 schema."""


class DatabaseClient:
    def __init__(self, path: Path):
        self.path = path

    def rows(self) -> Iterator[UsageRow]:
        for _identity, row in self.records():
            yield row

    def records(self, skip_ids: set[str] | None = None) -> Iterator[tuple[str, UsageRow]]:
        """Yield stable message identities with normalized usage rows."""
        try:
            connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        found = False
        try:
            queries = (
                ("v2", "session_message", "type = 'assistant'"),
                ("v1", "message", "1"),
            )
            known_schema = False
            for schema, table, where in queries:
                try:
                    columns = {
                        column[1] for column in connection.execute(f"PRAGMA table_info({table})")
                    }
                    if not columns:
                        continue
                    identity = "id" if "id" in columns else "NULL"
                    exclusion = ""
                    if identity == "id" and skip_ids:
                        connection.execute(
                            "CREATE TEMP TABLE IF NOT EXISTS excluded_message (id TEXT PRIMARY KEY)"
                        )
                        connection.executemany(
                            "INSERT INTO excluded_message (id) VALUES (?)",
                            ((message_id,) for message_id in skip_ids),
                        )
                        exclusion = " AND id NOT IN (SELECT id FROM excluded_message)"
                    if schema == "v2" and identity == "id":
                        cursor = connection.execute(
                            "SELECT id, json_extract(data, '$.model.providerID'), "
                            "json_extract(data, '$.model.id'), "
                            "coalesce(json_extract(data, '$.model.variant'), ''), "
                            "coalesce(json_extract(data, '$.tokens.input'), 0), "
                            "coalesce(json_extract(data, '$.tokens.cache.read'), 0), "
                            "coalesce(json_extract(data, '$.tokens.cache.write'), 0), "
                            "coalesce(json_extract(data, '$.tokens.output'), 0), "
                            "coalesce(json_extract(data, '$.tokens.reasoning'), 0), "
                            "coalesce(json_extract(data, '$.cost'), 0), "
                            "coalesce(json_extract(data, '$.time.created'), 0) "
                            f"FROM {table} WHERE {where} AND json_valid(data){exclusion}"  # noqa: S608
                        )
                        known_schema = True
                        for values in cursor:
                            row = _v2_values_row(values[1:])
                            if row is None:
                                continue
                            found = True
                            yield str(values[0]), row
                        if found:
                            break
                        continue
                    cursor = connection.execute(
                        f"SELECT {identity}, data FROM {table} WHERE {where}{exclusion}"  # noqa: S608
                    )
                    known_schema = True
                except sqlite3.Error:
                    continue
                for message_id, raw in cursor:
                    try:
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            continue
                        if schema == "v1":
                            if message.get("role") != "assistant":
                                continue
                            row = _v1_row(message)
                        else:
                            row = _assistant_row(message)
                    except (json.JSONDecodeError, TypeError, ServiceSchemaError):
                        continue
                    found = True
                    stable_id = (
                        str(message_id)
                        if message_id is not None
                        else hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    )
                    if stable_id in (skip_ids or ()):
                        continue
                    yield stable_id, row
                if found:
                    break
            if not known_schema:
                raise DatabaseError(f"unsupported OpenCode database schema: {self.path}")
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        finally:
            connection.close()

        if not found and not skip_ids:
            raise NoUsageDataError(
                "No assistant messages found. Run a session in OpenCode and retry."
            )


def load_databases(
    paths: list[Path], *, skip_errors: bool = False
) -> tuple[list[UsageRow], list[Path]]:
    """Merge databases while deduplicating messages shared by migrations."""
    rows = []
    used = []
    seen = set()
    for path in paths:
        try:
            records = list(DatabaseClient(path).records(seen))
        except (DatabaseError, NoUsageDataError):
            if skip_errors:
                continue
            raise
        used.append(path)
        for identity, row in records:
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
    return rows, used


def _as_int(value) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


def _v2_values_row(values) -> UsageRow | None:
    provider, model, variant, input_, cache_read, cache_write, output, reasoning, cost, created = (
        values
    )
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        return None
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost):
        cost = 0.0
    return UsageRow(
        provider=provider,
        model=model,
        variant=str(variant or ""),
        input=_as_int(input_),
        cache_read=_as_int(cache_read),
        cache_write=_as_int(cache_write),
        output=_as_int(output),
        reasoning=_as_int(reasoning),
        cost=max(0.0, float(cost)),
        time_created=_as_int(created),
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
    cost = message.get("cost", 0.0)
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost):
        cost = 0.0

    return UsageRow(
        provider=str(provider),
        model=str(model),
        variant=str(variant),
        input=_as_int(tokens.get("input")),
        cache_read=_as_int(cache.get("read")),
        cache_write=_as_int(cache.get("write")),
        output=_as_int(tokens.get("output")),
        reasoning=_as_int(tokens.get("reasoning")),
        cost=max(0.0, float(cost)),
        time_created=created,
    )
