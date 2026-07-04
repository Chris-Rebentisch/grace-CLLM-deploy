#!/usr/bin/env bash
# scripts/lint/check-spec-ac1-wording.sh
#
# D484 (Chunk 74): AC1 wording lint — validates spec AC1 phrasing against
# an allowlist of approved wordings. Prevents the chunk-72b PASS_WITH_DEVIATIONS
# infinite-loop root cause.
#
# Usage:
#   bash scripts/lint/check-spec-ac1-wording.sh <spec-path>
#
# Exit 0 on approved phrasing; exit 1 on rejected phrasing or missing AC1.
set -uo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/lint/check-spec-ac1-wording.sh <spec-path>" >&2
  exit 2
fi

SPEC="$1"

if [[ ! -f "$SPEC" ]]; then
  echo "check-spec-ac1-wording: spec file not found: $SPEC" >&2
  exit 1
fi

# Extract the first acceptance criterion (AC1) from the spec.
# AC1 is typically the first numbered item under ## 12. Acceptance Criteria
# or similar heading.
AC1_BLOCK=$(python3 -c "
import re, sys

text = open(sys.argv[1]).read()

# Find the Acceptance section — typically ## 12. or ## Acceptance
# or numbered list starting with '1.' after such a heading.
# Look for patterns like: '1. Full-suite regression' or '- [ ] 1.'
patterns = [
    # '1. Full-suite regression: ...'
    r'(?m)^[-*]?\s*\[?\s*[x ]?\s*\]?\s*1\.\s*(Full.suite.*)',
    # '- [ ] 1. Full-suite regression: ...'
    r'(?m)^[-*]\s*\[\s*[x ]?\s*\]\s*1\.\s*(.*regression.*)',
    # 'AC1' explicit mention
    r'(?m)^.*\bAC1\b.*?[:—]\s*(.*)',
]

for pat in patterns:
    m = re.search(pat, text, re.IGNORECASE)
    if m:
        print(m.group(1).strip()[:300])
        sys.exit(0)

# Fallback: just extract line containing 'Full-suite regression'
m = re.search(r'(?m)^.*Full.suite regression.*$', text, re.IGNORECASE)
if m:
    print(m.group(0).strip()[:300])
    sys.exit(0)

sys.exit(1)
" "$SPEC" 2>/dev/null)

if [[ $? -ne 0 ]] || [[ -z "$AC1_BLOCK" ]]; then
  echo "check-spec-ac1-wording: AC1 section not found in $SPEC" >&2
  exit 1
fi

# Allowlist of approved phrasings (case-insensitive substring match).
# These are the canonical forms from chunk-68, chunk-71, and chunk-73+ specs.
APPROVED_PATTERNS=(
  "passes with >= "
  "passes with >=~"
  "passes with >="
  "python tests"
  "zero new failures"
)

# Rejected patterns — the chunk-72b literal that caused the PWD incident.
REJECTED_PATTERNS=(
  "exactly 3362"
  "exactly 3384"
  "passes with exactly"
  "equal to 3362"
  "must equal"
)

# Check rejected patterns first.
for pat in "${REJECTED_PATTERNS[@]}"; do
  if echo "$AC1_BLOCK" | grep -qi "$pat"; then
    echo "check-spec-ac1-wording: REJECTED — AC1 contains forbidden wording: '$pat'" >&2
    echo "  AC1: $AC1_BLOCK" >&2
    echo "  See chunk-72b PWD incident. Use '>= N' form instead of 'exactly N'." >&2
    exit 1
  fi
done

# Check approved patterns.
MATCH_COUNT=0
for pat in "${APPROVED_PATTERNS[@]}"; do
  if echo "$AC1_BLOCK" | grep -qi "$pat"; then
    MATCH_COUNT=$((MATCH_COUNT + 1))
  fi
done

if [[ "$MATCH_COUNT" -eq 0 ]]; then
  echo "check-spec-ac1-wording: REJECTED — AC1 does not match any approved phrasing" >&2
  echo "  AC1: $AC1_BLOCK" >&2
  echo "  Approved patterns: ${APPROVED_PATTERNS[*]}" >&2
  exit 1
fi

echo "check-spec-ac1-wording: OK ($SPEC)"
exit 0
