#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/lint/check-test-band.sh <artifact-path>" >&2
  exit 2
fi

ARTIFACT="$1"

if [[ ! -f "$ARTIFACT" ]]; then
  echo "check-test-band: artifact missing: $ARTIFACT" >&2
  exit 2
fi

python3 - "$ARTIFACT" <<'PY'
import re
import sys
from pathlib import Path

artifact = Path(sys.argv[1])
text = artifact.read_text(encoding="utf-8")

pattern = re.compile(r"^\*\*Target new tests(?: this chunk)?:\*\*\s*(.+)$", re.MULTILINE)
m = pattern.search(text)
if not m:
    print("check-test-band: FAIL missing target new tests header line")
    sys.exit(1)

value = m.group(1).strip()
has_band = bool(re.search(r"\d+\s*[-–]\s*\d+", value)) or ("band:" in value.lower())
if not has_band:
    print(f"check-test-band: FAIL point claim detected: {value}")
    sys.exit(1)

print(f"check-test-band: OK ({value})")
PY
