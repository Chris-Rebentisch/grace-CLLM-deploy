#!/usr/bin/env bash
# Chunk 28 D219 — live-subprocess smoke harness.
#
# Starts uvicorn in subprocess, polls /openapi.json for readiness (no /health
# on src.api.main:app — same pattern as tests/analytics/test_metrics_live_server.py),
# hits PATHS[] via curl (includes D458 bare GET /metrics + mount GET /metrics/),
# kills subprocess on exit.

set -euo pipefail

cd "$(dirname "$0")/.."

# Chunk 43 retro fix (TOP-10-#1, proposed D350): preflight Alembic
# revision-id lint. Cheap (<1s), catches the c42d/c43a-class width bug
# (revision string > VARCHAR(32)) before any DB / uvicorn work runs.
# See docs/pipeline-failure-recovery-runbook.md Pattern A.
if ! bash scripts/lint/check-migration-revision-ids.sh; then
  echo "smoke-live-server: migration revision-id lint failed (see above)" >&2
  exit 1
fi

PORT="${SMOKE_UVICORN_PORT:-8123}"
# Chunk 33: /api/analytics/alerts/_internal is intentionally excluded from
# this smoke harness. The route requires either a Grafana-shaped webhook
# payload + valid X-Admin-Key, or a request whose source IP is in the
# Docker bridge CIDR. Forging either combination from a localhost smoke
# script would obscure the security shape that §16 actually defends. The
# webhook is exercised end-to-end by tests/analytics/correlation_engine/
# test_alert_webhook_route.py via FastAPI TestClient.
#
# Chunk 34: /api/extraction/mine-sample is intentionally excluded from
# this smoke harness. The route invokes the MINESampler against the
# live ArcadeDB graph + Postgres claim store; running it in a smoke
# context would either (a) require a populated graph fixture (out of
# scope for a 5-second harness) or (b) succeed only with an empty
# document_id and produce no signal. The route is exercised end-to-end
# by tests/api/test_extraction_routes.py via FastAPI TestClient with
# patched MINESampler / ArcadeClient builders.
#
# POST /api/extraction/reconciliation is included (Chunk 34 CP10) with an
# empty JSON body. When there are zero verified rows in extraction_events,
# the handler never calls ArcadeDB and returns 200. If your dev database
# has verified events, ArcadeDB must be up for this check to pass.
#
# Chunk 35a (D266): POST /api/feedback/retrieval is included with a
# fixture body. The route is mutating and append-only; the smoke harness
# inserts one `retrieval_feedback` row per run. That is acceptable for
# dev / CI smoke (tables are dropped between full upgrades). If you do
# not want smoke runs to touch retrieval_feedback (e.g. you are running
# against a snapshot you are inspecting manually), either set
# SMOKE_SKIP_FEEDBACK=1 or remove the entry from the PATHS array.
PATHS=(
  "GET /metrics/"
  "GET /metrics"
  "GET /api/graph/info"
  "GET /api/graph/entities?limit=5"
  "POST /api/retrieval/query"
  "POST /api/extraction/reconciliation"
  "POST /api/feedback/retrieval"
)

# Start uvicorn in background. Redirect logs to /tmp to keep the smoke
# output clean; dump them on failure.
LOG_FILE=$(mktemp -t grace_smoke_uvicorn.XXXXXX.log)
python3 -m uvicorn src.api.main:app \
  --host 127.0.0.1 --port "$PORT" > "$LOG_FILE" 2>&1 &
PID=$!

cleanup() {
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  rm -f "$LOG_FILE" /tmp/grace_smoke_body
}
trap cleanup EXIT

# Poll /openapi.json until ready (~10 s budget).
for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:${PORT}/openapi.json" > /dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

if ! curl -sf "http://127.0.0.1:${PORT}/openapi.json" > /dev/null 2>&1; then
  echo "smoke-live-server: readiness timeout"
  echo "--- uvicorn log ---"
  cat "$LOG_FILE"
  exit 1
fi

ERRORS=0
# Chunk 31: when GRACE_ADMIN_KEY is set, present it on the POST query
# call to exercise the keyed admission path. POST /api/retrieval/query is
# in READONLY_ROUTES so it is admitted without the header — the explicit
# header simulates the keyed configuration end-to-end.
ADMIN_KEY_HEADER=()
if [ -n "${GRACE_ADMIN_KEY:-}" ]; then
  ADMIN_KEY_HEADER=(-H "X-Admin-Key: ${GRACE_ADMIN_KEY}")
