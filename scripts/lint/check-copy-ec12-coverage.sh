#!/usr/bin/env bash
# EC-12 copy-registry coverage guard (Chunk 46, D378.g).
#
# Asserts every frontend/lib/*/copy.ts has a corresponding EC-12 or
# copy-governance test file under frontend/tests/.
#
# Exit 0 on success, exit 1 with diagnostic on missing coverage.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COPY_DIR="$ROOT/frontend/lib"
TEST_DIR="$ROOT/frontend/tests"
MISSING=()

for copy_file in "$COPY_DIR"/*/copy.ts; do
    [ -f "$copy_file" ] || continue
    # Extract the module name (e.g., "permissions" from "frontend/lib/permissions/copy.ts").
    module="$(basename "$(dirname "$copy_file")")"

    # Look for a matching test file. Accepted patterns:
    #   tests/<module>/*copy*EC12*   (e.g., tests/sensitivity/copy-EC12.test.ts)
    #   tests/*<module>*copy*        (e.g., tests/permissions-copy-EC12.test.ts)
    #   tests/*<module>*EC12*        (e.g., tests/permissions-copy-EC12.test.ts)
    found=0

    # Pattern 1: tests/<module>/*copy*EC12* or *ec12*
    if compgen -G "$TEST_DIR/$module/"*copy* > /dev/null 2>&1 || \
       compgen -G "$TEST_DIR/$module/"*EC12* > /dev/null 2>&1 || \
       compgen -G "$TEST_DIR/$module/"*ec12* > /dev/null 2>&1; then
        found=1
    fi

    # Pattern 2: tests/*<module>*copy* (flat layout)
    if [ "$found" -eq 0 ]; then
        if compgen -G "$TEST_DIR/"*"$module"*copy* > /dev/null 2>&1 || \
           compgen -G "$TEST_DIR/"*"$module"*EC12* > /dev/null 2>&1 || \
           compgen -G "$TEST_DIR/"*"$module"*ec12* > /dev/null 2>&1; then
            found=1
        fi
    fi

    if [ "$found" -eq 0 ]; then
        MISSING+=("$module (copy file: $copy_file)")
    fi
done

if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "ERROR: EC-12 copy-governance test missing for:"
    for m in "${MISSING[@]}"; do
        echo "  - $m"
    done
    echo ""
    echo "Each frontend/lib/*/copy.ts must have a corresponding test file"
    echo "under frontend/tests/ matching *<module>*copy* or *<module>*EC12*."
    exit 1
fi

echo "OK: All $(find "$COPY_DIR" -name 'copy.ts' 2>/dev/null | wc -l | tr -d ' ') copy.ts files have EC-12 test coverage."
exit 0
