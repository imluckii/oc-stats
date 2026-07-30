"""Synthetic HTTP tests for the OpenCode V2 server source."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from oc_usage.remote import (
    RemoteAuthError,
    RemoteClient,
    RemoteError,
    RemoteNoUsageDataError,
    RemoteSchemaError,
    ServerURL,
)


def _assistant(message_id: str, *, input: int, provider: str = "provider", variant: str = "high"):
    return {
        "id": message_id,
        "type": "assistant",
        "time": {"created": 1_700_000_000_000},
        "model": {"id": "model", "providerID": provider, "variant": variant},
        "tokens": {
            "input": input,
            "output": 3,
            "reasoning": 2,
            "cache": {"read": 5, "write": 7},
        },
        "cost": 0.25,
    }


class APIHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict[str, list[str]], str | None]] = []
    mode = "ok"

    def do_GET(self):  # noqa: N802 - stdlib handler API
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        auth = self.headers.get("Authorization")
        self.requests.append((parsed.path, query, auth))
        if self.mode == "auth" and auth != "Basic " + base64.b64encode(b"alice:secret").decode():
            self._send(401, {"_tag": "UnauthorizedError", "message": "no"})
            return
        if self.mode == "bad-json":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not-json")
            return
        if self.mode == "bad-shape":
            self._send(200, {"data": []})
            return
        if parsed.path == "/api/session":
            if query.get("cursor") == ["sessions-2"]:
                self._send(200, {"data": [{"id": "ses_two"}], "cursor": {"next": None}})
            else:
                self._send(
                    200,
                    {"data": [{"id": "ses_one"}], "cursor": {"next": "sessions-2"}},
                )
            return
        if parsed.path == "/api/session/ses_one/message":
            if query.get("cursor") == ["messages-2"]:
                self._send(
                    200, {"data": [_assistant("msg_two", input=20)], "cursor": {"next": None}}
                )
            else:
                self._send(
                    200,
                    {
                        "data": [
                            {"id": "msg_user", "type": "user"},
                            _assistant("msg_one", input=10),
                        ],
                        "cursor": {"next": "messages-2"},
                    },
                )
            return
        if parsed.path == "/api/session/ses_two/message":
            self._send(200, {"data": [], "cursor": {"next": None}})
            return
        self._send(404, {})

    def log_message(self, *_args):
        return

    def _send(self, status: int, payload: object):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def http_server():
    APIHandler.requests = []
    APIHandler.mode = "ok"
    server = ThreadingHTTPServer(("127.0.0.1", 0), APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_v2_server_reads_assistant_messages_and_pagination(http_server):
    rows = list(RemoteClient(http_server, username="alice", password="secret").rows())

    assert [(row.provider, row.model, row.variant) for row in rows] == [
        ("provider", "model", "high"),
        ("provider", "model", "high"),
    ]
    assert [row.input for row in rows] == [10, 20]
    assert [row.cache_read for row in rows] == [5, 5]
    assert [row.cache_write for row in rows] == [7, 7]
    assert sum(row.total for row in rows) == 64
    assert APIHandler.requests[0][0] == "/api/session"
    assert APIHandler.requests[0][1]["order"] == ["asc"]
    assert APIHandler.requests[1][0].endswith("/ses_one/message")
    assert APIHandler.requests[2][1]["cursor"] == ["messages-2"]
    assert APIHandler.requests[3][1]["cursor"] == ["sessions-2"]
    expected_auth = "Basic " + base64.b64encode(b"alice:secret").decode()
    assert all(request[2] == expected_auth for request in APIHandler.requests)


def test_server_url_rejects_userinfo_and_preserves_path_prefix():
    with pytest.raises(RemoteError, match="credentials"):
        ServerURL.parse("https://alice:secret@example.test")
    client = RemoteClient("https://example.test/proxy/")
    assert client._url("/api/session") == "https://example.test/proxy/api/session"  # noqa: SLF001


def test_server_auth_failure_does_not_echo_password(http_server):
    APIHandler.mode = "auth"
    with pytest.raises(RemoteAuthError, match="401") as exc:
        list(RemoteClient(http_server, password="wrong-secret").rows())
    assert "wrong-secret" not in str(exc.value)


@pytest.mark.parametrize("mode", ["bad-json", "bad-shape"])
def test_incompatible_server_response_fails_loudly(http_server, mode):
    APIHandler.mode = mode
    with pytest.raises(RemoteSchemaError):
        list(RemoteClient(http_server).rows())


def test_server_with_no_assistant_messages_is_not_zero_usage(http_server):
    class NoAssistantHandler(APIHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/api/session":
                self._send(200, {"data": [{"id": "ses_empty"}], "cursor": {"next": None}})
            else:
                self._send(
                    200, {"data": [{"id": "msg_user", "type": "user"}], "cursor": {"next": None}}
                )

    # Swap the server's handler only for this isolated fixture server.
    server = ThreadingHTTPServer(("127.0.0.1", 0), NoAssistantHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(RemoteNoUsageDataError):
            list(RemoteClient(f"http://127.0.0.1:{server.server_port}").rows())
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
