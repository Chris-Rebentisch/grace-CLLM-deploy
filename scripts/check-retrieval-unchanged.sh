#!/usr/bin/env bash
# CF3 retrieval-lock enforcement (Chunk 29). Mirrors check-regeneration-unchanged.sh.
# Asserts that src/retrieval/* has zero modifications, with a scoped
# allowlist for the D265 Strangler Fig shim.
#
# Usage:
#   bash scripts/check-retrieval-unchanged.sh [base-ref]
#
# Exits non-zero if any file under src/retrieval/ differs (excluding the
# allowlisted shim file).
#
# D265 (Chunk 35a): src/retrieval/semantic_strategy.py is the only
# permitted retrieval-side change — its body moved to
# src/shared/embeddings.py and only a thin re-export shim remains.
# D267 (Chunk 35b): allowlist extends to src/retrieval/query_event_writer.py
# (new file) and src/retrieval/retrieval_models.py (additive query_event_id
# field).
# D384 (Chunk 52): allowlist extends to src/retrieval/federation_router.py
# (new file — federation query router).
# All four exemptions are PERMANENT and exact-filename-anchored;
# subsequent chunks must add new entries explicitly per a new D-number.

set -uo pipefail

BASE_REF="${1:-HEAD}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: not a git repository"
  exit 1
fi

DIFF=$(git diff --name-only "${BASE_REF}" -- src/retrieval/ 2>/dev/null \
  | grep -v '^src/retrieval/semantic_strategy\.py$' \
  | grep -v '^src/retrieval/query_event_writer\.py$' \
  | grep -v '^src/retrieval/retrieval_models\.py$' \
  | grep -v '^src/retrieval/federation_router\.py$' \
  | grep -v '^src/retrieval/document_chunk_strategy\.py$' \
  | grep -v '^src/retrieval/pipeline\.py$' \
  | grep -v '^src/retrieval/retrieval_config\.py$' \
  | tr -d ' ')
# D466 (Chunk 71): 5th CF3 allowlist entry — document_chunk_strategy.py. PERMANENT.
# D356 capture-the-why: CF3 retrieval-lock accumulator requires explicit D-number
# for each src/retrieval/* modification. D466 adds chunk-semantic ANN strategy.
# D467 (Chunk 71): 6th CF3 allowlist entry — pipeline.py. PERMANENT.
# D356 capture-the-why: pipeline.py must be edited to wire the 5th query-time
# strategy into RetrievalPipeline.query()'s tasks fan-out. D467.
# D467 (Chunk 71): 7th CF3 allowlist entry — retrieval_config.py. PERMANENT.
# D356 capture-the-why: retrieval_config.py gains chunk_semantic_enabled +
# chunk_semantic_top_k config fields to gate the 5th strategy. D467.

if [ -n "${DIFF}" ]; then
  echo "ERROR: src/retrieval/* modified outside CF3 allowlist (D265 + D267). CF3 retrieval lock violated."
  echo "${DIFF}"
  exit 1
fi

echo "OK: src/retrieval/* unchanged (D265 shim + D267 writer + retrieval_models + D384 CF3 exemption allowlisted)."
