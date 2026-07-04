---
name: grace-auto-accept
description: >
  STEP 4 of the Claude-as-LLM onboarding flow. Auto-accepts the Claude-authored
  ontology proposal — makes it the ACTIVE ontology version and syncs the DDL into
  ArcadeDB — WITHOUT a human review/ratify gate. Operator decision (2026-06-10): skip
  human ontology ratification; grace's review/ratify code stays, we just don't require
  a person to click through. Use right after grace-ontology-proposal + coverage. No LLM.
---

# grace-auto-accept

## Why this exists
For a non-technical operator, hand-reviewing ontology entity/relationship types is
meaningless ("teaching Japanese to a goldfish"). Once Claude authors the proposal and
`map_coverage.py` confirms full CQ coverage, the human gate adds friction without value.
So we **auto-accept**: the proposal becomes the active ontology and is synced to ArcadeDB
programmatically. This is NOT "no ratification" — something still has to make the schema
active + synced or graph extraction has nothing to write against. We just remove the
*human* step. grace's `/review` + `POST /api/ontology/ratify` code is untouched.
Operator decision 2026-06-10 (see `grace-claude-skills/README.md` §Decisions locked)
— do not re-add a human gate without revisiting that decision.

## What it does (two documented, standalone calls — no review session needed)
1. `POST /api/ontology/ratify` — converts the SeedSchema into grace's ratified
   `schema_json` ({entity_types:{…}, relationships:{…}}) + per-domain `schema_modules`,
   ratifies it as a new **active** `OntologyVersion` (Postgres). `source="manual"`,
   reviewer recorded as the auto-accept provenance, CQ coverage carried in the changelog
   + `cq_coverage_snapshot`.
2. `POST /api/graph/sync-schema` — generates DDL from the active version and executes it
   on ArcadeDB (CREATE VERTEX/EDGE TYPE + indexes). Idempotent.

## Inputs
- The coverage-enriched `~/grace/workspace/seed_schema.json` from **grace-ontology-proposal**
  (after `map_coverage.py`).

## Do this
```bash
cd ~/grace
# preview the payload without writing anything:
.venv/bin/python ~/grace-claude-skills/scripts/auto_accept.py --in ./workspace/seed_schema.json --dry-run
# accept + sync to ArcadeDB:
.venv/bin/python ~/grace-claude-skills/scripts/auto_accept.py --in ./workspace/seed_schema.json
```
Output reports the new active version number, type/relationship counts, and the DDL sync
result (statements executed, any errors). It then verifies `GET /api/ontology/active`.

Flags: `--no-sync` (ratify only, sync later), `--source manual|discovery|adaptive_evolution`,
`--reviewer "<who/what>"`, `--admin-key` (if `GRACE_ADMIN_KEY` is set).

## Prereqs
- uvicorn running; **ArcadeDB up** (Docker) for the sync step; `GRACE_PERMISSION_ENFORCEMENT_ENABLED=0`.

## After this
- **Optional Step 5 (grace-property-detailing):** Claude fills full properties, then run
  `auto_accept.py` again on the detailed schema to ratify a new version (the detailing
  path also auto-accepts now — no human gate).
- **Step 6 (grace-graph-extraction):** the active+synced schema is the typing vocabulary
  extraction writes against.

## Re-running / versions
Each `auto_accept.py` run ratifies a NEW active version (v1, v2, …) with an OM4OV diff
from the predecessor; `sync-schema` is idempotent per version. Safe to re-run after edits.

## Heat / safety
- No LLM — pure HTTP. Keep gpt-oss unloaded.
- Mutating: writes the active ontology version (Postgres) + ArcadeDB DDL. Intended.
- The human review path is bypassed by choice; see memory `project-skip-human-ontology-ratification`.
