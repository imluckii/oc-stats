"""Builders and a fake OpenCode ``api`` service for tests.

Everything here is **synthetic** and deterministic. The fake service mimics the
exact V2 ``api get`` CLI/pagination contract so the transport can be tested
without touching real OpenCode history, sockets, or credentials.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterable
from typing import Any

from oc_usage.service import ProcResult

# A flat, human-friendly assistant-message spec:
#   (provider, model, variant, input, cache_read, cache_write,
#    output, reasoning, cost, created_ms)
RowSpec = tuple[str, str, str, int, int, int, int, int, float, int]

# Baseline ms timestamps (mid-2025) for deterministic span checks.
T0 = 1_750_000_000_000


def assistant_message(message_id: str, spec: RowSpec) -> dict[str, Any]:
    provider, model, variant, inp, cr, cw, out, reas, cost, created = spec
    return {
        "id": message_id,
        "type": "assistant",
        "time": {"created": created},
        "model": {"id": model, "providerID": provider, "variant": variant},
        "tokens": {
            "input": inp,
            "output": out,
            "reasoning": reas,
            "cache": {"read": cr, "write": cw},
        },
        "cost": cost,
    }


def user_message(message_id: str, *, created: int = T0) -> dict[str, Any]:
    """A non-assistant message that must be ignored during aggregation."""
    return {"id": message_id, "type": "user", "time": {"created": created}, "cost": None}


def model_switched_message(
    message_id: str, model: str, provider: str, variant: str = "", *, created: int = T0
) -> dict[str, Any]:
    """A ``model-switched`` control message: carries a model but is NOT counted."""
    return {
        "id": message_id,
        "type": "model-switched",
        "time": {"created": created},
        "model": {"id": model, "providerID": provider, "variant": variant},
    }


def make_session(session_id: str, **extra: Any) -> dict[str, Any]:
    return {"id": session_id, **extra}


class FakeService:
    """Deterministic stand-in for ``<executable> api``.

    Call it like the real runner: ``fake([exe, "api", "get", path])``. It
    paginates the configured sessions/messages using a configurable page size and
    records every argv it receives on :attr:`requests`.

    Error modes (set ``service.mode``):
        "ok"        — normal paginated responses
        "down"      — every GET exits nonzero (service unavailable)
        "bad-json"  — GET returns non-JSON text
        "bad-shape" — GET returns JSON missing the cursor/data envelope
        "tag-error" — GET returns an API error object ``{"_tag": ...}``
        "empty"     — GET returns empty stdout
    """

    def __init__(
        self,
        sessions: Iterable[str],
        messages_by_session: dict[str, list[dict[str, Any]]],
        *,
        page_size: int = 1000,
        mode: str = "ok",
    ) -> None:
        self.sessions = list(sessions)
        self.messages_by_session = messages_by_session
        self.page_size = page_size
        self.mode = mode
        self.requests: list[list[str]] = []
        # Compatibility-probe behavior (V2 prints help to stdout, exit 0).
        self.help_returncode = 0
        self.help_stdout = "opencode2 api\nMake a request to the running server\n"

    def __call__(self, argv: list[str]) -> ProcResult:
        self.requests.append(list(argv))
        if len(argv) >= 2 and argv[1] == "api" and (len(argv) < 3 or argv[2] == "--help"):
            return ProcResult(self.help_returncode, self.help_stdout, "")
        if len(argv) >= 4 and argv[1] == "api" and argv[2] == "get":
            return self._handle_get(argv[3])
        return ProcResult(1, "", f"unexpected argv: {argv}")

    def _handle_get(self, raw_path: str) -> ProcResult:
        if self.mode == "down":
            return ProcResult(1, "Could not reach server\n", "")
        if self.mode == "bad-json":
            return ProcResult(0, "not-json", "")
        if self.mode == "empty":
            return ProcResult(0, "", "")
        if self.mode == "tag-error":
            return ProcResult(
                0, json.dumps({"_tag": "InvalidRequestError", "message": "Invalid session ID"}), ""
            )
        if self.mode == "bad-shape":
            return ProcResult(0, json.dumps({"data": []}), "")

        path, _, query_string = raw_path.partition("?")
        query = urllib.parse.parse_qs(query_string)
        return ProcResult(0, json.dumps(self._page(path, query)), "")

    def _page(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        if path == "/api/session":
            items = [{"id": sid} for sid in self.sessions]
        else:
            # /api/session/{id}/message — the id is URL-encoded in the path.
            prefix = "/api/session/"
            suffix = "/message"
            session_id = urllib.parse.unquote(path[len(prefix) : -len(suffix)])
            items = self.messages_by_session.get(session_id, [])

        # The service paginates with its own page size; the client's requested
        # ``limit`` is still recorded (via ``self.requests``) so tests can assert
        # exactly which query string the client sent on each page.
        offset = int(query["cursor"][0]) if "cursor" in query else 0
        page = items[offset : offset + self.page_size]
        next_offset = offset + len(page)
        next_cursor = str(next_offset) if next_offset < len(items) else None
        return {"data": page, "cursor": {"previous": None, "next": next_cursor}}
