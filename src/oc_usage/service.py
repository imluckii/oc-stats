"""Read usage from the running OpenCode V2 service via its ``api`` CLI command.

``oc-stats`` never opens databases or raw HTTP sockets. It discovers the
OpenCode executable on ``PATH`` (preferring ``opencode2``, then ``opencode``)
and invokes its ``api get <path>`` subcommand, so OpenCode owns service
discovery, startup, and authentication and always inherits the active server
context.

Authoritative contract (verified against the running service's OpenAPI spec):
    GET /api/session                        -> {"data":[session...], "cursor":{...}}
    GET /api/session/{sessionID}/message    -> {"data":[message...], "cursor":{...}}

Both endpoints accept ``order`` (only on the first page), ``limit``, and an
opaque ``cursor`` returned as ``cursor.next`` (``cursor`` must not be combined
with ``order``). We request ascending order and walk ``cursor.next`` until it is
absent. Only ``type == "assistant"`` messages are counted; each carries its own
``model`` / ``tokens`` / ``cost`` / ``time`` record, so sessions that switch
model, provider, or variant mid-stream aggregate exactly.

Subprocess invocation always uses an argv list (never ``shell=True``).
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from oc_usage.models import UsageRow

# Executable lookup order. OpenCode V2 ships as ``opencode2``; ``opencode`` is a
# secondary fallback (it may be a V2 install or a legacy V1 binary, which the
# compatibility probe rejects).
EXECUTABLE_CANDIDATES = ("opencode2", "opencode")

# Per-request page size, sent on every page (including cursor pages, which the
# spec permits combining with ``limit``). Kept small deliberately: the ``api``
# command can intermittently truncate responses of roughly ~130KB and up, so a
# small page size keeps every response well under that threshold. Retries
# (below) cover the rare case that still truncates.
PAGE_LIMIT = 2

# Upper bound on a single ``api`` subprocess call so a hung service cannot stall
# the report forever.
REQUEST_TIMEOUT = 60.0

# The ``api`` command can intermittently truncate a page. Each request is an
# idempotent GET, so a JSON-decode failure is retried several times before the
# report gives up.
JSON_RETRIES = 5
RETRY_DELAY = 0.2


class ServiceError(RuntimeError):
    """Base class for errors reading the running OpenCode service."""


class ExecutableNotFoundError(ServiceError):
    """No OpenCode executable was found on PATH."""


class IncompatibleExecutableError(ServiceError):
    """An executable was found but it does not speak the V2 ``api`` command."""


class ServiceUnavailableError(ServiceError):
    """The service could not be reached or the CLI exited with an error."""


class ServiceSchemaError(ServiceError):
    """The service response was invalid JSON or did not match the V2 shape."""


class NoUsageDataError(ServiceError):
    """The service was reachable but returned no assistant messages."""


@dataclass(frozen=True)
class ProcResult:
    """Minimal captured-subprocess result used by the service transport."""

    returncode: int
    stdout: str
    stderr: str


def default_runner(argv: list[str]) -> ProcResult:
    """Run ``argv`` (no shell) and capture UTF-8 text output.

    The stdout/stderr are decoded with ``errors="replace"`` so a stray byte in
    an unrelated field can never crash JSON parsing of the values we need.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, never shell=True
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=REQUEST_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        # The executable vanished between discovery and use, or PATH lookup was
        # bypassed with a bad explicit path.
        raise ExecutableNotFoundError(
            "OpenCode V2 was not found. Install OpenCode V2 and ensure "
            "`opencode2` (or `opencode`) is on PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceUnavailableError(
            "OpenCode service request timed out. Start or update OpenCode V2, then retry."
        ) from exc
    return ProcResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def discover_executable() -> str | None:
    """Return the preferred OpenCode executable on PATH, or ``None``."""
    for name in EXECUTABLE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


