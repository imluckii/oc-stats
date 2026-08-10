"""Fast, read-only access to a local OpenCode V2 session database."""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from oc_usage.models import UNKNOWN, UsageRow
from oc_usage.service import NoUsageDataError, ServiceSchemaError, _assistant_row


def discover_database() -> Path | None:
    """Return the first local OpenCode database in the platform data directory."""
    roots = []
    if xdg_data := os.environ.get("XDG_DATA_HOME"):
        roots.append(Path(xdg_data) / "opencode")
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        roots.append(Path(local_app_data) / "opencode")
    if user_profile := os.environ.get("USERPROFILE"):
        roots.append(Path(user_profile) / ".local" / "share" / "opencode")
    roots.append(Path.home() / ".local" / "share" / "opencode")

    for root in dict.fromkeys(roots):
        for name in ("opencode-next.db", "opencode.db", "opencode-dev.db", "opencode-local.db"):
            path = root / name
            if path.is_file():
                return path
    return None


class DatabaseError(RuntimeError):
    """A discovered database could not be read using the V2 schema."""


class DatabaseClient:
    def __init__(self, path: Path):
        self.path = path

    def rows(self) -> Iterator[UsageRow]:
        try:
            connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        found = False
        try:
            queries = (
                ("v2", "SELECT data FROM session_message WHERE type = 'assistant'"),
                ("v1", "SELECT data FROM message"),
            )
            known_schema = False
            for schema, query in queries:
                try:
                    cursor = connection.execute(query)
                    known_schema = True
                except sqlite3.Error:
                    continue
                for (raw,) in cursor:
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
                    yield row
                if found:
                    break
            if not known_schema:
                raise DatabaseError(f"unsupported OpenCode database schema: {self.path}")
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        finally:
            connection.close()

        if not found:
            raise NoUsageDataError(
                "No assistant messages found. Run a session in OpenCode and retry."
            )


def _as_int(value) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


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
