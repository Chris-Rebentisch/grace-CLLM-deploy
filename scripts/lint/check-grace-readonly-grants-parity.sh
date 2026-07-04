#!/usr/bin/env bash
# check-grace-readonly-grants-parity.sh — CI lint: verifies GRANT statements
# in bootstrap_grace_readonly.sh match docs/security-posture.md §8 fenced SQL block.
#
# Reference: D456.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/setup/bootstrap_grace_readonly.sh"
DOC="$REPO_ROOT/docs/security-posture.md"

if [ ! -f "$SCRIPT" ]; then
  echo "ERROR: $SCRIPT not found." >&2
  exit 1
fi
if [ ! -f "$DOC" ]; then
  echo "ERROR: $DOC not found." >&2
  exit 1
fi

# Extract GRANT lines from the bootstrap script (normalize whitespace)
SCRIPT_GRANTS=$(grep '^GRANT ' "$SCRIPT" | sed 's/[[:space:]]*$//' | sort)

# Extract the fenced SQL block from security-posture.md §8
# The block is between ```sql and ``` markers in the §8 section
DOC_GRANTS=$(awk '/^```sql$/,/^```$/' "$DOC" | grep '^GRANT ' | sed 's/[[:space:]]*$//' | sort)

if [ -z "$SCRIPT_GRANTS" ]; then
  echo "ERROR: No GRANT lines found in $SCRIPT" >&2
  exit 1
fi

if [ -z "$DOC_GRANTS" ]; then
  echo "ERROR: No GRANT lines found in §8 SQL block of $DOC" >&2
  exit 1
fi

DIFF_OUTPUT=$(diff <(echo "$SCRIPT_GRANTS") <(echo "$DOC_GRANTS") || true)

if [ -n "$DIFF_OUTPUT" ]; then
  echo "FAIL: GRANT parity mismatch between bootstrap script and security-posture.md §8." >&2
  echo "" >&2
  echo "--- bootstrap_grace_readonly.sh" >&2
  echo "+++ security-posture.md §8 block" >&2
  echo "$DIFF_OUTPUT" >&2
  exit 1
fi

echo "OK: GRANT statements match between bootstrap script and security-posture.md §8."
exit 0
