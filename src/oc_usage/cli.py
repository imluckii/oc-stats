"""Command-line interface for ``oc-usage``.

Exit codes:
    0  — success
    1  — database not found, no usage data, or other runtime error
    2  — argument parsing error (from argparse)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from oc_usage import __version__
from oc_usage.db import DatabaseNotFoundError, NoUsageDataError, find_db, load_rows
from oc_usage.models import aggregate
from oc_usage.render import fmt_compact, fmt_full, make_console, render_json, render_rich

PROG = "oc-usage"
FormatFn = Callable[[int | float], str]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "All-time OpenCode token usage from the local session database. "
            "Reads assistant turns (OpenCode v1 and v2 schemas) and reports "
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
            "\n"
            "In OpenCode's shell mode:  !oc-usage\n"
        ),
    )
    ap.add_argument(
        "--db",
        metavar="PATH",
        help="path to an opencode .db file (auto-detected by default; v2 preferred)",
    )
    ap.add_argument("--full", action="store_true", help="show full integers (no K/M rounding)")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    ap.add_argument("--plain", action="store_true", help="alias for --no-color")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    ap.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="show version and exit",
    )
    return ap


def _err(message: str) -> None:
    sys.stderr.write(f"{PROG}: {message}\n")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    fmt_num: FormatFn = fmt_full if args.full else fmt_compact

    db = find_db(args.db)
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

    report = aggregate(rows)

    if args.json:
        print(render_json(report))
        return 0

    no_color = args.no_color or args.plain
    console = make_console(no_color=no_color)
    render_rich(report, fmt_num=fmt_num, console=console)
    return 0


if __name__ == "__main__":
    sys.exit(main())
