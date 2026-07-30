"""Read usage from an OpenCode V2 HTTP server.

The implementation intentionally uses only the Python standard library.  The
V2 OpenAPI contract exposes paginated ``GET /api/session`` and
``GET /api/session/{sessionID}/message`` endpoints.  Session aggregates are
not used: assistant messages are fetched and normalized one by one so model,
provider, variant, token components, and recorded cost retain their per-turn
semantics.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from oc_usage.models import UsageRow

DEFAULT_TIMEOUT = 10.0
PAGE_LIMIT = 100


class RemoteError(RuntimeError):
    """Base class for errors reading a remote OpenCode server."""


class RemoteAuthError(RemoteError):
    """The server rejected the supplied (or absent) Basic credentials."""


class RemoteConnectionError(RemoteError):
    """The server could not be reached or timed out."""


class RemoteSchemaError(RemoteError):
    """The server response does not match the current V2 API contract."""


class RemoteNoUsageDataError(RemoteError):
    """The server was reachable but contained no assistant messages."""


@dataclass(frozen=True)
class ServerURL:
    """Validated server URL with credentials and fragments removed."""

    value: str

    @classmethod
    def parse(cls, value: str) -> ServerURL:
        if not value:
            raise RemoteError("server URL must not be empty")
        try:
            parts = urllib.parse.urlsplit(value)
        except ValueError as exc:
            raise RemoteError("invalid server URL") from exc
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise RemoteError("server URL must include an http:// or https:// scheme and host")
        if parts.username is not None or parts.password is not None:
            raise RemoteError("credentials in --server URLs are not allowed; use password options")
        if parts.query or parts.fragment:
            raise RemoteError("server URL must not include a query string or fragment")
        # Keep a possible path prefix for reverse proxies, but never retain a
        # trailing slash that would create an accidental empty path component.
        path = parts.path.rstrip("/")
        return cls(urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", "")))


@dataclass
class RemoteClient:
    """Small read-only client for the V2 session/message API."""

    server: ServerURL | str
    username: str = "opencode"
    password: str | None = None
    timeout: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        if isinstance(self.server, str):
            self.server = ServerURL.parse(self.server)
        if ":" in self.username:
            raise RemoteError("username must not contain ':' for HTTP Basic authentication")

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        base = urllib.parse.urlsplit(self.server.value)
        endpoint = f"{base.path.rstrip('/')}/{path.lstrip('/')}"
        encoded_query = urllib.parse.urlencode(query or {})
        return urllib.parse.urlunsplit((base.scheme, base.netloc, endpoint, encoded_query, ""))

    def _request_json(self, path: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        url = self._url(path, query)
        headers = {
            "Accept": "application/json",
            "User-Agent": "oc-usage/" + _package_version(),
        }
        if self.password is not None:
            credentials = f"{self.username}:{self.password}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode("ascii")
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise RemoteAuthError(
                    f"server returned HTTP {exc.code} (authentication or permission denied)"
                ) from None
            raise RemoteError(f"server returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            detail = reason if isinstance(reason, str) else str(exc)
            raise RemoteConnectionError(f"could not connect to server: {detail}") from None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteSchemaError(f"server returned invalid JSON for {path}") from exc
        if not isinstance(payload, dict):
            raise RemoteSchemaError(f"server response for {path} must be a JSON object")
        return payload

    def list_sessions(self) -> Iterator[dict[str, Any]]:
        """Yield all sessions using the contract's opaque ``cursor.next``."""
        cursor: str | None = None
        first_page = True
        while True:
            query = {"limit": str(PAGE_LIMIT)}
            if first_page:
                query["order"] = "asc"
            elif cursor is not None:
                query = {"cursor": cursor}
            payload = self._request_json("/api/session", query)
            data, next_cursor = _paged_data(payload, "/api/session")
            for session in data:
                if not isinstance(session, dict) or not isinstance(session.get("id"), str):
                    raise RemoteSchemaError("server returned a session without a string id")
                yield session
            if next_cursor is None:
                return
            if next_cursor == cursor:
                raise RemoteSchemaError("server returned a repeating session pagination cursor")
            cursor = next_cursor
            first_page = False

    def list_messages(self, session_id: str) -> Iterator[dict[str, Any]]:
        """Yield all projected messages for one session, in ascending order."""
        escaped_id = urllib.parse.quote(session_id, safe="")
        path = f"/api/session/{escaped_id}/message"
        cursor: str | None = None
        first_page = True
        seen_ids: set[str] = set()
        while True:
            query = {"limit": str(PAGE_LIMIT)}
            if first_page:
                query["order"] = "asc"
            elif cursor is not None:
                query = {"cursor": cursor}
            payload = self._request_json(path, query)
            data, next_cursor = _paged_data(payload, path)
            for message in data:
                if not isinstance(message, dict):
                    raise RemoteSchemaError(f"server returned a non-object message for {path}")
                message_id = message.get("id")
                if not isinstance(message_id, str):
                    raise RemoteSchemaError(
                        f"server returned a message without a string id for {path}"
                    )
                # The API promises ordered, non-overlapping pages.  Protect
                # against a broken proxy repeating a page without ever
                # silently double-counting a stable message identity.
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
                yield message
            if next_cursor is None:
                return
            if next_cursor == cursor:
                raise RemoteSchemaError(f"server returned a repeating message cursor for {path}")
            cursor = next_cursor
            first_page = False

    def rows(self) -> Iterator[UsageRow]:
        """Yield normalized assistant-message rows from exactly this server."""
        found_assistant = False
        for session in self.list_sessions():
            session_id = session["id"]
            for message in self.list_messages(session_id):
                if message.get("type") != "assistant":
                    continue
                found_assistant = True
                yield _assistant_row(message)
        if not found_assistant:
            raise RemoteNoUsageDataError("server returned no assistant messages")


