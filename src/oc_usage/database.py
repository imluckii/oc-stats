"""Fast, read-only access to a local OpenCode V2 session database."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from oc_usage.models import UsageRow
from oc_usage.service import NoUsageDataError, ServiceSchemaError, _assistant_row


def discover_database() -> Path | None:
    """Return the first local OpenCode database in the platform data directory."""
    roots = []
    if xdg_data := os.environ.get("XDG_DATA_HOME"):
        roots.append(Path(xdg_data) / "opencode")
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        roots.append(Path(local_app_data) / "opencode")
    roots.append(Path.home() / ".local" / "share" / "opencode")

    for root in dict.fromkeys(roots):
        for name in ("opencode-next.db", "opencode.db"):
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
        try:
            cursor = connection.execute("SELECT data FROM session_message WHERE type = 'assistant'")
        except sqlite3.Error as exc:
            connection.close()
            raise DatabaseError(f"could not read {self.path}") from exc

        found = False
        try:
            for (raw,) in cursor:
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        continue
                    row = _assistant_row(message)
                except (json.JSONDecodeError, TypeError, ServiceSchemaError):
                    continue
                found = True
                yield row
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not read {self.path}") from exc
        finally:
            connection.close()

        if not found:
            raise NoUsageDataError(
                "No assistant messages found. Run a session in OpenCode and retry."
            )
