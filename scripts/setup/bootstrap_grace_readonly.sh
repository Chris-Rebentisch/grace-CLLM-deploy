#!/usr/bin/env bash
# bootstrap_grace_readonly.sh — Idempotent creation of the grace_readonly
# PostgreSQL role with GRANTs matching docs/security-posture.md §8.
#
# References: D167 (original role design), D456 (automation), security-posture.md §8.
#
# Usage:
#   GRAFANA_POSTGRES_PASSWORD=<secret> bash scripts/setup/bootstrap_grace_readonly.sh
#   GRAFANA_POSTGRES_PASSWORD=<secret> bash scripts/setup/bootstrap_grace_readonly.sh --check
#
# Honors PGUSER/PGPASSWORD/PGHOST/PGPORT/PGDATABASE env vars (psql reads natively).

set -euo pipefail

# --- Password guard ---
if [ -z "${GRAFANA_POSTGRES_PASSWORD:-}" ] || [ "$GRAFANA_POSTGRES_PASSWORD" = "CHANGEME" ]; then
  echo "ERROR: GRAFANA_POSTGRES_PASSWORD must be set and not equal to 'CHANGEME'." >&2
  echo "Set it in .env or export it before running this script." >&2
  exit 1
fi

DB="${PGDATABASE:-grace}"

# --- --check mode ---
if [ "${1:-}" = "--check" ]; then
  echo "Checking grace_readonly role and grants..."

  ROLE_EXISTS=$(psql -d "$DB" -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly';" 2>/dev/null || echo "")
  if [ "$ROLE_EXISTS" != "1" ]; then
    echo "FAIL: Role 'grace_readonly' does not exist." >&2
    exit 1
  fi

  EXPECTED_TABLES=(
    extraction_events_pg
    entity_resolution_log
    mine_samples
    ontology_versions
    schema_proposals
    schema_promotion_events
    calibration_records
    review_sessions
    review_decisions
    change_of_status_events
    cq_test_runs
    competency_questions
    cq_clusters
    schema_extraction_runs
    schema_merge_runs
    merge_runs
    processed_documents
    kill_switch_history
  )

  MISSING=()
  for tbl in "${EXPECTED_TABLES[@]}"; do
    HAS_GRANT=$(psql -d "$DB" -tAc \
      "SELECT 1 FROM information_schema.role_table_grants
       WHERE grantee = 'grace_readonly'
         AND table_name = '$tbl'
         AND privilege_type = 'SELECT';" 2>/dev/null || echo "")
    if [ "$HAS_GRANT" != "1" ]; then
      MISSING+=("$tbl")
    fi
  done

  if [ ${#MISSING[@]} -gt 0 ]; then
    echo "FAIL: Missing SELECT grants on: ${MISSING[*]}" >&2
    exit 1
  fi

  echo "OK: grace_readonly role exists with all 18 expected grants."
  exit 0
fi

# --- Apply mode ---
echo "Bootstrapping grace_readonly role..."

# Idempotent role creation — check first, then CREATE ROLE via heredoc
# so psql variable interpolation (:'password') works correctly.
ROLE_EXISTS=$(psql -d "$DB" -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly';" 2>/dev/null || echo "")
if [ "$ROLE_EXISTS" != "1" ]; then
  psql -d "$DB" -v password="$GRAFANA_POSTGRES_PASSWORD" <<'CREATEROLE'
CREATE ROLE grace_readonly LOGIN PASSWORD :'password';
CREATEROLE
  echo "Role grace_readonly created."
else
  echo "Role grace_readonly already exists, skipping creation."
fi

psql -d "$DB" <<'SQL'
GRANT CONNECT ON DATABASE grace TO grace_readonly;
GRANT USAGE ON SCHEMA public TO grace_readonly;
GRANT SELECT ON extraction_events_pg TO grace_readonly;
GRANT SELECT ON entity_resolution_log TO grace_readonly;
GRANT SELECT ON mine_samples TO grace_readonly;
GRANT SELECT ON ontology_versions TO grace_readonly;
GRANT SELECT ON schema_proposals TO grace_readonly;
GRANT SELECT ON schema_promotion_events TO grace_readonly;
GRANT SELECT ON calibration_records TO grace_readonly;
GRANT SELECT ON review_sessions TO grace_readonly;
GRANT SELECT ON review_decisions TO grace_readonly;
GRANT SELECT ON change_of_status_events TO grace_readonly;
GRANT SELECT ON cq_test_runs TO grace_readonly;
GRANT SELECT ON competency_questions TO grace_readonly;
GRANT SELECT ON cq_clusters TO grace_readonly;
GRANT SELECT ON schema_extraction_runs TO grace_readonly;
GRANT SELECT ON schema_merge_runs TO grace_readonly;
GRANT SELECT ON merge_runs TO grace_readonly;
GRANT SELECT ON processed_documents TO grace_readonly;
GRANT SELECT ON kill_switch_history TO grace_readonly;
SQL

echo "Done. grace_readonly role bootstrapped with 18 GRANT SELECT statements."
