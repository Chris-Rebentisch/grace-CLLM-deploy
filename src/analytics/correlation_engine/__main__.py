"""Module dunder entry: ``python -m src.analytics.correlation_engine``.

Re-exports ``cli.main`` so the launchd plist can invoke a single
sanctioned process per D246.
"""

from __future__ import annotations

import sys

from src.analytics.correlation_engine.cli import main

if __name__ == "__main__":
    sys.exit(main())
