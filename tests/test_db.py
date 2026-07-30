"""Tests for DB discovery, schema detection, and row loading."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import PureWindowsPath

import pytest

from oc_usage import db
from oc_usage.db import (
    DatabaseNotFoundError,
    NoUsageDataError,
    detect_schema,
    find_db,
    load_rows,
)
from tests.helpers import T0, build_v1_db, build_v2_db, create_both_tables, insert_v1


def _connect(path):
    return db._connect(path)  # noqa: SLF001 — exercising the read-only helper


# ── discovery ─────────────────────────────────────────────────────────────────


def test_find_db_prefers_explicit_arg(tmp_path):
    custom = tmp_path / "custom.db"
    custom.write_text("")
    assert find_db(str(custom)) == str(custom)


def test_find_db_prefers_v2_when_both_default_candidates_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_DB_DIR", str(tmp_path))
    (tmp_path / "opencode-next.db").write_text("")
    (tmp_path / "opencode.db").write_text("")
    assert find_db(None).endswith("opencode-next.db")


def test_find_db_falls_back_to_v1(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_DB_DIR", str(tmp_path))
    (tmp_path / "opencode.db").write_text("")
    assert find_db(None).endswith("opencode.db")


def test_find_db_returns_v2_path_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_DB_DIR", str(tmp_path))
    assert find_db(None).endswith("opencode-next.db")


def test_default_db_dir_respects_xdg_data_home(monkeypatch):
    monkeypatch.setattr(db.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg-data")
    assert db.default_db_dirs() == ("/tmp/xdg-data/opencode",)


def test_default_db_dir_uses_unix_home_fallback(monkeypatch):
    monkeypatch.setattr(db.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(
        db.os.path,
        "expanduser",
        lambda path: "/home/test/.local/share" if path == "~/.local/share" else path,
    )
    assert db.default_db_dirs() == ("/home/test/.local/share/opencode",)


def test_default_db_dir_uses_macos_application_support(monkeypatch):
    monkeypatch.setattr(db.sys, "platform", "darwin")
    monkeypatch.setattr(
        db.os.path,
        "expanduser",
        lambda path: (
            "/Users/test/Library/Application Support"
            if path == "~/Library/Application Support"
            else path
        ),
    )
    assert db.default_db_dirs() == ("/Users/test/Library/Application Support/opencode",)


def test_default_db_dir_respects_windows_localappdata(monkeypatch):
    monkeypatch.setattr(db.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    monkeypatch.setattr(
        db.os.path,
        "expanduser",
        lambda path: r"C:\Users\test\AppData\Local" if path == "~/AppData/Local" else path,
    )
    assert db.default_db_dirs() == (os.path.join(r"C:\Users\test\AppData\Local", "opencode"),)


def test_default_db_dir_falls_back_when_localappdata_is_missing(monkeypatch):
    monkeypatch.setattr(db.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(
        db.os.path,
        "expanduser",
        lambda path: r"C:\Users\test\AppData\Local" if path == "~/AppData/Local" else path,
    )
    assert db.default_db_dirs() == (os.path.join(r"C:\Users\test\AppData\Local", "opencode"),)


def test_default_db_dir_tries_localappdata_then_home_fallback(monkeypatch):
    monkeypatch.setattr(db.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"D:\portable\AppData\Local")
    monkeypatch.setattr(
        db.os.path,
        "expanduser",
        lambda path: r"C:\Users\test\AppData\Local" if path == "~/AppData/Local" else path,
    )
    assert db.default_db_dirs() == (
        os.path.join(r"D:\portable\AppData\Local", "opencode"),
        os.path.join(r"C:\Users\test\AppData\Local", "opencode"),
    )


def test_find_db_prefers_v2_across_default_directory_candidates(tmp_path, monkeypatch):
    first = tmp_path / "primary"
    second = tmp_path / "fallback"
    first.mkdir()
    second.mkdir()
    (first / "opencode.db").write_text("")
    (second / "opencode-next.db").write_text("")
    monkeypatch.setattr(db, "default_db_dirs", lambda: (str(first), str(second)))
    assert find_db(None) == str(second / "opencode-next.db")


# ── schema detection (row-based, not table-based) ─────────────────────────────


def test_detect_v2_when_session_message_has_assistant_rows(v2_db):
    with _connect(v2_db) as con:
        assert detect_schema(con) == "v2"


def test_detect_v1_when_only_message_has_assistant_rows(v1_db):
    # v1_db also has a session_message table but only control rows.
    with _connect(v1_db) as con:
        assert detect_schema(con) == "v1"


def test_detect_none_when_no_assistant_rows_anywhere(empty_db):
    with _connect(empty_db) as con:
        assert detect_schema(con) is None


def test_detect_v2_overrides_v1_when_session_message_has_assistant_rows(v2_db):
    # v2_db also has an empty message table — v2 should still win by row content.
    with _connect(v2_db) as con:
        assert detect_schema(con) == "v2"


# ── loading ───────────────────────────────────────────────────────────────────


def test_load_v2_rows(v2_db, v2_rows):
    rows = list(load_rows(v2_db))
    assert len(rows) == len(v2_rows)
    first = v2_rows[0]
    r = rows[0]
    assert r.provider == first[0]
    assert r.model == first[1]
    assert r.variant == first[2]
    assert r.input == first[3]
    assert r.cache_read == first[4]
    assert r.cache_write == first[5]
    assert r.output == first[6]
    assert r.reasoning == first[7]
    assert r.cost == first[8]
    assert r.time_created == first[9]


def test_load_v1_rows_ignore_stored_total(v1_db, v1_rows):
    rows = list(load_rows(v1_db))
    assert len(rows) == len(v1_rows)
    # First v1 fixture row: 581+7168+0+25+57 == stored total 7831.
    r = rows[0]
    assert r.total == 581 + 7168 + 0 + 25 + 57
    assert r.time_created == v1_rows[0][9]


def test_load_v1_total_reconciles_with_components(v1_db):
    rows = list(load_rows(v1_db))
    for r in rows:
        # The stored total (7831, etc.) must equal the component sum.
        assert r.total == r.input + r.cache_read + r.cache_write + r.output + r.reasoning


def test_provider_and_model_attribution(v2_db):
    rows = list(load_rows(v2_db))
    providers = {r.provider for r in rows}
    assert providers == {"zai-coding-plan", "openai"}
    models = {(r.provider, r.model, r.variant) for r in rows}
    assert ("openai", "gpt-4o", "high") in models
    assert ("openai", "gpt-4o-mini", "low") in models


def test_cache_write_is_loaded(v1_db):
    rows = list(load_rows(v1_db))
    # The cerebras fixture rows carry cache writes (128 then 0).
    writes = sorted(r.cache_write for r in rows if r.provider == "cerebras")
    assert writes == [0, 128]


# ── error handling ────────────────────────────────────────────────────────────


def test_missing_file_raises_database_not_found(tmp_path):
    missing = str(tmp_path / "nope.db")
    with pytest.raises(DatabaseNotFoundError):
        list(load_rows(missing))


def test_no_assistant_rows_raises_no_usage_data(empty_db):
    with pytest.raises(NoUsageDataError):
        list(load_rows(empty_db))


def test_malformed_json_rows_are_skipped(tmp_path):
    path = str(tmp_path / "bad.db")
    con = sqlite3.connect(path)
    try:
        # Reuse the shared schema-builder by hand for control.
        from tests.helpers import create_both_tables, insert_v2

        create_both_tables(con)
        # Two valid + two malformed rows.
        insert_v2(con, [("openai", "gpt-4o", "high", 10, 0, 0, 1, 0, 0.0, T0)])
        con.execute(
            "INSERT INTO session_message(id, session_id, type, seq, time_created, time_updated, data)"
            " VALUES('bad-1','s1','assistant',99,1,1,'{not valid json')"
        )
        con.execute(
            "INSERT INTO session_message(id, session_id, type, seq, time_created, time_updated, data)"
            " VALUES('bad-2','s1','assistant',100,2,2,'42')"
        )  # valid JSON but not an object
        con.commit()
    finally:
        con.close()

    rows = list(load_rows(path))
    assert len(rows) == 1  # only the valid object row survives
    assert rows[0].model == "gpt-4o"


def test_v1_malformed_json_does_not_hide_valid_assistant_rows(tmp_path):
    path = str(tmp_path / "v1-malformed.db")
    con = sqlite3.connect(path)
    try:
        create_both_tables(con)
        con.execute(
            "INSERT INTO message(id, session_id, time_created, time_updated, data)"
            " VALUES('bad-first','s1',1,1,'{not valid json')"
        )
        insert_v1(
            con,
            [
                ("p1", "m1", "high", 10, 20, 3, 4, 5, 0.0, T0),
                ("p2", "m2", "low", 30, 40, 6, 7, 8, 0.0, T0 + 1),
            ],
        )
        con.commit()
    finally:
        con.close()

    with _connect(path) as con:
        assert detect_schema(con) == "v1"
    rows = list(load_rows(path))
    assert [(row.provider, row.model) for row in rows] == [("p1", "m1"), ("p2", "m2")]


def test_missing_token_fields_default_to_zero(tmp_path):
    path = str(tmp_path / "sparse.db")
    con = sqlite3.connect(path)
    try:
        from tests.helpers import create_both_tables

        create_both_tables(con)
        # An assistant row with model + time but NO tokens / cost.
        con.execute(
            "INSERT INTO session_message(id, session_id, type, seq, time_created, time_updated, data)"
            " VALUES('s','s1','assistant',0,1,1, ?)",
            (json.dumps({"model": {"id": "m", "providerID": "p"}, "time": {"created": T0}}),),
        )
        con.commit()
    finally:
        con.close()

    rows = list(load_rows(path))
    assert len(rows) == 1
    r = rows[0]
    assert (r.input, r.cache_read, r.cache_write, r.output, r.reasoning) == (0, 0, 0, 0, 0)
    assert r.cost == 0.0
    assert r.total == 0


def test_database_opened_read_only(v2_db):
    # mode=ro must not create -wal/-shm side effects and must allow a second
    # read-only connection without locking.
    rows = list(load_rows(v2_db))
    assert rows  # reads succeed
    # No WAL files should have been created by read-only access.
    assert not os.path.exists(v2_db + "-wal")


def test_sqlite_uri_uses_platform_aware_windows_escaping(monkeypatch):
    monkeypatch.setattr(db.sys, "platform", "win32")
    path = r"C:\Users\Test User\AppData\Local\opencode\usage #?% 日本.db"
    assert db._sqlite_uri(path) == PureWindowsPath(path).as_uri() + "?mode=ro"


def test_special_character_database_path_is_readable(tmp_path):
    path = tmp_path / "usage #?% 日本.db"
    build_v2_db(str(path), [("p", "m", "", 1, 2, 3, 4, 5, 0.0, T0)])

    assert len(list(load_rows(str(path)))) == 1
    uri = db._sqlite_uri(str(path))
    assert "%20" in uri
    assert "%23" in uri
    assert "%3F" in uri
    assert "%25" in uri
    assert "日本" not in uri


def test_active_wal_can_still_be_read(tmp_path):
    # A writer using WAL journal mode must remain readable via the read-only URI.
    path = str(tmp_path / "wal.db")
    from tests.helpers import create_both_tables, insert_v2

    writer = sqlite3.connect(path)
    try:
        create_both_tables(writer)
        writer.execute("PRAGMA journal_mode=WAL")
        insert_v2(writer, [("openai", "gpt-4o", "high", 5, 0, 0, 1, 0, 0.0, T0)])
        writer.commit()
        # Keep the writer open so SQLite does not checkpoint/delete the -wal file.
        assert os.path.exists(path + "-wal")
        rows = list(load_rows(path))  # separate read-only connection sees committed data
        assert len(rows) == 1
    finally:
        writer.close()


def test_v2_model_as_string_is_handled(tmp_path):
    path = str(tmp_path / "strmodel.db")
    con = sqlite3.connect(path)
    try:
        from tests.helpers import create_both_tables

        create_both_tables(con)
        con.execute(
            "INSERT INTO session_message(id, session_id, type, seq, time_created, time_updated, data)"
            " VALUES('s','s1','assistant',0,1,1, ?)",
            (
                json.dumps(
                    {"model": "just-a-name", "tokens": {"input": 3}, "time": {"created": T0}}
                ),
            ),
        )
        con.commit()
    finally:
        con.close()

    rows = list(load_rows(path))
    assert rows[0].model == "just-a-name"
    assert rows[0].provider == "(unknown)"
    assert rows[0].input == 3


# ── fixture builders sanity ───────────────────────────────────────────────────


def test_builders_roundtrip(tmp_path):
    v2 = build_v2_db(str(tmp_path / "a.db"), [("p", "m", "v", 1, 2, 3, 4, 5, 0.1, T0)])
    v1 = build_v1_db(str(tmp_path / "b.db"), [("p", "m", "v", 1, 2, 3, 4, 5, 0.1, T0)])
    assert len(list(load_rows(v2))) == 1
    assert len(list(load_rows(v1))) == 1
