#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: bash scripts/lint/check-literal-blocks.sh <spec-path> <prompt-path>" >&2
  exit 2
fi

SPEC="$1"
PROMPT="$2"

python3 - "$SPEC" "$PROMPT" <<'PY'
import re
import sys
from pathlib import Path

spec = Path(sys.argv[1])
prompt = Path(sys.argv[2])

if not spec.exists() or not prompt.exists():
    print("check-literal-blocks: missing input file(s)", file=sys.stderr)
    sys.exit(2)

spec_text = spec.read_text(encoding="utf-8")
prompt_text = prompt.read_text(encoding="utf-8")

block_re = re.compile(r"```(json|yaml|sql)\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
spec_blocks = [(m.group(1).lower(), m.group(2).strip()) for m in block_re.finditer(spec_text)]

missing = []
for lang, body in spec_blocks:
    snippet = f"```{lang}\n{body}\n```"
    if snippet not in prompt_text:
        missing.append((lang, body.splitlines()[0] if body.splitlines() else "<empty>"))

if missing:
    print("check-literal-blocks: FAIL missing verbatim literal blocks in prompt")
    for lang, head in missing:
        print(f"  - {lang}: {head}")
    sys.exit(1)

print(f"check-literal-blocks: OK ({len(spec_blocks)} blocks checked)")
PY
