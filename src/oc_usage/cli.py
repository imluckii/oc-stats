"""Command-line interface for ``oc-usage``.

Exit codes:
    0  — success
    1  — no executable, service unavailable, incompatible API, or no usage data
    2  — argument parsing error (from argparse)
"""

from __future__ import annotations

import argparse
import sys

from oc_usage import __version__
from oc_usage.models import aggregate
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

PROG = "oc-usage"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "All-time OpenCode token usage, read live from your running OpenCode V2 service."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  oc-usage          # show the report\n"
            "  oc-usage --json   # machine-readable JSON\n"
            "\n"
            "In OpenCode's shell mode:  !oc-usage\n"
        ),
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
        # exception merely because a legacy console cannot print a character.
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

    try:
        client = ServiceClient()
        rows = list(client.rows())
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

    report = aggregate(rows)

    if args.json:
        print(render_json(report))
        return 0

    ascii_mode = needs_ascii()
    console = make_console()
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
