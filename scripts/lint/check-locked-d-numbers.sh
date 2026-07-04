#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/lint/check-locked-d-numbers.sh <artifact-path>" >&2
  exit 2
fi

ARTIFACT="$1"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DECISIONS="$REPO_ROOT/docs/GrACE-Decisions.md"
SIDECAR="$REPO_ROOT/.build-state/proposed-decisions.json"

python3 - "$ARTIFACT" "$DECISIONS" "$SIDECAR" <<'PY'
import json
import re
import sys
from pathlib import Path

artifact = Path(sys.argv[1])
decisions = Path(sys.argv[2])
sidecar = Path(sys.argv[3])

if not artifact.exists():
    print(f"check-locked-d-numbers: artifact missing: {artifact}", file=sys.stderr)
    sys.exit(2)
if not decisions.exists():
    print(f"check-locked-d-numbers: decisions file missing: {decisions}", file=sys.stderr)
    sys.exit(2)

text = artifact.read_text(encoding="utf-8")
tokens = re.findall(r"\bD(\d{1,4}(?:\.\d+)?)\b", text)
artifact_ds = [f"D{t}" for t in tokens]
artifact_set = set(artifact_ds)

dupes = sorted({d for d in artifact_set if artifact_ds.count(d) > 1})

decisions_text = decisions.read_text(encoding="utf-8")
locked = set(
    re.findall(
        r"\|\s*(D\d+(?:\.\d+)?)\s*\|.*\|\s*(?:locked|amended|superseded)\s*\|",
        decisions_text,
        flags=re.IGNORECASE,
    )
)

reserved = set()
if sidecar.exists():
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            vals = payload.get("proposed_d_numbers", [])
        else:
            vals = []
        if isinstance(vals, list):
            for v in vals:
                if isinstance(v, str) and re.fullmatch(r"D\d+(?:\.\d+)?", v):
                    reserved.add(v)
    except Exception:
        pass

collisions_locked = sorted(artifact_set & locked)
collisions_reserved = sorted(artifact_set & reserved)

if dupes:
    print("check-locked-d-numbers: FAIL duplicate D numbers inside artifact:")
    for d in dupes:
        print(f"  - {d}")
    sys.exit(1)

if collisions_locked or collisions_reserved:
    print("check-locked-d-numbers: FAIL collisions detected")
    for d in collisions_locked:
        print(f"  - locked collision: {d}")
    for d in collisions_reserved:
        print(f"  - reserved collision: {d}")
    sys.exit(1)

print(f"check-locked-d-numbers: OK ({len(artifact_set)} D-number references checked)")
PY
