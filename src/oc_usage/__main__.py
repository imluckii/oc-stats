"""Enable ``python -m oc_usage``."""

from __future__ import annotations

import sys

from oc_usage.cli import main

if __name__ == "__main__":
    sys.exit(main())
