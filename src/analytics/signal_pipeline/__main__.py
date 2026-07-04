"""``python -m src.analytics.signal_pipeline`` entry point."""

from __future__ import annotations

import sys

from src.analytics.signal_pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