def _paged_data(payload: dict[str, Any], path: str) -> tuple[list[Any], str | None]:
    """Validate the shared V2 paginated response shape."""
    data = payload.get("data")
    cursor = payload.get("cursor")
    if not isinstance(data, list) or not isinstance(cursor, dict):
        raise RemoteSchemaError(
            f"server response for {path} must contain array data and cursor object"
        )
    next_cursor = cursor.get("next")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise RemoteSchemaError(f"server response for {path} has an invalid cursor.next")
    return data, next_cursor


def _assistant_row(message: dict[str, Any]) -> UsageRow:
    """Normalize the V2 ``Session.Message.Assistant`` contract."""
    model = message.get("model")
    if not isinstance(model, dict):
        raise RemoteSchemaError("assistant message has no model object")
    model_id = _required_string(model, "id", "assistant model")
    provider = _required_string(model, "providerID", "assistant model")
    variant = model.get("variant", "")
    if variant is None:
        variant = ""
    if not isinstance(variant, str):
        raise RemoteSchemaError("assistant model variant must be a string")

    tokens = message.get("tokens")
    if tokens is None:
        token_values = dict.fromkeys(
            ("input", "output", "reasoning", "cache_read", "cache_write"), 0
        )
    elif isinstance(tokens, dict):
        cache = tokens.get("cache")
        if not isinstance(cache, dict):
            raise RemoteSchemaError("assistant tokens.cache must be an object")
        token_values = {
            "input": _number_as_int(tokens, "input", "assistant tokens"),
            "output": _number_as_int(tokens, "output", "assistant tokens"),
            "reasoning": _number_as_int(tokens, "reasoning", "assistant tokens"),
            "cache_read": _number_as_int(cache, "read", "assistant tokens.cache"),
            "cache_write": _number_as_int(cache, "write", "assistant tokens.cache"),
        }
    else:
        raise RemoteSchemaError("assistant tokens must be an object")

    time = message.get("time")
    if not isinstance(time, dict):
        raise RemoteSchemaError("assistant message has no time object")
    created = _number_as_int(time, "created", "assistant time")
    cost = message.get("cost", 0.0)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        raise RemoteSchemaError("assistant cost must be a number")
    return UsageRow(
        provider=provider,
        model=model_id,
        variant=variant,
        input=token_values["input"],
        cache_read=token_values["cache_read"],
        cache_write=token_values["cache_write"],
        output=token_values["output"],
        reasoning=token_values["reasoning"],
        cost=float(cost),
        time_created=created,
    )


def _required_string(value: dict[str, Any], key: str, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise RemoteSchemaError(f"{label} has no non-empty {key}")
    return result


def _number_as_int(value: dict[str, Any], key: str, label: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise RemoteSchemaError(f"{label}.{key} must be a number")
    return int(result)


def _package_version() -> str:
    # Avoid importing the package version at module import time in tests that
    # monkeypatch package metadata; the value is not security-sensitive.
    try:
        from oc_usage import __version__

        return __version__
    except Exception:  # pragma: no cover - defensive fallback
        return "0.0.0"
