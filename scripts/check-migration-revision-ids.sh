#!/usr/bin/env bash
# scripts/check-migration-revision-ids.sh
#
# Top-level alias for `scripts/lint/check-migration-revision-ids.sh` so the
# lint shows up alongside the other repo-level check-*.sh guards
# (check-no-third-party, check-regeneration-unchanged, check-retrieval-unchanged,
# check-api-contract, check-no-numeric-scores). See the lint script for the
# full implementation; this wrapper exists for discoverability in the
# `scripts/check-*.sh` family per chunk-43 retro fix TOP-10-#1.
set -euo pipefail
exec bash "$(dirname "$0")/lint/check-migration-revision-ids.sh" "$@"
