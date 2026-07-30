"""oc-usage — all-time OpenCode token usage from your running OpenCode service.

A standalone CLI that reads assistant messages from the running OpenCode V2
service (via OpenCode's own ``api`` command) and reports token usage broken
down by provider, model, and variant, with cost when it is tracked.
"""

from __future__ import annotations

try:  # installed distribution: authoritative
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("oc-usage")
    except PackageNotFoundError:  # pragma: no cover - dev/editable fallback
        __version__ = "0.2.0"
except Exception:  # pragma: no cover - extremely defensive
    __version__ = "0.2.0"

__all__ = ["__version__"]
