#!/usr/bin/env bash
# Chunk 28 EC-7 — airgap enforcement for viewer/inspector surfaces.
# Scans frontend source for third-party CDN / telemetry hosts.
# Allowlist: data: URIs and _next/static/* internal paths.
#
# Usage:
#   bash scripts/check-no-third-party.sh                  # default prod paths
#   bash scripts/check-no-third-party.sh path1 path2 ...  # explicit paths

set -euo pipefail

cd "$(dirname "$0")/.."

FORBIDDEN_HOSTS=(
  "fonts.googleapis.com"
  "cdnjs.cloudflare.com"
  "unpkg.com"
  "jsdelivr.net"
  "sentry.io"
  "posthog.com"
  "mixpanel.com"
  "google-analytics.com"
  "logrocket.com"
  "datadoghq.com"
  "telemetry.nextjs.org"
)

if [ "$#" -gt 0 ]; then
  SCAN_PATHS=("$@")
else
  SCAN_PATHS=(
    "frontend/.next/static"
    "frontend/app"
    "frontend/components"
    # Chunk 43 (CP7): explicit Sensitivity Gate paths. The recursive scan
    # over frontend/app and frontend/components already reaches these, but
    # naming them explicitly keeps the contract obvious to reviewers and
    # protects against future refactors that move the parent paths.
    "frontend/app/sensitivity"
    "frontend/components/sensitivity"
    # Chunk 47 (CP8): explicit Proposals paths.
    "frontend/app/proposals"
    "frontend/components/proposals"
    # Chunk 49 (CP9): explicit Autonomy Calibration paths.
    "frontend/app/autonomy"
    "frontend/components/autonomy"
    # Chunk 55 (CP10): explicit Communication Ingestion paths.
    "frontend/app/ingestion"
    "frontend/components/ingestion"
    # Chunk 60 (CP9): explicit Communications profile paths.
    "frontend/app/communications"
    "frontend/components/communications"
    "src/eval"
  )
fi

ERRORS=0

for host in "${FORBIDDEN_HOSTS[@]}"; do
  for path in "${SCAN_PATHS[@]}"; do
    if [ -d "$path" ] || [ -f "$path" ]; then
      if [ -d "$path" ]; then
        matches=$(grep -rE "${host}" "$path" \
          --include='*.ts' --include='*.tsx' \
          --include='*.js' --include='*.html' --include='*.css' \
          --include='*.py' --include='*.yaml' --include='*.yml' \
          2>/dev/null \
          | grep -vE "data:|_next/static" || true)
      else
        matches=$(grep -E "${host}" "$path" 2>/dev/null \
          | grep -vE "data:|_next/static" || true)
      fi
      if [ -n "$matches" ]; then
        echo "FORBIDDEN HOST: ${host} in ${path}"
        echo "$matches"
        ERRORS=$((ERRORS + 1))
      fi
    fi
  done
done

if [ "$ERRORS" -gt 0 ]; then
  echo "check-no-third-party: ${ERRORS} violation(s)"
  exit 1
fi
echo "check-no-third-party: OK"
