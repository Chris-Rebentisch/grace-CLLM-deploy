---
name: grace-property-detailing
description: >
  STEP 4 of the Claude-as-LLM onboarding flow (refined Option B). After the operator
  reviews the skeleton ontology proposal, Claude fills in full properties for the kept
  (ratified-subset) types — replacing the slow native gpt-oss Stage-2 detailing that
  took ~25 min and timed out the UI. Produces a detailed seed_schema that is
  auto-accepted as a new active version. Use after grace-ontology-proposal (+ the
  optional human review, if you ran one — the default path has none).
---

# grace-property-detailing

## Role
You (Claude) are the Stage-2 property detailer. The skeleton proposal from
**grace-ontology-proposal** has types + relationships but only 2–5 obvious properties
each. Here you flesh out the **full** property set per type — but only for the types
the operator decided to keep (the "ratified subset"), so no effort is spent on types
they rejected. This is the refined Option B from the 2026-06-09 session: skeleton →
human review → detail-on-subset, batched.

## Why Claude instead of native
Native Stage-2 ran serial gpt-oss calls per type (~25 min for 24 types) and blew the
frontend's 15-min poll timeout. Claude details all types in one reasoning pass, no heat.

## Inputs
- The skeleton proposal on disk: `~/grace/workspace/seed_schema.json`.
- The review session id (to know which types were accepted).
- The corpus bundle(s) from **grace-corpus-export** (the evidence for property values).

## Step A — prepare the subset (no LLM)
```bash
cd ~/grace
# all types — the DEFAULT Claude-as-LLM path (no review session exists on the
# auto-accept path, so there is nothing to filter by):
.venv/bin/python ~/grace-claude-skills/scripts/export_proposal_for_detailing.py \
  --in ./workspace/seed_schema.json
# OPTIONAL — only if you ran the human-review variant (import_proposal.py opened
# a review session) and want to detail just the accepted types:
.venv/bin/python ~/grace-claude-skills/scripts/export_proposal_for_detailing.py \
  --in ./workspace/seed_schema.json --session-id <SESSION_ID> --only-accepted
```
`--session-id` / `--only-accepted` are **optional** — skip them on the default path.
Writes `~/grace/workspace/to_detail.json` (skeleton, kept subset only).

## Step B — Claude details properties
Read `to_detail.json` + the corpus. For **every** entity type and relationship, author
the complete property set the documents and CQs justify. Per property:
- `name` (snake_case), `data_type` (string | datetime | float | boolean | integer | reference),
  `description`, `required` (true only when every instance must have it), `answerable_cqs`.
- Add edge_properties to relationships whose `richness_tier` is `attributed`/`reified`.
- Keep names PascalCase_With_Underscores (types) / snake_case (properties).
- Ground each property in the evidence; don't invent fields the corpus never implies.

Emit the **same SeedSchema shape** as `grace-ontology-proposal/templates/seed_schema.example.json`,
now with rich `properties[]`. Write it to `~/grace/workspace/seed_schema.detailed.json`.

## Step C — re-map coverage + auto-accept (no LLM, no human gate)
Refresh coverage on the detailed schema, then auto-accept it as a NEW active ontology
version (human ratification is bypassed — see grace-auto-accept):
```bash
cd ~/grace
.venv/bin/python ~/grace-claude-skills/scripts/map_coverage.py \
  --in ./workspace/seed_schema.detailed.json --domain legal
.venv/bin/python ~/grace-claude-skills/scripts/auto_accept.py \
  --in ./workspace/seed_schema.detailed.json
```
`auto_accept.py` ratifies the detailed schema (a new active version with an OM4OV diff
from the skeleton) **and re-syncs the DDL to ArcadeDB** — the prerequisite for graph
extraction (grace-graph-extraction).

## Note on the native engine
GrACE has a built, tested batched detailer (`detail_types`, `run_stage2_batch`,
batch_size 4) in `src/discovery/schema_extractor.py`, but there is **no write-back
endpoint** to patch properties onto a ratified version — you re-review/re-ratify a new
version. This skill takes that same re-ratify path, with Claude doing the detailing.

## Heat / safety
- Claude's own inference — no gpt-oss. Keep the model unloaded.
- Keep `GRACE_PERMISSION_ENFORCEMENT_ENABLED=0` during onboarding.
