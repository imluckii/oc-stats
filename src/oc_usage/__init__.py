"""oc-usage — all-time OpenCode token usage from local or server history.

A standalone CLI that reads assistant turns from OpenCode's SQLite database
(both the v1 ``message`` and v2 ``session_message`` schemas) or one V2 HTTP
server and reports token usage broken down by provider, model, and variant,
with cost when it is tracked.
"""

from __future__ import annotations

try:  # installed distribution: authoritative
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("oc-usage")
    except PackageNotFoundError:  # pragma: no cover - dev/editable fallback
        __version__ = "0.1.0"
except Exception:  # pragma: no cover - extremely defensive
    __version__ = "0.1.0"

__all__ = ["__version__"]
