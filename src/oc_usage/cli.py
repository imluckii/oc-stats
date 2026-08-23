"""Command-line interface for ``oc-stats``.

Exit codes:
    0  — success
    1  — no executable, service unavailable, incompatible API, or no usage data
    2  — argument parsing error (from argparse)
"""

from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext
from pathlib import Path

from oc_usage import __version__
from oc_usage.database import DatabaseError, discover_databases, load_databases
from oc_usage.models import aggregate
from oc_usage.pricing import PricingError
from oc_usage.render import (
    fmt_compact,
    make_console,
    needs_ascii,
    render_json,
    render_rich,
)
from oc_usage.service import (
    ExecutableNotFoundError,
    IncompatibleExecutableError,
    NoUsageDataError,
    ServiceClient,
    ServiceError,
    ServiceSchemaError,
    ServiceUnavailableError,
)

PROG = "oc-stats"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description=("Retained OpenCode token usage from local databases or the running service."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  oc-stats          # show the report\n"
            "  oc-stats --json   # machine-readable JSON\n"
            "  oc-stats tui      # interactive TUI (needs oc-stats[tui])\n"
            "  oc-stats --db PATH tui   # TUI over an explicit database\n"
            "\n"
            "In OpenCode's shell mode:  !oc-stats\n"
        ),
    )
    ap.add_argument(
        "--db",
        type=Path,
        action="append",
        metavar="PATH",
        help="database path; repeat to merge multiple databases "
        "(must come before the tui subcommand)",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    subparsers = ap.add_subparsers(dest="command")
    subparsers.add_parser("tui", help="interactive TUI (requires textual)")
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
        # exception merely because a legacy console cannot print a character.
        encoding = getattr(sys.stderr, "encoding", None) or "ascii"
        try:
            safe = text.encode(encoding, errors="backslashreplace").decode(
                encoding, errors="replace"
            )
        except (LookupError, UnicodeError):
            safe = text.encode("ascii", errors="backslashreplace").decode("ascii")
        sys.stderr.write(safe)


def load_rows(dbs: list[Path] | None):
    """Discover/validate databases and load rows.

    Returns ``(rows, source)``. Mirrors the data path used by the report
    mode: explicit --db paths, discovery otherwise, service fallback last.
    """
    explicit = dbs is not None
    databases = [path.expanduser() for path in dbs] if explicit else discover_databases()
    for database in databases:
        if not database.is_file():
            raise DatabaseError(f"database not found: {database}")
    rows, used_databases = load_databases(databases, skip_errors=not explicit)
    if used_databases:
        count = len(used_databases)
        source = f"{count} local OpenCode database{'s' if count != 1 else ''}"
    else:
        rows = list(ServiceClient().rows())
        source = "OpenCode service"
    return rows, source


def run_tui(dbs: list[Path] | None) -> int:
    try:
        from oc_usage.tui import OcStatsApp
    except ImportError:
        _err(
            "the TUI needs textual: pipx install --force "
            "'oc-stats[tui]' (or pip install oc-stats[tui])"
        )
        return 1

    def loader():
        return load_rows(dbs)

    OcStatsApp(loader).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "tui":
        return run_tui(args.db)

    console = make_console()
    loading = (
        console.status("Reading OpenCode usage...", spinner="line")
        if console.is_terminal and not args.json
        else nullcontext()
    )

    try:
        with loading:
            rows, source = load_rows(args.db)
    except ExecutableNotFoundError as exc:
        _err(str(exc))
        return 1
    except IncompatibleExecutableError as exc:
        _err(str(exc))
        return 1
    except ServiceUnavailableError as exc:
        _err(str(exc))
        return 1
    except NoUsageDataError as exc:
        _err(str(exc))
        return 1
    except ServiceSchemaError as exc:
        _err(str(exc))
        return 1
    except ServiceError as exc:  # any other transport error, surfaced cleanly
        _err(str(exc))
        return 1
    except DatabaseError as exc:
        _err(str(exc))
        return 1

    try:
        report = aggregate(rows, source=source)
    except PricingError as exc:
        # A broken user price override must fail loudly, not zero the report.
        _err(str(exc))
        return 1

    if args.json:
        print(render_json(report))
        return 0

    ascii_mode = needs_ascii()
    try:
        render_rich(report, fmt_num=fmt_compact, console=console, ascii=ascii_mode)
    except UnicodeEncodeError:
        # A stream can misreport its encoding (or change it while the process
        # is running). Retry once with the fully ASCII renderer; all dynamic
        # labels are escaped there as well.
        render_rich(report, fmt_num=fmt_compact, console=make_console(), ascii=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
