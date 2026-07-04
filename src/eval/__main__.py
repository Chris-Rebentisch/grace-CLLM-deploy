"""Module entry point: ``python -m src.eval``."""

from __future__ import annotations

import sys

from src.eval.cli import main

if __name__ == "__main__":
    sys.exit(main())