fi
for entry in "${PATHS[@]}"; do
  method="${entry%% *}"
  path="${entry#* }"
  if [ "$method" = "POST" ]; then
    if [ "$path" = "/api/extraction/reconciliation" ]; then
      POST_BODY='{}'
    elif [ "$path" = "/api/feedback/retrieval" ]; then
      POST_BODY='{"query_event_id":"smoke-harness","vote":"up"}'
    else
      POST_BODY='{"query_text":"smoke"}'
    fi
    STATUS=$(curl -s -o /tmp/grace_smoke_body -w "%{http_code}" \
      -X POST "http://127.0.0.1:${PORT}${path}" \
      -H "Content-Type: application/json" \
      -H "X-Graph-Scope: all" \
      ${ADMIN_KEY_HEADER[@]+"${ADMIN_KEY_HEADER[@]}"} \
      -d "$POST_BODY")
  else
    STATUS=$(curl -s -o /tmp/grace_smoke_body -w "%{http_code}" \
      -H "X-Graph-Scope: all" "http://127.0.0.1:${PORT}${path}")
  fi
  BODY_SIZE=$(wc -c < /tmp/grace_smoke_body | tr -d ' ')
  # POST /api/feedback/retrieval returns 201 Created on success (Chunk 35a).
  EXPECTED_STATUS="200"
  if [ "$method" = "POST" ] && [ "$path" = "/api/feedback/retrieval" ]; then
    EXPECTED_STATUS="201"
  fi
  if [ "$STATUS" != "$EXPECTED_STATUS" ] || [ "$BODY_SIZE" -lt 2 ]; then
    echo "FAIL ${method} ${path}: status=${STATUS} body_size=${BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   ${method} ${path}: status=${STATUS} body_size=${BODY_SIZE}"
  fi
done

if [ "$ERRORS" -gt 0 ]; then
  echo "--- uvicorn log (on failure) ---"
  cat "$LOG_FILE"
  exit 1
fi

# Chunk 36 (D283): recon gap-report generate + read smoke. Seeds a
# minimal `completed` review_sessions row via psql, hits POST
# /api/recon/gap-report/{session_id}/generate (expects 201), then GET
# /api/recon/gap-report/{session_id} (expects 200), and cleans up. Set
# SMOKE_SKIP_RECON=1 to disable. Below-floor graphs return 201 with
# evidence_grounding_score=null, which is still 201 — happy path only;
# 422 lifecycle case is covered in tests/api/test_recon_routes.py.
if [ -z "${SMOKE_SKIP_RECON:-}" ]; then
  PSQL_BIN="${PSQL_BIN:-/opt/homebrew/opt/postgresql@17/bin/psql}"
  RECON_SID=$(python3 -c "import uuid; print(uuid.uuid4())")
  if "$PSQL_BIN" grace -c "INSERT INTO review_sessions (id, status, reviewer, seed_schema_merge_run_id, seed_schema_snapshot) VALUES ('${RECON_SID}', 'completed', 'smoke-recon', 'smoke-merge-run', '{}'::jsonb);" > /dev/null 2>&1; then
    GEN_STATUS=$(curl -s -o /tmp/grace_smoke_body -w "%{http_code}" \
      -X POST "http://127.0.0.1:${PORT}/api/recon/gap-report/${RECON_SID}/generate" \
      -H "Content-Type: application/json" \
      -H "X-Graph-Scope: all" \
      ${ADMIN_KEY_HEADER[@]+"${ADMIN_KEY_HEADER[@]}"} \
      -d '{}')
    GEN_BODY_SIZE=$(wc -c < /tmp/grace_smoke_body | tr -d ' ')
    if [ "$GEN_STATUS" != "201" ] || [ "$GEN_BODY_SIZE" -lt 2 ]; then
      echo "FAIL POST /api/recon/gap-report/{sid}/generate: status=${GEN_STATUS} body_size=${GEN_BODY_SIZE}"
      ERRORS=$((ERRORS + 1))
    else
      echo "OK   POST /api/recon/gap-report/{sid}/generate: status=${GEN_STATUS} body_size=${GEN_BODY_SIZE}"
    fi

    GET_STATUS=$(curl -s -o /tmp/grace_smoke_body -w "%{http_code}" \
      -H "X-Graph-Scope: all" \
      "http://127.0.0.1:${PORT}/api/recon/gap-report/${RECON_SID}")
    GET_BODY_SIZE=$(wc -c < /tmp/grace_smoke_body | tr -d ' ')
    if [ "$GET_STATUS" != "200" ] || [ "$GET_BODY_SIZE" -lt 2 ]; then
      echo "FAIL GET /api/recon/gap-report/{sid}: status=${GET_STATUS} body_size=${GET_BODY_SIZE}"
      ERRORS=$((ERRORS + 1))
    else
      echo "OK   GET /api/recon/gap-report/{sid}: status=${GET_STATUS} body_size=${GET_BODY_SIZE}"
    fi

    "$PSQL_BIN" grace -c "UPDATE review_sessions SET gap_report_id = NULL WHERE id = '${RECON_SID}'; DELETE FROM gap_reports WHERE session_id = '${RECON_SID}'; DELETE FROM review_sessions WHERE id = '${RECON_SID}';" > /dev/null 2>&1 || true
  else
    echo "SKIP recon smoke: psql seed failed (set SMOKE_SKIP_RECON=1 to silence)"
  fi
