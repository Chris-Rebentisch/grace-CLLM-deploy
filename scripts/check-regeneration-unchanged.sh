#!/usr/bin/env bash
# D193 hard-lock enforcement. Asserts that src/regeneration/* has zero
# modifications in this branch compared to the base, with a scoped exact-filename
# allowlist for the D533 carve-out (mirrors check-retrieval-unchanged.sh / CF3).
#
# Usage:
#   bash scripts/check-regeneration-unchanged.sh [base-ref]
#
# Exits non-zero if any file under src/regeneration/ differs (excluding allowlist).
#
# D533 (ratified 2026-06-22, Claude-as-LLM test track): FIRST D193 carve-out. The exact file
# src/regeneration/regeneration_config.py is exempt — its system_prompt_template default
# gains the SS-1/S7 data-vs-instruction prompt-injection defense clause. Exact-filename-
# anchored and PERMANENT. Capture-the-why (D356): invariant = D193 hard-lock; carve-out =
# config default-string edit; authorization = D533 (GrACE-Decisions.md). New carve-outs
# require a new D-number entry here.
#
# F-0048 / ISS-0039 (validation run, 2026-07-03): SECOND D193 carve-out. The exact
# file src/regeneration/prompt_assembly.py is exempt — the compose path appends a
# budget-bounded supplement serializing entity property values + D532 intent reasoning
# prose that the upstream retrieval serialization dropped (CQ-21 compose thinness; the
# serializer content-completeness half of F-0048). Exact-filename-anchored. Capture-the-
# why (D356): invariant = D193 hard-lock; carve-out = prompt_assembly.py compose-context
# supplement; authorization = F-0048 / ISS-0039 (serializer content-completeness). New
# carve-outs require a new entry here.

set -uo pipefail

BASE_REF="${1:-HEAD}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: not a git repository"
  exit 1
fi

DIFF=$(git diff --name-only "${BASE_REF}" -- src/regeneration/ 2>/dev/null \
  | grep -v '^src/regeneration/regeneration_config\.py$' \
  | grep -v '^src/regeneration/prompt_assembly\.py$' \
  | tr -d ' ')

if [ -n "${DIFF}" ]; then
  echo "ERROR: src/regeneration/* modified outside the D533/ISS-0039 carve-out allowlist. Chunk 27 D193 hard lock violated."
  echo "${DIFF}"
  exit 1
fi

echo "OK: src/regeneration/* unchanged (D533 regeneration_config.py + F-0048/ISS-0039 prompt_assembly.py carve-outs allowlisted)."
