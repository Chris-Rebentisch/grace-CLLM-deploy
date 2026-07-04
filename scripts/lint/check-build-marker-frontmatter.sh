#!/usr/bin/env bash
# scripts/lint/check-build-marker-frontmatter.sh
#
# D483 (Chunk 74): Validate build-complete marker YAML frontmatter schema.
# Schema enforced by this script; see docs/_templates/chunk-build-complete-marker.md.
#
# Usage:
#   bash scripts/lint/check-build-marker-frontmatter.sh <marker-path>
#
# Exit 0 on pass; exit 1 with diagnostic on stderr.
set -uo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/lint/check-build-marker-frontmatter.sh <marker-path>" >&2
  exit 2
fi

MARKER="$1"

if [[ ! -f "$MARKER" ]]; then
  echo "check-build-marker-frontmatter: marker file not found: $MARKER" >&2
  exit 1
fi

python3 - "$MARKER" <<'PYEOF'
import sys
import re

marker_path = sys.argv[1]

try:
    text = open(marker_path).read()
except OSError as e:
    print(f"check-build-marker-frontmatter: cannot read {marker_path}: {e}", file=sys.stderr)
    sys.exit(1)

# Extract frontmatter.
fm_match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, flags=re.DOTALL)
if not fm_match:
    # No frontmatter — legacy marker. Skip lint (behavioral bypass).
    print("check-build-marker-frontmatter: no frontmatter found (legacy marker, skipping)")
    sys.exit(0)

body = fm_match.group(1)

try:
    import yaml
except ImportError:
    print("check-build-marker-frontmatter: PyYAML not installed, cannot lint", file=sys.stderr)
    sys.exit(1)

try:
    parsed = yaml.safe_load(body)
except Exception as e:
    print(f"check-build-marker-frontmatter: YAML parse error: {e}", file=sys.stderr)
    sys.exit(1)

if not isinstance(parsed, dict):
    print(f"check-build-marker-frontmatter: frontmatter is not a mapping (got {type(parsed).__name__})", file=sys.stderr)
    sys.exit(1)

errors = []

# Required fields.
REQUIRED = {"chunk", "status", "exit_signal", "blocker_kind"}
for field in sorted(REQUIRED):
    if field not in parsed:
        errors.append(f"missing required field: {field}")

# Enum validation.
VALID_STATUS = {"partial", "complete", "blocked", "incomplete"}
status = parsed.get("status")
if status is not None and str(status).strip().lower() not in VALID_STATUS:
    errors.append(f"invalid status: '{status}' (must be one of {sorted(VALID_STATUS)})")

VALID_EXIT_SIGNAL = {"stop", "continue", "escalate"}
es = parsed.get("exit_signal")
if es is not None and str(es).strip().lower() not in VALID_EXIT_SIGNAL:
    errors.append(f"invalid exit_signal: '{es}' (must be one of {sorted(VALID_EXIT_SIGNAL)})")

VALID_BLOCKER_KIND = {"null", "architectural_divergence", "infrastructure", "spec_defect"}
bk = parsed.get("blocker_kind")
if bk is not None:
    bk_str = str(bk).strip().lower()
    if bk_str not in VALID_BLOCKER_KIND and bk_str not in ("none", "~"):
        errors.append(f"invalid blocker_kind: '{bk}' (must be one of {sorted(VALID_BLOCKER_KIND)} or null/none)")

# cps mapping shape.
cps = parsed.get("cps")
if cps is not None and not isinstance(cps, dict):
    errors.append(f"cps must be a mapping (got {type(cps).__name__})")
elif isinstance(cps, dict):
    for k, v in cps.items():
        if not isinstance(v, dict):
            errors.append(f"cps.{k} must be a mapping (got {type(v).__name__})")

# cumulative_tests mapping shape.
ct = parsed.get("cumulative_tests")
if ct is not None and not isinstance(ct, dict):
    errors.append(f"cumulative_tests must be a mapping (got {type(ct).__name__})")

if errors:
    print(f"check-build-marker-frontmatter: {len(errors)} error(s) in {marker_path}:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print(f"check-build-marker-frontmatter: OK ({marker_path})")
sys.exit(0)
PYEOF
