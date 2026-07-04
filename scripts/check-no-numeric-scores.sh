#!/usr/bin/env bash
# Chunk 28 D217 — no confidence numerals in viewer/inspector DOM fixtures.
# D217.3 exemption — skip DOM subtrees marked with
# data-serialized-context-verbatim="true" (SerializedContextViewer's panel).
#
# Usage:
#   bash scripts/check-no-numeric-scores.sh                 # default scan
#   bash scripts/check-no-numeric-scores.sh path1 path2 ... # explicit paths

set -euo pipefail

cd "$(dirname "$0")/.."

# By default scan nothing real — the production defense is the TS code,
# enforced via component tests (D217). This script gains teeth when fixture
# paths are passed explicitly (by the CI guard test suite).
if [ "$#" -gt 0 ]; then
  TARGETS=("$@")
else
  # Empty default: the test suite drives this script with fixture files.
  TARGETS=()
fi

export TARGETS_JOINED
TARGETS_JOINED=$(printf '%s\n' "${TARGETS[@]-}" | tr '\n' '|')

python3 - <<'PY'
import os
import re
import sys
from pathlib import Path

targets_raw = os.environ.get("TARGETS_JOINED", "")
targets = [Path(p) for p in targets_raw.split("|") if p]

forbidden = [
    "extraction_confidence",
    "relationship_confidence",
    "span_confidence",
    "rrf_score",
    "rerank_score",
]

exempt_pat = re.compile(
    r'<([A-Za-z][A-Za-z0-9-]*)\b[^>]*data-serialized-context-verbatim="true"[^>]*>.*?</\1>',
    re.S,
)


def scan_file(f: Path) -> int:
    text = f.read_text(encoding="utf-8")
    text = exempt_pat.sub("", text)
    errs = 0
    for field in forbidden:
        pat = re.compile(
            rf"{re.escape(field)}[^<>]*?(\d+\.\d+|\d+%|\b\d{{1,3}}\b)"
        )
        if pat.search(text):
            print(f"{f}: {field} numeral found")
            errs += 1
    return errs


errors = 0
for t in targets:
    if t.is_dir():
        for f in t.rglob("*.html"):
            errors += scan_file(f)
    elif t.is_file():
        errors += scan_file(t)

if errors > 0:
    print(f"check-no-numeric-scores: {errors} violation(s)")
    sys.exit(1)
print("check-no-numeric-scores: OK")
PY
