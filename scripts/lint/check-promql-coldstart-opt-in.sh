#!/usr/bin/env bash
# D459 — Defensive lint: every src/analytics/**/*.py that assembles
# long-window PromQL from baseline_window_days (or calls _to_window())
# must import query_with_coldstart_hint.
#
# Exit 0 if all construction-site files have the import.
# Exit 1 with a list of non-compliant files otherwise.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC_DIR="${REPO_ROOT}/src/analytics"

non_compliant=()

while IFS= read -r -d '' f; do
    base="$(basename "$f")"
    # Exclude config files, inits, and the helper itself
    case "$base" in
        config.py|__init__.py|_prometheus_query_helpers.py) continue ;;
    esac

    # Condition (a): references baseline_window_days or calls _to_window()
    has_baseline=false
    if grep -qE 'baseline_window_days|_to_window\(' "$f" 2>/dev/null; then
        has_baseline=true
    fi
    [ "$has_baseline" = false ] && continue

    # Condition (b): contains a PromQL window-assembly pattern
    has_promql_window=false
    if grep -qE "f'.*\[\\{|_to_window\(" "$f" 2>/dev/null; then
        has_promql_window=true
    fi
    [ "$has_promql_window" = false ] && continue

    # If we get here, the file constructs long-window PromQL.
    # Assert it imports the cold-start helper.
    if ! grep -qE 'from src\.analytics\._prometheus_query_helpers import|import _prometheus_query_helpers' "$f" 2>/dev/null; then
        non_compliant+=("$f")
    fi
done < <(find "$SRC_DIR" -name '*.py' -print0)

if [ ${#non_compliant[@]} -gt 0 ]; then
    echo "FAIL: The following files assemble long-window PromQL but do not import query_with_coldstart_hint:"
    for f in "${non_compliant[@]}"; do
        echo "  - $f"
    done
    exit 1
fi

echo "OK: All long-window PromQL construction sites import query_with_coldstart_hint."
exit 0
