"""Tests for the OpenCode service transport (subprocess ``api`` client).

All fixtures are synthetic. No real OpenCode history, sockets, or credentials
are used.
"""

from __future__ import annotations

import pytest

from oc_usage.service import (
    PAGE_LIMIT,
    ExecutableNotFoundError,
    IncompatibleExecutableError,
    NoUsageDataError,
    ServiceClient,
    ServiceSchemaError,
    ServiceUnavailableError,
    discover_executable,
)
from tests.helpers import (
    T0,
    FakeService,
    assistant_message,
    make_session,
    model_switched_message,
    user_message,
)


def patch_which(monkeypatch, mapping: dict[str, str | None]) -> None:
    monkeypatch.setattr("oc_usage.service.shutil.which", lambda name: mapping.get(name))


# ── executable discovery & compatibility ──────────────────────────────────────


def test_discover_prefers_opencode2_over_opencode(monkeypatch):
    patch_which(monkeypatch, {"opencode2": "/bin/opencode2", "opencode": "/bin/opencode"})
    assert discover_executable() == "/bin/opencode2"


def test_discover_falls_back_to_opencode(monkeypatch):
    patch_which(monkeypatch, {"opencode2": None, "opencode": "/bin/opencode"})
    assert discover_executable() == "/bin/opencode"


def test_discover_returns_none_when_neither_present(monkeypatch):
    patch_which(monkeypatch, {"opencode2": None, "opencode": None})
    assert discover_executable() is None


def test_client_with_no_executable_raises_not_found(monkeypatch):
    patch_which(monkeypatch, {"opencode2": None, "opencode": None})
    client = ServiceClient()
    with pytest.raises(ExecutableNotFoundError, match="was not found"):
        list(client.rows())


def test_v1_binary_without_api_command_is_rejected(fake_service):
    # A legacy V1 `opencode` prints its banner to stderr with EMPTY stdout for
    # `api --help`; the compatibility probe must reject it.
    fake_service.help_returncode = 0
    fake_service.help_stdout = ""
    client = ServiceClient(executable="opencode", runner=fake_service)
    with pytest.raises(IncompatibleExecutableError, match="does not provide"):
        list(client.rows())


def test_compatibility_probe_runs_only_once(fake_service):
    fake_service.page_size = 1000
    client = ServiceClient(executable="opencode2", runner=fake_service)
    list(client.rows())
    help_calls = [r for r in fake_service.requests if r[2:3] == ["--help"]]
    assert len(help_calls) == 1


# ── happy path: exact aggregation & extraction ────────────────────────────────


def test_reads_assistant_messages_and_extracts_exact_components(client, sample_specs):
    rows = list(client.rows())
    assert [r.provider for r in rows] == ["zai-coding-plan", "zai-coding-plan", "openai", "openai"]

    a0 = rows[0]
    spec = sample_specs[0]
    assert a0.model == spec[1]
    assert a0.variant == spec[2]
    assert a0.input == spec[3]
    assert a0.cache_read == spec[4]
    assert a0.cache_write == spec[5]
    assert a0.output == spec[6]
    assert a0.reasoning == spec[7]
    assert a0.cost == spec[8]
    assert a0.time_created == spec[9]


def test_non_assistant_messages_are_filtered(client):
    rows = list(client.rows())
    # user + model-switched messages are not counted; only the 4 assistants.
    assert len(rows) == 4
    assert all(r.model in {"glm-4.7", "gpt-4o", "gpt-4o-mini"} for r in rows)


def test_model_switched_messages_within_a_session_are_not_counted():
    # A session whose model changed mid-stream: each assistant keeps its own
    # model, and the switch control message is ignored.
    messages = [
        assistant_message("m1", ("openai", "gpt-4o", "high", 10, 0, 0, 1, 0, 0.0, T0)),
        model_switched_message("m2", "gpt-4o-mini", "openai", "low"),
        assistant_message("m3", ("openai", "gpt-4o-mini", "low", 5, 0, 0, 1, 0, 0.0, T0 + 1)),
    ]
    fake = FakeService(["s1"], {"s1": messages})
    rows = list(ServiceClient(executable="opencode2", runner=fake).rows())
    assert [(r.model, r.variant) for r in rows] == [("gpt-4o", "high"), ("gpt-4o-mini", "low")]


def test_provider_variant_cache_and_cost_extraction():
    fake = FakeService(
        ["s1"],
        {
            "s1": [
                assistant_message("m1", ("zai", "glm-5.2", "max", 100, 4096, 128, 30, 8, 0.25, T0))
            ]
        },
    )
    (row,) = list(ServiceClient(executable="opencode2", runner=fake).rows())
    assert row.provider == "zai"
    assert row.variant == "max"
    assert row.cache_read == 4096
    assert row.cache_write == 128
    assert row.cost == 0.25
    assert row.total == 100 + 4096 + 128 + 30 + 8


