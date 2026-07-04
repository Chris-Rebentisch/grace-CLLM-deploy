#!/usr/bin/env bash
# Safe pytest wrapper — enforces the GrACE test-DB isolation doctrine.
#
# The grace `conftest.py` auto-redirects the test process to the grace_test
# sibling DB at import time, so a normal `pytest tests/` can NEVER reach the
# `grace` GOLD corpus. This wrapper makes that explicit and refuses obviously
# unsafe DATABASE_URLs, then runs the default (non-perf, non-smoke) selection.
#
# Usage:
#   ./safe_pytest.sh                      # full safe suite
#   ./safe_pytest.sh tests/discovery -v   # scoped
#
# One-time grace_test setup (see testing-protocol skill):
#   createdb grace_test
#   DATABASE_URL=postgresql+psycopg2://$USER@localhost:5432/grace_test alembic upgrade head
set -euo pipefail

GRACE_ROOT="${GRACE_ROOT:-$HOME/grace}"
cd "$GRACE_ROOT"

# Refuse to run if someone hard-pointed DATABASE_URL at a protected DB.
if [[ "${DATABASE_URL:-}" =~ (prod|production|gold|live) ]]; then
  echo "[safe_pytest] REFUSING: DATABASE_URL points at a protected database." >&2
  exit 78
fi

PY="${GRACE_ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

echo "[safe_pytest] grace_test isolation active (conftest auto-redirect). Running..."
exec "$PY" -m pytest "$@"