@dataclass
class ServiceClient:
    """Read-only client for the running OpenCode V2 service.

    ``executable`` and ``runner`` are injectable for testing. When omitted, the
    executable is discovered on PATH and :func:`default_runner` is used.
    """

    executable: str | None = None
    page_limit: int = PAGE_LIMIT
    runner: Callable[[list[str]], ProcResult] = default_runner
    json_retries: int = JSON_RETRIES
    retry_delay: float = RETRY_DELAY
    _compatibility_checked: bool = False

    def __post_init__(self) -> None:
        if self.executable is None:
            self.executable = discover_executable()

    # ── executable resolution & compatibility ────────────────────────────────

    def _resolve(self) -> str:
        if self.executable is None:
            raise ExecutableNotFoundError(
                "OpenCode V2 was not found. Install OpenCode V2 and ensure "
                "`opencode2` (or `opencode`) is on PATH."
            )
        return self.executable

    def _ensure_compatible(self) -> None:
        """Probe once that the binary exposes the V2 ``api`` command.

        A V2 binary prints its ``api`` subcommand help to stdout and exits 0.
        A legacy V1 ``opencode`` binary has no ``api`` subcommand: it dumps its
        main banner/help to stderr with an empty stdout. We therefore treat
        non-empty stdout as the compatibility signal so an installed V1 binary
        is never silently used.
        """
        if self._compatibility_checked:
            return
        exe = self._resolve()
        result = self.runner([exe, "api", "--help"])
        if result.returncode != 0 or not result.stdout.strip():
            raise IncompatibleExecutableError(
                f"`{exe}` was found but does not provide the OpenCode V2 `api` "
                "command. Install or upgrade to OpenCode V2 (`opencode2`)."
            )
        self._compatibility_checked = True

    # ── low-level request ────────────────────────────────────────────────────

    def _get(self, path: str) -> dict[str, Any]:
        exe = self._resolve()
        last_exc: Exception | None = None
        for attempt in range(self.json_retries + 1):
            result = self.runner([exe, "api", "get", path])
            if result.returncode != 0:
                # The CLI prints human-readable diagnostics (often a node stack
                # trace) on a nonzero exit. Those can embed internal socket paths,
                # so they are intentionally not echoed back to the user.
                raise ServiceUnavailableError(
                    "OpenCode service is unavailable. Start or update OpenCode V2, then retry."
                )
            try:
                payload = json.loads(result.stdout)
                break
            except (ValueError, json.JSONDecodeError) as exc:
                # The ``api`` command can intermittently truncate a very large
                # page. Each request is an idempotent GET, so retry before
                # declaring the schema incompatible.
                last_exc = exc
                if attempt < self.json_retries:
                    time.sleep(self.retry_delay)
                    continue
                raise ServiceSchemaError(
                    "OpenCode service returned invalid JSON; the running OpenCode "
                    "may be too old. Upgrade to the latest OpenCode V2."
                ) from exc
        else:  # pragma: no cover — the loop always breaks or raises above
            raise ServiceSchemaError("OpenCode service returned invalid JSON.") from last_exc

        if not isinstance(payload, dict):
            raise ServiceSchemaError(
                "OpenCode service returned a non-object response; the running "
                "OpenCode may be too old. Upgrade to the latest OpenCode V2."
            )
        tag = payload.get("_tag")
        if isinstance(tag, str):
            # API-level error envelope (e.g. {"_tag":"...","message":"..."}).
            message = _safe_error_message(payload.get("message"))
            raise ServiceSchemaError(
                f"OpenCode service reported an error: {message}"
                if message
                else "OpenCode service reported an error; check OpenCode is up to date."
            )
        return payload

    # ── pagination ───────────────────────────────────────────────────────────

    def _page_path(self, path: str, *, first: bool, cursor: str | None) -> str:
        # ``limit`` is sent on every page (the spec allows ``cursor``+``limit``;
        # only ``cursor``+``order`` is forbidden) so each response stays modest.
        if first:
            params = {"order": "asc", "limit": str(self.page_limit)}
        else:
            params = {"cursor": cursor, "limit": str(self.page_limit)}
        return f"{path}?{urllib.parse.urlencode(params)}"

    def list_sessions(self) -> Iterator[dict[str, Any]]:
        """Yield every session using the contract's opaque ``cursor.next``."""
        cursor: str | None = None
        first = True
        while True:
            path = self._page_path("/api/session", first=first, cursor=cursor)
            payload = self._get(path)
            data, next_cursor = _paged(payload, "/api/session")
            for session in data:
                if not isinstance(session, dict) or not isinstance(session.get("id"), str):
                    raise ServiceSchemaError(
                        "OpenCode service returned a session without a string id."
                    )
                yield session
            if next_cursor is None:
                return
            if next_cursor == cursor:
                raise ServiceSchemaError("OpenCode service returned a repeating session cursor.")
            cursor = next_cursor
            first = False

    def list_messages(self, session_id: str) -> Iterator[dict[str, Any]]:
        """Yield every message for one session, in ascending order."""
        escaped_id = urllib.parse.quote(session_id, safe="")
        base = f"/api/session/{escaped_id}/message"
        cursor: str | None = None
        first = True
        seen: set[str] = set()
        while True:
            path = self._page_path(base, first=first, cursor=cursor)
            payload = self._get(path)
            data, next_cursor = _paged(payload, base)
            for message in data:
                if not isinstance(message, dict):
                    raise ServiceSchemaError(
                        f"OpenCode service returned a non-object message for {base}."
                    )
                message_id = message.get("id")
                if not isinstance(message_id, str):
                    raise ServiceSchemaError(
                        f"OpenCode service returned a message without a string id for {base}."
                    )
                # The API promises ordered, non-overlapping pages.  Defensively
                # skip a stable message identity a broken proxy might replay, so
                # a duplicated page can never silently inflate the totals.
                if message_id in seen:
                    continue
                seen.add(message_id)
                yield message
            if next_cursor is None:
                return
            if next_cursor == cursor:
                raise ServiceSchemaError(
                    f"OpenCode service returned a repeating message cursor for {base}."
                )
            cursor = next_cursor
            first = False

    # ── row production ───────────────────────────────────────────────────────

    def rows(self) -> Iterator[UsageRow]:
        """Yield normalized assistant-message rows from the running service."""
        self._ensure_compatible()
        found_assistant = False
        for session in self.list_sessions():
            session_id = session["id"]
            for message in self.list_messages(session_id):
                if message.get("type") != "assistant":
                    continue
                found_assistant = True
                yield _assistant_row(message)
        if not found_assistant:
            raise NoUsageDataError(
                "No assistant messages found. Run a session in OpenCode and retry."
            )


