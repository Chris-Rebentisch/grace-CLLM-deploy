#!/usr/bin/env bash
set -euo pipefail
psql -d "${PGDATABASE:-grace}" -c "DROP OWNED BY grace_readonly;" -c "DROP ROLE IF EXISTS grace_readonly;"
