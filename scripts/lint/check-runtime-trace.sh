#!/usr/bin/env bash
# Wrapper for check_runtime_trace.py (spec-stage runtime trace lint).
# Usage: bash scripts/lint/check-runtime-trace.sh <spec-path>
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec python3 "$REPO_ROOT/scripts/lint/check_runtime_trace.py" "$@"