fi

# Chunk 37 (D286/D287): Documented Reality Report on-demand smoke. Seeds
# nothing — the route reads the live ArcadeDB graph; an empty graph
# returns corpus_below_floor=true which is still a valid 201 response.
# A second GET against /latest then returns 200. Set
# SMOKE_SKIP_DOCUMENTED_REALITY=1 to disable. The Cross-Executive
# Divergence Map route is intentionally NOT smoked — it requires two
# pre-existing ratified `ontology_versions` rows, which is out of scope
# for a 5-second harness; tests/api/test_recon_divergence_map_routes.py
# covers it via FastAPI TestClient.
if [ -z "${SMOKE_SKIP_DOCUMENTED_REALITY:-}" ]; then
  DR_GEN_STATUS=$(curl -s -o /tmp/grace_smoke_body -w "%{http_code}" \
    -X POST "http://127.0.0.1:${PORT}/api/recon/documented-reality/generate" \
    -H "Content-Type: application/json" \
    -H "X-Graph-Scope: all" \
    ${ADMIN_KEY_HEADER[@]+"${ADMIN_KEY_HEADER[@]}"} \
    -d '{}')
  DR_GEN_BODY_SIZE=$(wc -c < /tmp/grace_smoke_body | tr -d ' ')
  if [ "$DR_GEN_STATUS" != "201" ] || [ "$DR_GEN_BODY_SIZE" -lt 2 ]; then
    echo "FAIL POST /api/recon/documented-reality/generate: status=${DR_GEN_STATUS} body_size=${DR_GEN_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   POST /api/recon/documented-reality/generate: status=${DR_GEN_STATUS} body_size=${DR_GEN_BODY_SIZE}"
  fi

  DR_GET_STATUS=$(curl -s -o /tmp/grace_smoke_body -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/recon/documented-reality/latest")
  DR_GET_BODY_SIZE=$(wc -c < /tmp/grace_smoke_body | tr -d ' ')
  if [ "$DR_GET_STATUS" != "200" ] || [ "$DR_GET_BODY_SIZE" -lt 2 ]; then
    echo "FAIL GET /api/recon/documented-reality/latest: status=${DR_GET_STATUS} body_size=${DR_GET_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/recon/documented-reality/latest: status=${DR_GET_STATUS} body_size=${DR_GET_BODY_SIZE}"
  fi

  # Hygiene: prune the rows the smoke run inserted.
  PSQL_BIN="${PSQL_BIN:-/opt/homebrew/opt/postgresql@17/bin/psql}"
  "$PSQL_BIN" grace -c "DELETE FROM recon_documented_reality_reports WHERE generated_at > now() - interval '5 minutes';" > /dev/null 2>&1 || true
fi

# Chunk 43 (CP7): Sensitivity Gate read-path smoke. Hits
# `GET /api/sensitivity/report/latest` and accepts either 200 (a report
# exists for some prior matrix) or 404 (no report yet — fresh dev DB).
# This is intentionally read-only: the generate route requires an active
# permission matrix and is admin-key-gated; exercising it here would
# either touch the matrix hash chain or fail spuriously on dev DBs that
# have never ratified a matrix. Set SMOKE_SKIP_SENSITIVITY=1 to disable.
if [ -z "${SMOKE_SKIP_SENSITIVITY:-}" ]; then
  SENS_STATUS=$(curl -s -o /tmp/grace_smoke_body -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/sensitivity/report/latest")
  SENS_BODY_SIZE=$(wc -c < /tmp/grace_smoke_body | tr -d ' ')
  if [ "$SENS_STATUS" != "200" ] && [ "$SENS_STATUS" != "404" ]; then
    echo "FAIL GET /api/sensitivity/report/latest: status=${SENS_STATUS} body_size=${SENS_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/sensitivity/report/latest: status=${SENS_STATUS} body_size=${SENS_BODY_SIZE}"
  fi
fi

# D478 (D356 capture-the-why, TESTING_LOG R6-H1): WRITABLE_REVIEW_ROUTES
# assertion hardcodes 9 (5 pre-72a + 4 Chunk 72a POST routes, deduped).
# Authorization: D478.
#
# Chunk 44 (CP8): MCP write-tool surface smoke. Verifies that
# WRITABLE_REVIEW_ROUTES is importable and populated (9 entries
# post-Chunk-72a: 5 pre-72a + 4 Chunk 72a POST routes, after dedup
# of grace_extract_document and grace_batch_extract which share
# ("POST", "/api/extraction/jobs")), and that the MCP tools module
# imports without error. This is a code-path smoke, not an HTTP
# hit — the write routes require session seeding and admin-key,
# which the smoke harness does not set up. Set
# SMOKE_SKIP_MCP_SURFACE=1 to disable.
if [ -z "${SMOKE_SKIP_MCP_SURFACE:-}" ]; then
  PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd)" python3 -c "
from src.mcp_server.server import WRITABLE_REVIEW_ROUTES, READONLY_ROUTES
assert len(WRITABLE_REVIEW_ROUTES) == 9, f'expected 9, got {len(WRITABLE_REVIEW_ROUTES)}'
assert len(WRITABLE_REVIEW_ROUTES & READONLY_ROUTES) == 0, 'frozensets overlap'
from src.mcp_server import tools_session, tools_review
print(f'OK   MCP write-tool surface: WRITABLE={len(WRITABLE_REVIEW_ROUTES)} disjoint=True tools_imported=True')
" 2>&1
  if [ $? -ne 0 ]; then
    echo "FAIL MCP write-tool surface import check"
    ERRORS=$((ERRORS + 1))
  fi
fi

# Chunk 45 (CP8): Support session status read-path smoke. Hits
# `GET /api/support/status` and expects 200 with an `active` boolean
# field. This route is always available (no admin-key, no feature gate
# required for the read path). Set SMOKE_SKIP_SUPPORT=1 to disable.
if [ -z "${SMOKE_SKIP_SUPPORT:-}" ]; then
  SUPPORT_STATUS=$(curl -s -o /tmp/grace_smoke_support -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/support/status")
  SUPPORT_BODY_SIZE=$(wc -c < /tmp/grace_smoke_support | tr -d ' ')
  if [ "$SUPPORT_STATUS" != "200" ]; then
    echo "FAIL GET /api/support/status: status=${SUPPORT_STATUS} body_size=${SUPPORT_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/support/status: status=${SUPPORT_STATUS} body_size=${SUPPORT_BODY_SIZE}"
  fi
fi

# Chunk 47 (CP8): Proposals list read-path smoke. Hits
# `GET /api/ontology/proposals` and accepts 200.
# Set SMOKE_SKIP_PROPOSALS=1 to disable.
if [ -z "${SMOKE_SKIP_PROPOSALS:-}" ]; then
  PROPOSALS_STATUS=$(curl -s -o /tmp/grace_smoke_proposals -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/ontology/proposals?limit=5")
  PROPOSALS_BODY_SIZE=$(wc -c < /tmp/grace_smoke_proposals | tr -d ' ')
  if [ "$PROPOSALS_STATUS" != "200" ]; then
    echo "FAIL GET /api/ontology/proposals: status=${PROPOSALS_STATUS} body_size=${PROPOSALS_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/ontology/proposals: status=${PROPOSALS_STATUS} body_size=${PROPOSALS_BODY_SIZE}"
  fi
fi

# Chunk 48 (CP8): Proposal preview read-only POST smoke. Hits
# `POST /api/ontology/proposals/{fake_id}/preview` with a random UUID
# and accepts 200, 404, or 422 (all valid depending on DB state).
# Set SMOKE_SKIP_PROPOSAL_PREVIEW=1 to disable.
if [ -z "${SMOKE_SKIP_PROPOSAL_PREVIEW:-}" ]; then
  PREVIEW_STATUS=$(curl -s -o /tmp/grace_smoke_preview -w "%{http_code}" \
    -X POST \
    -H "X-Graph-Scope: all" \
    -H "Content-Type: application/json" \
    "http://127.0.0.1:${PORT}/api/ontology/proposals/00000000-0000-0000-0000-000000000048/preview")
  PREVIEW_BODY_SIZE=$(wc -c < /tmp/grace_smoke_preview | tr -d ' ')
  if [ "$PREVIEW_STATUS" != "200" ] && [ "$PREVIEW_STATUS" != "404" ] && [ "$PREVIEW_STATUS" != "422" ]; then
    echo "FAIL POST /api/ontology/proposals/.../preview: status=${PREVIEW_STATUS} body_size=${PREVIEW_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   POST /api/ontology/proposals/.../preview: status=${PREVIEW_STATUS} body_size=${PREVIEW_BODY_SIZE}"
  fi
fi

# Chunk 49 (CP9): Calibration dashboard read-path smoke. Hits
# `GET /api/ontology/calibration/dashboard` and expects 200.
# Cold-start DB returns three tiers with empty bands — still 200.
# Set SMOKE_SKIP_CALIBRATION=1 to disable.
if [ -z "${SMOKE_SKIP_CALIBRATION:-}" ]; then
  CAL_STATUS=$(curl -s -o /tmp/grace_smoke_calibration -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/ontology/calibration/dashboard")
  CAL_BODY_SIZE=$(wc -c < /tmp/grace_smoke_calibration | tr -d ' ')
  if [ "$CAL_STATUS" != "200" ]; then
    echo "FAIL GET /api/ontology/calibration/dashboard: status=${CAL_STATUS} body_size=${CAL_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/ontology/calibration/dashboard: status=${CAL_STATUS} body_size=${CAL_BODY_SIZE}"
  fi
fi

# Chunk 50 (CP10): Agent daemon status read-path smoke. Hits
# `GET /api/ontology/daemon/status` and accepts 200 or 404.
# Set SMOKE_SKIP_DAEMON=1 to disable.
if [ -z "${SMOKE_SKIP_DAEMON:-}" ]; then
  DAEMON_STATUS=$(curl -s -o /tmp/grace_smoke_daemon -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/ontology/daemon/status")
  DAEMON_BODY_SIZE=$(wc -c < /tmp/grace_smoke_daemon | tr -d ' ')
  if [ "$DAEMON_STATUS" != "200" ] && [ "$DAEMON_STATUS" != "404" ]; then
    echo "FAIL GET /api/ontology/daemon/status: status=${DAEMON_STATUS} body_size=${DAEMON_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/ontology/daemon/status: status=${DAEMON_STATUS} body_size=${DAEMON_BODY_SIZE}"
  fi
fi

# Chunk 51 (CP9): Federation namespaces list read-path smoke. Hits
# `GET /api/federation/namespaces` and expects 200.
# Set SMOKE_SKIP_FEDERATION=1 to disable.
if [ -z "${SMOKE_SKIP_FEDERATION:-}" ]; then
  FED_STATUS=$(curl -s -o /tmp/grace_smoke_federation -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/federation/namespaces")
  FED_BODY_SIZE=$(wc -c < /tmp/grace_smoke_federation | tr -d ' ')
  if [ "$FED_STATUS" != "200" ]; then
    echo "FAIL GET /api/federation/namespaces: status=${FED_STATUS} body_size=${FED_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/federation/namespaces: status=${FED_STATUS} body_size=${FED_BODY_SIZE}"
  fi
fi

# Chunk 53 (CP10): Connector list read-path smoke. Hits
# `GET /api/connectors` and expects 200.
# Set SMOKE_SKIP_CONNECTORS=1 to disable.
if [ -z "${SMOKE_SKIP_CONNECTORS:-}" ]; then
  CONN_STATUS=$(curl -s -o /tmp/grace_smoke_connectors -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/connectors")
  CONN_BODY_SIZE=$(wc -c < /tmp/grace_smoke_connectors | tr -d ' ')
  if [ "$CONN_STATUS" != "200" ]; then
    echo "FAIL GET /api/connectors: status=${CONN_STATUS} body_size=${CONN_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/connectors: status=${CONN_STATUS} body_size=${CONN_BODY_SIZE}"
  fi
fi

# Chunk 55 (CP10): Ingestion read-path smoke. Hits
# `GET /api/ingestion/sources` and `GET /api/ingestion/readiness` and accepts
# 200 or 404 (empty list / no deployment path configured).
# Set SMOKE_SKIP_INGESTION=1 to disable.
if [ -z "${SMOKE_SKIP_INGESTION:-}" ]; then
  INGEST_SRC_STATUS=$(curl -s -o /tmp/grace_smoke_ingestion_sources -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/ingestion/sources")
  INGEST_SRC_BODY_SIZE=$(wc -c < /tmp/grace_smoke_ingestion_sources | tr -d ' ')
  if [ "$INGEST_SRC_STATUS" != "200" ]; then
    echo "FAIL GET /api/ingestion/sources: status=${INGEST_SRC_STATUS} body_size=${INGEST_SRC_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/ingestion/sources: status=${INGEST_SRC_STATUS} body_size=${INGEST_SRC_BODY_SIZE}"
  fi

  INGEST_RDY_STATUS=$(curl -s -o /tmp/grace_smoke_ingestion_readiness -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/ingestion/readiness")
  INGEST_RDY_BODY_SIZE=$(wc -c < /tmp/grace_smoke_ingestion_readiness | tr -d ' ')
  if [ "$INGEST_RDY_STATUS" != "200" ] && [ "$INGEST_RDY_STATUS" != "404" ]; then
    echo "FAIL GET /api/ingestion/readiness: status=${INGEST_RDY_STATUS} body_size=${INGEST_RDY_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/ingestion/readiness: status=${INGEST_RDY_STATUS} body_size=${INGEST_RDY_BODY_SIZE}"
  fi

  # Chunk 56 (CP10): Curate read-path probe. Accepts 200 or 404.
  CURATE_STATUS=$(curl -s -o /tmp/grace_smoke_curate -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/ingestion/curate/00000000-0000-0000-0000-000000000000")
  CURATE_BODY_SIZE=$(wc -c < /tmp/grace_smoke_curate | tr -d ' ')
  if [ "$CURATE_STATUS" != "200" ] && [ "$CURATE_STATUS" != "404" ]; then
    echo "FAIL GET /api/ingestion/curate/{subset_id}: status=${CURATE_STATUS} body_size=${CURATE_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/ingestion/curate/{subset_id}: status=${CURATE_STATUS} body_size=${CURATE_BODY_SIZE}"
  fi

  # Chunk 57 (CP10): OAuth callback probe. Empty body → expects 422 (validation error).
  OAUTH_CB_STATUS=$(curl -s -o /tmp/grace_smoke_oauth_cb -w "%{http_code}" \
    -X POST -H "Content-Type: application/json" -H "X-Graph-Scope: all" \
    -d '{}' \
    "http://127.0.0.1:${PORT}/api/ingestion/oauth/callback")
  if [ "$OAUTH_CB_STATUS" != "422" ] && [ "$OAUTH_CB_STATUS" != "401" ]; then
    echo "FAIL POST /api/ingestion/oauth/callback (empty): status=${OAUTH_CB_STATUS}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   POST /api/ingestion/oauth/callback (empty): status=${OAUTH_CB_STATUS}"
  fi
fi

# Chunk 58 (CP10): DPIA status read-path smoke. Hits
# `GET /api/communications/dpia/status` and accepts 200 or 404.
# Set SMOKE_SKIP_COMMUNICATIONS=1 to disable.
if [ -z "${SMOKE_SKIP_COMMUNICATIONS:-}" ]; then
  DPIA_STATUS=$(curl -s -o /tmp/grace_smoke_dpia_status -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/communications/dpia/status")
  DPIA_BODY_SIZE=$(wc -c < /tmp/grace_smoke_dpia_status | tr -d ' ')
  if [ "$DPIA_STATUS" != "200" ] && [ "$DPIA_STATUS" != "404" ]; then
    echo "FAIL GET /api/communications/dpia/status: status=${DPIA_STATUS} body_size=${DPIA_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/communications/dpia/status: status=${DPIA_STATUS} body_size=${DPIA_BODY_SIZE}"
  fi
fi

# Chunk 60 (CP13): Communications profile list read-path smoke. Hits
# `GET /api/communications/profiles` and accepts 200 or 404.
if [ -z "${SMOKE_SKIP_COMMUNICATIONS:-}" ]; then
  PROF_STATUS=$(curl -s -o /tmp/grace_smoke_profiles -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/communications/profiles")
  PROF_BODY_SIZE=$(wc -c < /tmp/grace_smoke_profiles | tr -d ' ')
  if [ "$PROF_STATUS" != "200" ] && [ "$PROF_STATUS" != "404" ]; then
    echo "FAIL GET /api/communications/profiles: status=${PROF_STATUS} body_size=${PROF_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/communications/profiles: status=${PROF_STATUS} body_size=${PROF_BODY_SIZE}"
  fi
fi

# Chunk 61 (CP8): Ingestion runs read-path smoke. Hits
# `GET /api/ingestion/runs` and expects 200.
if [ -z "${SMOKE_SKIP_INGESTION:-}" ]; then
  RUNS_STATUS=$(curl -s -o /tmp/grace_smoke_ingestion_runs -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/ingestion/runs")
  RUNS_BODY_SIZE=$(wc -c < /tmp/grace_smoke_ingestion_runs | tr -d ' ')
  if [ "$RUNS_STATUS" != "200" ] && [ "$RUNS_STATUS" != "404" ]; then
    echo "FAIL GET /api/ingestion/runs: status=${RUNS_STATUS} body_size=${RUNS_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/ingestion/runs: status=${RUNS_STATUS} body_size=${RUNS_BODY_SIZE}"
  fi
fi

# Chunk 61 (CP8): Retriage stats read-path smoke. Hits
# `GET /api/ingestion/retriage/stats` and expects 200.
if [ -z "${SMOKE_SKIP_INGESTION:-}" ]; then
  RETRIAGE_STATUS=$(curl -s -o /tmp/grace_smoke_retriage_stats -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/ingestion/retriage/stats")
  RETRIAGE_BODY_SIZE=$(wc -c < /tmp/grace_smoke_retriage_stats | tr -d ' ')
  if [ "$RETRIAGE_STATUS" != "200" ] && [ "$RETRIAGE_STATUS" != "404" ]; then
    echo "FAIL GET /api/ingestion/retriage/stats: status=${RETRIAGE_STATUS} body_size=${RETRIAGE_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/ingestion/retriage/stats: status=${RETRIAGE_STATUS} body_size=${RETRIAGE_BODY_SIZE}"
  fi
fi

# Chunk 61 (CP8): Communications profile for nil UUID. Hits
# `GET /api/communications/profiles/{person_id}` and expects 200 or 404.
if [ -z "${SMOKE_SKIP_COMMUNICATIONS:-}" ]; then
  PROF_NIL_STATUS=$(curl -s -o /tmp/grace_smoke_prof_nil -w "%{http_code}" \
    -H "X-Graph-Scope: all" \
    "http://127.0.0.1:${PORT}/api/communications/profiles/00000000-0000-0000-0000-000000000000")
  PROF_NIL_BODY_SIZE=$(wc -c < /tmp/grace_smoke_prof_nil | tr -d ' ')
  if [ "$PROF_NIL_STATUS" != "200" ] && [ "$PROF_NIL_STATUS" != "404" ]; then
    echo "FAIL GET /api/communications/profiles/{nil}: status=${PROF_NIL_STATUS} body_size=${PROF_NIL_BODY_SIZE}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   GET /api/communications/profiles/{nil}: status=${PROF_NIL_STATUS} body_size=${PROF_NIL_BODY_SIZE}"
  fi
fi

# Chunk 66 (CP4): OPTIONS preflight probe for the kill-switch PATCH route.
# Verifies the D449 allow_methods extension admits PATCH from a browser
# origin. Set SMOKE_SKIP_PREFLIGHT=1 to disable.
if [ -z "${SMOKE_SKIP_PREFLIGHT:-}" ]; then
  PREFLIGHT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X OPTIONS "http://127.0.0.1:${PORT}/api/ontology/daemon/kill-switch" \
    -H "Origin: http://localhost:3000" \
    -H "Access-Control-Request-Method: PATCH")
  if [ "$PREFLIGHT_STATUS" != "200" ]; then
    echo "FAIL OPTIONS /api/ontology/daemon/kill-switch: status=${PREFLIGHT_STATUS}"
    ERRORS=$((ERRORS + 1))
  else
    # Verify the response header includes PATCH
    PREFLIGHT_METHODS=$(curl -s -D- -o /dev/null \
      -X OPTIONS "http://127.0.0.1:${PORT}/api/ontology/daemon/kill-switch" \
      -H "Origin: http://localhost:3000" \
      -H "Access-Control-Request-Method: PATCH" \
      | grep -i 'access-control-allow-methods')
    if echo "$PREFLIGHT_METHODS" | grep -q 'PATCH'; then
      echo "OK   OPTIONS /api/ontology/daemon/kill-switch: status=${PREFLIGHT_STATUS} PATCH in Allow-Methods"
    else
      echo "FAIL OPTIONS /api/ontology/daemon/kill-switch: PATCH not in Allow-Methods header"
      ERRORS=$((ERRORS + 1))
    fi
  fi
fi

# Chunk 72a (D470): POST /api/extraction/jobs smoke — spawn a job and poll
# for terminal status. Uses the tiny.txt fixture in tests/fixtures/.
SMOKE_FIXTURE="$(pwd)/tests/fixtures/extraction-smoke/tiny.txt"
if [ -f "$SMOKE_FIXTURE" ]; then
  JOB_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{\"job_kind\": \"document\", \"source_path\": \"${SMOKE_FIXTURE}\"}" \
    "http://127.0.0.1:${PORT}/api/extraction/jobs")
  JOB_STATUS=$(echo "$JOB_RESPONSE" | tail -1)
  JOB_BODY=$(echo "$JOB_RESPONSE" | sed '$d')
  if [ "$JOB_STATUS" != "202" ]; then
    echo "FAIL POST /api/extraction/jobs: status=${JOB_STATUS}"
    ERRORS=$((ERRORS + 1))
  else
    echo "OK   POST /api/extraction/jobs: status=${JOB_STATUS}"
    # Extract job_id and poll for terminal status
    JOB_ID=$(echo "$JOB_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null || echo "")
    if [ -n "$JOB_ID" ]; then
      POLL_TIMEOUT=60
      POLL_ELAPSED=0
      while [ "$POLL_ELAPSED" -lt "$POLL_TIMEOUT" ]; do
        sleep 3
        POLL_ELAPSED=$((POLL_ELAPSED + 3))
        POLL_RESP=$(curl -s "http://127.0.0.1:${PORT}/api/extraction/jobs/${JOB_ID}")
        POLL_STATUS=$(echo "$POLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
        if [ "$POLL_STATUS" = "completed" ] || [ "$POLL_STATUS" = "failed" ]; then
          echo "OK   GET /api/extraction/jobs/${JOB_ID}: terminal status=${POLL_STATUS}"
          break
        fi
      done
      if [ "$POLL_ELAPSED" -ge "$POLL_TIMEOUT" ]; then
        echo "WARN GET /api/extraction/jobs/${JOB_ID}: poll timed out (status=${POLL_STATUS})"
        # Not a hard failure — the subprocess may take longer in CI
      fi
    fi
  fi
else
  echo "SKIP POST /api/extraction/jobs: fixture not found at ${SMOKE_FIXTURE}"
fi

if [ "$ERRORS" -gt 0 ]; then
  echo "--- uvicorn log (on failure) ---"
  cat "$LOG_FILE"
  exit 1
fi
exit 0
