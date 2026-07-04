---
name: grace-graph-extraction
description: >
  STEP 5 of the Claude-as-LLM onboarding flow. Claude reads a document + the ratified
  ontology and extracts entities + relationships into the graph — replacing the gpt-oss
  extraction pass. A helper writes them to ArcadeDB via the documented bulk + lookup
  endpoints (two-phase, so relationships resolve to grace_ids). Use after an ontology is
  ratified and synced. MVP: no entity-resolution/dedup yet — see limitations.
---

# grace-graph-extraction

## Role
You (Claude) are the graph extractor. You replace GrACE's native `extract_document`
(which calls gpt-oss via the instructor client). For one document at a time, you read
the text + the active ontology and emit the entities and relationships present, typed
against the ratified schema.

## Hard prerequisite
An ontology must be **active AND synced to ArcadeDB** first — produced by
**grace-auto-accept** (Step 4: `auto_accept.py` ratifies + runs `POST /api/graph/sync-schema`).
The graph schema must define every `entity_type` / `relationship_type` you emit, or
ArcadeDB rejects the insert. Fetch the live schema:
```
GET http://127.0.0.1:8000/api/ontology/active
```
Use ONLY the type names, relationship names, and property names it defines.

## Inputs
- One document's text (from `~/grace/workspace/corpus/<domain>.md`, or pull a single
  `processed_documents` row's `extracted_text`).
- The active ontology (`GET /api/ontology/active`) — your typing vocabulary.

## Method
1. Identify the entities in the document. Type each against the ontology
   (`entity_type` = a defined vertex type). Put the human-readable identifier in
   `name` and in `properties.name`; fill the other ontology properties you can ground.
2. Identify relationships. Reference endpoints **by name + type** (`source_name`,
   `source_type`, `target_name`, `target_type`) — you don't know grace_ids; the helper
   resolves them after inserting entities.
3. Set a `confidence` per item. Add `sensitivity_tags` (bar-form, e.g. `"|privileged|"`)
   when the source warrants it; default `""`.
4. Do NOT invent entities/relations the document doesn't support.

## Output format
Emit one JSON object per document matching
`templates/extraction.example.json` (`entities[]` + `relationships[]`). Write it to
`~/grace/workspace/extraction_<docname>.json`.

## Write to the graph (no LLM)
```bash
cd ~/grace
.venv/bin/python ~/grace-claude-skills/scripts/import_extraction.py \
  --in ./workspace/extraction_<docname>.json --doc-id <PROCESSED_DOC_UUID> --dry-run
.venv/bin/python ~/grace-claude-skills/scripts/import_extraction.py \
  --in ./workspace/extraction_<docname>.json --doc-id <PROCESSED_DOC_UUID> --module <domain>
```
Phase A inserts entities (`POST /api/graph/entities/bulk`); Phase B resolves each
relationship endpoint via `GET /api/graph/entities/lookup?type=&name=` and inserts the
edges. Inspect results at `http://localhost:3000/graph`.

## MVP limitations (verify before trusting at scale)
- **No entity resolution / dedup.** Endpoints are matched by `(entity_type, name)`.
  Re-running over overlapping documents can create duplicate vertices. The native
  pipeline's verify + resolve stage is not replicated here — run documents once, or add
  a resolution pass before scaling.
- **Name property assumption.** The lookup endpoint matches on a `name` property; the
  helper injects `properties.name`. If a type's identifier property is named
  differently, adjust before relying on relationship resolution.
- One document per file keeps grace_id resolution unambiguous. Batch by looping the
  helper over per-document JSON files.

## Heat / safety
- Claude's own inference — no gpt-oss. Keep the model unloaded.
- Writes the live `grace` graph (ArcadeDB) — intended. Never point it at a test graph
  you care about without checking `ARCADE_*` config.
