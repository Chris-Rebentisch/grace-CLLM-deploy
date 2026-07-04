#!/usr/bin/env python3
"""Thin wrapper — delegates to `python -m src.regeneration.cli`.

Zero drift with the module entrypoint by design (§10.2 of chunk-23-spec.md).
"""

import subprocess
import sys

sys.exit(
    subprocess.run(
        [sys.executable, "-m", "src.regeneration.cli", *sys.argv[1:]]
    ).returncode
)