# ── response/message helpers ──────────────────────────────────────────────────


def _paged(payload: dict[str, Any], path: str) -> tuple[list[Any], str | None]:
    """Validate the shared paginated response shape and return (data, next)."""
    data = payload.get("data")
    cursor = payload.get("cursor")
    if not isinstance(data, list) or not isinstance(cursor, dict):
        raise ServiceSchemaError(
            f"OpenCode service response for {path} did not match the V2 API shape "
            "(expected a JSON object with array `data` and `cursor`); the running "
            "OpenCode may be too old. Upgrade to the latest OpenCode V2."
        )
    next_cursor = cursor.get("next")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise ServiceSchemaError(
            f"OpenCode service response for {path} has an invalid cursor.next."
        )
    return data, next_cursor


def _assistant_row(message: dict[str, Any]) -> UsageRow:
    """Normalize the V2 ``Session.Message.Assistant`` contract to a UsageRow."""
    model = message.get("model")
    if not isinstance(model, dict):
        raise ServiceSchemaError("OpenCode assistant message has no model object.")
    model_id = _required_string(model, "id", "assistant model")
    provider = _required_string(model, "providerID", "assistant model")
    variant = model.get("variant", "")
    if variant is None:
        variant = ""
    if not isinstance(variant, str):
        raise ServiceSchemaError("OpenCode assistant model variant must be a string.")

    tokens = message.get("tokens")
    if tokens is None:
        # An assistant message may be recorded before token accounting exists.
        token_values = dict.fromkeys(
            ("input", "output", "reasoning", "cache_read", "cache_write"), 0
        )
    elif isinstance(tokens, dict):
        cache = tokens.get("cache")
        if not isinstance(cache, dict):
            raise ServiceSchemaError("OpenCode assistant tokens.cache must be an object.")
        token_values = {
            "input": _number_as_int(tokens, "input", "assistant tokens"),
            "output": _number_as_int(tokens, "output", "assistant tokens"),
            "reasoning": _number_as_int(tokens, "reasoning", "assistant tokens"),
            "cache_read": _number_as_int(cache, "read", "assistant tokens.cache"),
            "cache_write": _number_as_int(cache, "write", "assistant tokens.cache"),
        }
    else:
        raise ServiceSchemaError("OpenCode assistant tokens must be an object.")

    time = message.get("time")
    if not isinstance(time, dict):
        raise ServiceSchemaError("OpenCode assistant message has no time object.")
    created = _number_as_int(time, "created", "assistant time")

    cost = message.get("cost", 0.0)
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(cost)
        or cost < 0
    ):
        raise ServiceSchemaError("OpenCode assistant cost must be a finite non-negative number.")

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
        raise ServiceSchemaError(f"OpenCode {label} has no non-empty {key}.")
    return result


def _number_as_int(value: dict[str, Any], key: str, label: str) -> int:
    result = value.get(key)
    if (
        isinstance(result, bool)
        or not isinstance(result, (int, float))
        or not math.isfinite(result)
        or result < 0
        or result != int(result)
    ):
        raise ServiceSchemaError(f"OpenCode {label}.{key} must be a finite non-negative integer.")
    return int(result)


def _safe_error_message(value: Any) -> str:
    """Reduce a server error message to a short, single-line, safe string."""
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())
    return cleaned[:200]
