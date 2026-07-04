#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/lint/check-final-files.sh <chunk-id>" >&2
  exit 2
fi

CHUNK="$1"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DOCS="$REPO_ROOT/docs"

python3 - "$DOCS" "$CHUNK" <<'PY'
import re
import sys
from pathlib import Path

docs = Path(sys.argv[1])
chunk = sys.argv[2]
stages = ["outline", "spec", "prompt", "audit", "handoff"]

errors = []
for stage in stages:
    files = sorted(docs.glob(f"chunk-{chunk}-{stage}-v*-FINAL.md"))
    active = []
    for f in files:
        name = f.name.lower()
        if "archived" in name:
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"Superseded by", txt, re.IGNORECASE):
            continue
        active.append(f)
    if len(active) > 1:
        errors.append(f"{stage}: multiple active FINAL files: " + ", ".join(x.name for x in active))

if errors:
    print("check-final-files: FAIL")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print(f"check-final-files: OK (chunk {chunk})")
PY