def test_time_created_drives_span_in_aggregation(client):
    from oc_usage.models import aggregate

    report = aggregate(list(client.rows()))
    assert report.span is not None
    lo, hi = report.span
    assert lo.timestamp() * 1000 == T0
    assert hi.timestamp() * 1000 == T0 + 3000


# ── pagination: request paths, cursors, ordering ──────────────────────────────


def test_session_pagination_walks_cursor_next_until_none():
    fake = FakeService(
        ["s1", "s2", "s3"],
        {
            "s1": [assistant_message("s1m1", ("p", "m", "", 1, 0, 0, 0, 0, 0.0, T0))],
            "s2": [assistant_message("s2m1", ("p", "m", "", 1, 0, 0, 0, 0, 0.0, T0 + 1))],
            "s3": [assistant_message("s3m1", ("p", "m", "", 1, 0, 0, 0, 0, 0.0, T0 + 2))],
        },
        page_size=1,
    )
    rows = list(ServiceClient(executable="opencode2", runner=fake).rows())
    assert [r.time_created for r in rows] == [T0, T0 + 1, T0 + 2]

    gets = [r[3] for r in fake.requests if r[1:3] == ["api", "get"]]
    # The first page carries ascending order + limit; every later page carries
    # the opaque cursor plus the same limit (order is never combined with cursor).
    assert gets[0] == f"/api/session?order=asc&limit={PAGE_LIMIT}"
    assert gets[1].endswith(f"/s1/message?order=asc&limit={PAGE_LIMIT}")
    assert gets[2] == f"/api/session?cursor=1&limit={PAGE_LIMIT}"
    assert gets[3].endswith(f"/s2/message?order=asc&limit={PAGE_LIMIT}")
    assert gets[4] == f"/api/session?cursor=2&limit={PAGE_LIMIT}"
    assert gets[5].endswith(f"/s3/message?order=asc&limit={PAGE_LIMIT}")


def test_message_pagination_walks_cursor_next():
    messages = [
        assistant_message(f"m{i}", ("p", "m", "", i, 0, 0, 0, 0, 0.0, T0 + i)) for i in range(5)
    ]
    fake = FakeService(["s1"], {"s1": messages}, page_size=2)
    rows = list(ServiceClient(executable="opencode2", runner=fake).rows())
    assert [r.input for r in rows] == [0, 1, 2, 3, 4]

    msg_gets = [r[3] for r in fake.requests if len(r) >= 4 and r[2] == "get" and "/message" in r[3]]
    assert msg_gets[0].endswith(f"?order=asc&limit={PAGE_LIMIT}")
    assert msg_gets[1].endswith(f"?cursor=2&limit={PAGE_LIMIT}")
    assert msg_gets[2].endswith(f"?cursor=4&limit={PAGE_LIMIT}")
    assert len(msg_gets) == 3  # 2 + 2 + 1


def test_session_id_is_url_escaped_in_message_path():
    fake = FakeService(
        ["ses/with?special=&chars"],
        {
            "ses/with?special=&chars": [
                assistant_message("m1", ("p", "m", "", 1, 0, 0, 0, 0, 0.0, T0))
            ]
        },
    )
    list(ServiceClient(executable="opencode2", runner=fake).rows())
    msg_get = next(
        r[3] for r in fake.requests if len(r) >= 4 and r[2] == "get" and "/message" in r[3]
    )
    # The raw id must not appear unescaped in the path; only the encoded form.
    assert "ses/with" not in msg_get
    assert "ses%2Fwith%3Fspecial%3D%26chars/message" in msg_get


def test_cursor_query_value_is_encoded():
    fake = FakeService(
        ["s1", "s2"],
        {
            "s1": [assistant_message("m1", ("p", "m", "", 1, 0, 0, 0, 0, 0.0, T0))],
            "s2": [assistant_message("m2", ("p", "m", "", 1, 0, 0, 0, 0, 0.0, T0 + 1))],
        },
        page_size=1,
    )
    list(ServiceClient(executable="opencode2", runner=fake).rows())
    cursor_get = next(
        r[3]
        for r in fake.requests
        if len(r) >= 4 and r[2] == "get" and r[3].startswith("/api/session?cursor=")
    )
    # Cursor value "1" is a clean token; the limit travels alongside it.
    assert cursor_get == f"/api/session?cursor=1&limit={PAGE_LIMIT}"


