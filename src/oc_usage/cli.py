"""Command-line interface for ``oc-usage``.

Exit codes:
    0  — success
    1  — database not found, no usage data, or other runtime error
    2  — argument parsing error (from argparse)
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

from oc_usage import __version__
from oc_usage.db import DatabaseNotFoundError, NoUsageDataError, find_db, load_rows
from oc_usage.models import SourceMetadata, aggregate
from oc_usage.remote import (
    RemoteAuthError,
    RemoteClient,
    RemoteConnectionError,
    RemoteError,
    RemoteNoUsageDataError,
    RemoteSchemaError,
)
from oc_usage.render import (
    fmt_compact,
    fmt_full,
    make_console,
    needs_ascii,
    render_json,
    render_rich,
)

PROG = "oc-usage"
FormatFn = Callable[[int | float], str]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "All-time OpenCode token usage from a local session database or "
            "one OpenCode V2 HTTP server. Reads assistant turns and reports "
            "tokens by provider, model, and variant, with cost when tracked."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  oc-usage                 # auto-detected database, compact numbers\n"
            "  oc-usage --full          # full integers (no K/M rounding)\n"
            "  oc-usage --json          # machine-readable JSON\n"
            "  oc-usage --db ~/.local/share/opencode/opencode.db   # v1 database\n"
            "  oc-usage --db ~/.local/share/opencode/opencode-next.db  # v2 database\n"
            "  oc-usage --server http://127.0.0.1:4096 --password-stdin\n"
            "\n"
            "In OpenCode's shell mode:  !oc-usage\n"
        ),
    )
    sources = ap.add_mutually_exclusive_group()
    sources.add_argument(
        "--db",
        metavar="PATH",
        help="path to one opencode .db file (highest source precedence; never merged)",
    )
    sources.add_argument(
        "--server",
        metavar="URL",
        help="read one OpenCode V2 HTTP server (mutually exclusive with --db)",
    )
    ap.add_argument(
        "--username",
        default="opencode",
        metavar="NAME",
        help="HTTP Basic username for --server (default: opencode)",
    )
    password = ap.add_mutually_exclusive_group()
    password.add_argument(
        "--password-env",
        metavar="NAME",
        help="read the HTTP Basic password from environment variable NAME",
    )
    password.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the HTTP Basic password from one line of stdin",
    )
    ap.add_argument("--full", action="store_true", help="show full integers (no K/M rounding)")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    ap.add_argument("--plain", action="store_true", help="alias for --no-color")
    ap.add_argument(
        "--ascii",
        action="store_true",
        help="use ASCII-only boxes and progress bars (auto-enabled for legacy encodings)",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    ap.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="show version and exit",
    )
    return ap


def _err(message: str) -> None:
    text = f"{PROG}: {message}\n"
    try:
        sys.stderr.write(text)
    except UnicodeEncodeError:
        # Error messages should not turn a useful exit status into another
        # exception merely because a legacy console cannot print a path.
        encoding = getattr(sys.stderr, "encoding", None) or "ascii"
        try:
            safe = text.encode(encoding, errors="backslashreplace").decode(
                encoding, errors="replace"
            )
        except (LookupError, UnicodeError):
            safe = text.encode("ascii", errors="backslashreplace").decode("ascii")
        sys.stderr.write(safe)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    fmt_num: FormatFn = fmt_full if args.full else fmt_compact

    if not args.server and (args.password_env or args.password_stdin):
        _err("--password-env and --password-stdin require --server")
        return 2

    if args.server:
        try:
            password_value = _read_password(args)
            client = RemoteClient(args.server, username=args.username, password=password_value)
            rows = list(client.rows())
        except RemoteNoUsageDataError as exc:
            _err(str(exc))
            return 1
        except (RemoteAuthError, RemoteConnectionError, RemoteSchemaError, RemoteError) as exc:
            _err(str(exc))
            return 1
        report = aggregate(rows, source=SourceMetadata("server", client.server.value))
    else:
        db = find_db(args.db)
        if db == ":memory:":
            _err("database path :memory: cannot be read after process exit; provide a file path")
            return 1
        try:
            rows = list(load_rows(db))
        except DatabaseNotFoundError:
            _err(f"database not found: {db}")
            return 1
        except NoUsageDataError:
            _err(f"no assistant messages found in {db}")
            return 1
        except Exception as exc:  # noqa: BLE001 — surface a clean message, not a traceback
            _err(f"could not read {db}: {exc}")
            return 1

        if not rows:
            _err(f"no assistant messages found in {db}")
            return 1
        report = aggregate(rows, source=SourceMetadata("local_database"))

    if args.json:
        print(render_json(report))
        return 0

    no_color = args.no_color or args.plain
    ascii_mode = args.ascii or needs_ascii()
    console = make_console(no_color=no_color)
    try:
        render_rich(report, fmt_num=fmt_num, console=console, ascii=ascii_mode)
    except UnicodeEncodeError:
        # A stream can misreport its encoding (or change it while the process
        # is running). Retry once with the fully ASCII renderer; all dynamic
        # labels are escaped there as well.
        console = make_console(no_color=no_color)
        render_rich(report, fmt_num=fmt_num, console=console, ascii=True)
    return 0


def _read_password(args: argparse.Namespace) -> str | None:
    """Read a server password without ever including it in diagnostics."""
    if args.password_env:
        value = os.environ.get(args.password_env)
        if value is None:
            raise RemoteError(f"password environment variable not set: {args.password_env}")
        return value
    if args.password_stdin:
        value = sys.stdin.readline()
        if value.endswith("\n"):
            value = value[:-1]
        if value.endswith("\r"):
            value = value[:-1]
        return value
    return None


if __name__ == "__main__":
    sys.exit(main())
