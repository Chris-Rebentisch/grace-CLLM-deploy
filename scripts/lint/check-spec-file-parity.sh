#!/usr/bin/env bash
# check-spec-file-parity.sh — verify every file path cited in §6 CP **Files:**
# lines also appears somewhere in §2 (Created or Edited sections).
#
# Usage: bash scripts/lint/check-spec-file-parity.sh <spec-path>
# Exit 0 on parity, exit 1 with diagnostics on mismatch.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <spec-path>" >&2
  exit 1
fi

SPEC="$1"

if [[ ! -f "$SPEC" ]]; then
  echo "ERROR: spec file not found: $SPEC" >&2
  exit 1
fi

# Extract §2 block (from "## 2." up to "## 3.")
SECTION2=$(sed -n '/^## 2\./,/^## 3\./p' "$SPEC")

# Extract all file paths from §6 **Files:** lines.
# These lines look like: **Files:** `path1`; `path2`; `path3`.
# We extract backtick-quoted paths from lines containing "**Files:**"
CP_FILES=$(grep -E '^\*\*Files:\*\*' "$SPEC" \
  | grep -oE '`[^`]+`' \
  | tr -d '`' \
  | sort -u)

if [[ -z "$CP_FILES" ]]; then
  echo "WARNING: no **Files:** lines found in $SPEC — nothing to check." >&2
  exit 0
fi

MISSING=()

while IFS= read -r filepath; do
  [[ -z "$filepath" ]] && continue
  # Check if the path appears anywhere in §2
  if ! echo "$SECTION2" | grep -qF "$filepath"; then
    MISSING+=("$filepath")
  fi
done <<< "$CP_FILES"

if [[ ${#MISSING[@]} -eq 0 ]]; then
  echo "OK: all §6 CP file paths found in §2."
  exit 0
else
  echo "FAIL: ${#MISSING[@]} file path(s) in §6 CPs missing from §2:"
  for m in "${MISSING[@]}"; do
    echo "  - $m"
  done
  exit 1
fi