# ── deduplication ─────────────────────────────────────────────────────────────


def test_duplicate_message_ids_across_pages_are_not_double_counted():
    # Two pages that (mis)repeat the same message id must aggregate it once.
    # Cursors differ and terminate so the dedupe path, not the repeat guard, is
    # what protects the totals.
    fake = FakeService(["s1"], {"s1": []})
    original = fake._page
    calls = {"n": 0}

    def repeating_page(path, query):
        if "/message" in path:
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "data": [assistant_message("dup", ("p", "m", "", 10, 0, 0, 0, 0, 0.0, T0))],
                    "cursor": {"previous": None, "next": "2"},
                }
            return {
                "data": [assistant_message("dup", ("p", "m", "", 10, 0, 0, 0, 0, 0.0, T0))],
                "cursor": {"previous": None, "next": None},
            }
        return original(path, query)

    fake._page = repeating_page
    rows = list(ServiceClient(executable="opencode2", runner=fake).rows())
    assert len(rows) == 1
    assert rows[0].input == 10


# ── error handling ────────────────────────────────────────────────────────────


def test_service_down_maps_to_unavailable(fake_service):
    fake_service.mode = "down"
    client = ServiceClient(executable="opencode2", runner=fake_service)
    with pytest.raises(ServiceUnavailableError, match="unavailable"):
        list(client.rows())


def test_invalid_json_fails_loudly(fake_service):
    fake_service.mode = "bad-json"
    client = ServiceClient(executable="opencode2", runner=fake_service)
    with pytest.raises(ServiceSchemaError, match="invalid JSON"):
        list(client.rows())


def test_truncated_response_retries_until_valid(fake_service):
    # The api command can intermittently truncate a large page; an idempotent
    # GET that succeeds on a later attempt must be accepted, not treated as an
    # incompatible schema. Here the first session GET fails JSON twice, then
    # succeeds, and aggregation proceeds normally.
    calls = {"n": 0}

    def runner(argv):
        from oc_usage.service import ProcResult

        if len(argv) >= 4 and argv[2] == "get" and argv[3].startswith("/api/session?order"):
            calls["n"] += 1
            if calls["n"] <= 2:
                return ProcResult(0, "truncated", "")
        return fake_service(argv)

    client = ServiceClient(executable="opencode2", runner=runner, json_retries=3, retry_delay=0)
    rows = list(client.rows())
    assert len(rows) == 4  # recovered and aggregated normally
    assert calls["n"] == 3


def test_empty_stdout_fails_loudly(fake_service):
    fake_service.mode = "empty"
    client = ServiceClient(executable="opencode2", runner=fake_service)
    with pytest.raises(ServiceSchemaError):
        list(client.rows())


def test_api_error_envelope_fails_loudly(fake_service):
    fake_service.mode = "tag-error"
    client = ServiceClient(executable="opencode2", runner=fake_service)
    with pytest.raises(ServiceSchemaError, match="Invalid session ID"):
        list(client.rows())


def test_non_object_response_fails_loudly():
    fake = FakeService(["s1"], {})
    fake.help_stdout = "ok"

    def runner(argv):
        if len(argv) >= 3 and argv[2] == "get":
            from oc_usage.service import ProcResult

            return ProcResult(0, "[1, 2, 3]", "")  # valid JSON, not an object
        return fake(argv)

    client = ServiceClient(executable="opencode2", runner=runner)
    with pytest.raises(ServiceSchemaError, match="non-object"):
        list(client.rows())


def test_missing_data_cursor_envelope_fails_loudly(fake_service):
    fake_service.mode = "bad-shape"
    client = ServiceClient(executable="opencode2", runner=fake_service)
    with pytest.raises(ServiceSchemaError, match="did not match the V2 API shape"):
        list(client.rows())


def test_repeating_session_cursor_fails_loudly():
    fake = FakeService(["s1", "s2"], {}, page_size=1)
    original = fake._page

    def stuck(path, query):
        if path == "/api/session":
            return {"data": [{"id": "s1"}], "cursor": {"previous": None, "next": "1"}}
        return original(path, query)

    fake._page = stuck
    client = ServiceClient(executable="opencode2", runner=fake)
    with pytest.raises(ServiceSchemaError, match="repeating session cursor"):
        list(client.rows())


