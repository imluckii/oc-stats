"""oc-stats — retained OpenCode token usage from local databases or the service.

A standalone CLI that reads assistant-message usage from local OpenCode V2
databases (or the running service via OpenCode's own ``api`` command) and
reports token usage broken down by provider, model, and variant. Session
ledgers are reconciled so title generation, compaction, reverted requests, and
fork-copied history are accounted for correctly.
"""

from __future__ import annotations

try:  # installed distribution: authoritative
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("oc-stats")
    except PackageNotFoundError:  # pragma: no cover - dev/editable fallback
        __version__ = "1.1.0"
except Exception:  # pragma: no cover - extremely defensive
    __version__ = "1.1.0"

__all__ = ["__version__"]
