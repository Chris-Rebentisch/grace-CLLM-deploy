#!/usr/bin/env bash
# grafana_health_check.sh — Grafana startup preflight with defect-class
# regex detection and JSON diagnostic output.
#
# Reference: D457. Detects known defect classes from Grafana container logs.
#
# Usage:
#   bash scripts/preflight/grafana_health_check.sh
#
# Exit 0: Grafana healthy, no defect patterns found.
# Exit 1: Grafana unhealthy or defect pattern detected; JSON diagnostic on stdout.

set -euo pipefail

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3001}"
CONTAINER_NAME="${GRAFANA_CONTAINER:-grace-grafana}"
TIMEOUT_SECONDS=30

# --- Health poll loop ---
HEALTHY=false
for i in $(seq 1 "$TIMEOUT_SECONDS"); do
  if curl -sf "${GRAFANA_URL}/api/health" > /dev/null 2>&1; then
    HEALTHY=true
    break
  fi
  sleep 1
done

# --- Capture logs and check for defect patterns ---
LOGS=$(docker logs "$CONTAINER_NAME" --tail 100 2>&1 || echo "")

emit_diagnostic() {
  local defect_class="$1"
  local offending_line="$2"
  local recommended_fix="$3"
  # Escape quotes in offending_line for valid JSON
  offending_line=$(echo "$offending_line" | sed 's/"/\\"/g')
  cat <<ENDJSON
{"defect_class": "$defect_class", "offending_line": "$offending_line", "recommended_fix": "$recommended_fix"}
ENDJSON
  exit 1
}

# Check defect-class patterns against captured logs
if echo "$LOGS" | grep -q 'UID is longer than 40 symbols'; then
  OFFENDING=$(echo "$LOGS" | grep 'UID is longer than 40 symbols' | head -1)
  emit_diagnostic "uid_too_long" "$OFFENDING" "Shorten alert-rule UIDs to <= 40 characters in provisioning YAML."
fi

if echo "$LOGS" | grep -q 'role "grace_readonly" does not exist'; then
  OFFENDING=$(echo "$LOGS" | grep 'role "grace_readonly" does not exist' | head -1)
  emit_diagnostic "role_not_exist" "$OFFENDING" "Run: GRAFANA_POSTGRES_PASSWORD=<secret> bash scripts/setup/bootstrap_grace_readonly.sh"
fi

if echo "$LOGS" | grep -qE 'Failed to provision.*error:'; then
  OFFENDING=$(echo "$LOGS" | grep -E 'Failed to provision.*error:' | head -1)
  emit_diagnostic "yaml_parse_failure" "$OFFENDING" "Check provisioning YAML syntax in docker/grafana/provisioning/."
fi

if echo "$LOGS" | grep -q 'Datasource not found'; then
  OFFENDING=$(echo "$LOGS" | grep 'Datasource not found' | head -1)
  emit_diagnostic "datasource_missing" "$OFFENDING" "Verify datasource provisioning files reference correct datasource names."
fi

if [ "$HEALTHY" = "false" ]; then
  emit_diagnostic "health_timeout" "No response from ${GRAFANA_URL}/api/health within ${TIMEOUT_SECONDS}s" "Check Grafana container status: docker logs $CONTAINER_NAME"
fi

echo "OK: Grafana healthy at ${GRAFANA_URL}, no defect patterns detected."
exit 0