def test_repeating_message_cursor_fails_loudly():
    fake = FakeService(
        ["s1"], {"s1": [assistant_message("m1", ("p", "m", "", 1, 0, 0, 0, 0, 0.0, T0))]}
    )
    original = fake._page

    def stuck(path, query):
        if "/message" in path:
            return {
                "data": [assistant_message("m1", ("p", "m", "", 1, 0, 0, 0, 0, 0.0, T0))],
                "cursor": {"previous": None, "next": "1"},
            }
        return original(path, query)

    fake._page = stuck
    client = ServiceClient(executable="opencode2", runner=fake)
    with pytest.raises(ServiceSchemaError, match="repeating message cursor"):
        list(client.rows())


def test_no_assistant_messages_is_not_zero_usage():
    fake = FakeService(
        ["s1"], {"s1": [user_message("u1"), model_switched_message("ms1", "m", "p")]}
    )
    client = ServiceClient(executable="opencode2", runner=fake)
    with pytest.raises(NoUsageDataError, match="No assistant messages"):
        list(client.rows())


def test_assistant_without_tokens_or_cost_defaults_to_zero():
    # Per the OpenAPI schema, tokens/cost are optional on an assistant message.
    fake = FakeService(
        ["s1"],
        {
            "s1": [
                {
                    "id": "m1",
                    "type": "assistant",
                    "time": {"created": T0},
                    "model": {"id": "m", "providerID": "p", "variant": ""},
                }
            ]
        },
    )
    (row,) = list(ServiceClient(executable="opencode2", runner=fake).rows())
    assert row.input == 0 and row.cache_read == 0 and row.cache_write == 0
    assert row.output == 0 and row.reasoning == 0
    assert row.cost == 0.0
    assert row.total == 0


def test_assistant_with_non_dict_model_fails_loudly():
    fake = FakeService(
        ["s1"], {"s1": [{"id": "m1", "type": "assistant", "time": {"created": T0}, "model": "x"}]}
    )
    with pytest.raises(ServiceSchemaError, match="no model object"):
        list(ServiceClient(executable="opencode2", runner=fake).rows())


# ── end-to-end subprocess plumbing (real argv, no shell) ──────────────────────


_FAKE_LOGIC = (
    "#!/usr/bin/env python3\n"
    "import json, sys\n"
    "CREATED = __CREATED__\n"
    "args = sys.argv[1:]\n"
    "if args[:2] == ['api', '--help']:\n"
    "    print('opencode2 api help'); sys.exit(0)\n"
    "if args[:2] == ['api', 'get']:\n"
    "    path = args[2]\n"
    "    if path.startswith('/api/session?order=asc') and '/message' not in path:\n"
    "        print(json.dumps({'data':[{'id':'s1'}],'cursor':{'previous':None,'next':None}}))\n"
    "    elif '/s1/message?order=asc' in path:\n"
    "        print(json.dumps({'data':[{'id':'m1','type':'assistant',"
    "        'time':{'created':CREATED},"
    "        'model':{'id':'m','providerID':'p','variant':''},"
    "        'tokens':{'input':7,'output':2,'reasoning':0,'cache':{'read':0,'write':0}},"
    "        'cost':0.0}],'cursor':{'previous':None,'next':None}}))\n"
    "    else:\n"
    "        sys.exit(0)\n"
    "    sys.exit(0)\n"
)


def _install_fake_executable(tmp_path) -> str:
    """Place a deterministic fake `opencode2` on a fresh bin dir; return it.

    Cross-platform: a shebang script + chmod on POSIX, a ``.bat`` launcher over a
    ``.py`` script on Windows (so ``shutil.which`` resolves it via PATHEXT).
    """
    import os
    import sys

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    logic = _FAKE_LOGIC.replace("__CREATED__", str(T0))
    if os.name == "nt" or sys.platform.startswith("win"):
        script = bin_dir / "opencode2.py"
        script.write_text(logic, encoding="utf-8")
        (bin_dir / "opencode2.bat").write_text(
            f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8"
        )
    else:
        exe = bin_dir / "opencode2"
        exe.write_text(logic, encoding="utf-8")
        exe.chmod(0o755)
    return str(bin_dir)


def test_real_fake_executable_end_to_end(tmp_path, monkeypatch):
    """Validate the real default_runner with argv plumbing against a script.

    Confirms PATH discovery, the compatibility probe, and Windows-safe argv
    passing (a list, never shell=True) using a deterministic fake executable.
    """
    bin_dir = _install_fake_executable(tmp_path)
    monkeypatch.setenv("PATH", bin_dir, prepend=":")
    rows = list(ServiceClient().rows())
    assert len(rows) == 1
    assert rows[0].input == 7


def test_make_session_helper_passes_extra_fields():
    session = make_session("s1", title="anything", cost=0)
    assert session["id"] == "s1"
    assert session["title"] == "anything"
