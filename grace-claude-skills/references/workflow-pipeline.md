# End-to-end pipeline map — where Claude replaces Ollama

GrACE's onboarding pipeline and which steps run native vs. on Claude. Human ontology
ratification is **bypassed** on the Claude path (operator decision 2026-06-10) — the
proposal is auto-accepted programmatically.

```
                         NATIVE (no gpt-oss heat)        CLAUDE-AS-LLM (this bundle)
┌─────────────────┐
│ 0 Process docs  │  Docling OCR/parse → processed_documents   (CPU, not gpt-oss; OK)
└────────┬────────┘
         │
┌────────▼────────┐   STEP 1  export_corpus.py
│ 1 Export corpus │  balanced all-doc text  ──────────────────►  reads Postgres only
└────────┬────────┘
         │
┌────────▼────────┐   STEP 2  grace-cq-authoring SKILL
│ 2 Generate CQs  │  *** was gpt-oss ***       ◄──────────────  CLAUDE authors cqs.json
│                 │   import_cqs.py persists  ──────────────────► bulk_create_cqs (DB)
└────────┬────────┘
         │   ✗ CQ merge SKIPPED — Claude authors a deduped set, nothing to merge.
         │
┌────────▼────────┐   STEP 3  grace-ontology-proposal SKILL
│ 3 Schema        │  export_seed_reference.py (FIBO/LKIF/...) ──► seed grounding (Option C)
│   propose       │  *** was gpt-oss (25 min, UI timeout) ***  ◄ CLAUDE authors + aligns
│   + coverage    │                                              seed_schema.json
│                 │   map_coverage.py (nomic-embed, no heat) ───► fills answerable_cqs
└────────┬────────┘
         │
┌────────▼────────┐   STEP 4  grace-auto-accept SKILL
│ 4 Auto-accept   │  ✗ HUMAN /review gate BYPASSED             ◄  auto_accept.py
│   (ratify+sync) │   POST /api/ontology/ratify  ──────────────► active OntologyVersion
│                 │   POST /api/graph/sync-schema ─────────────► ArcadeDB DDL (vertex/edge types)
└────────┬────────┘
         │
┌────────▼────────┐   STEP 5  grace-property-detailing SKILL (optional, refined Option B)
│ 5 Detail props  │  *** was gpt-oss (25 min serial) ***       ◄  CLAUDE details props →
│   (optional)    │   map_coverage.py + auto_accept.py  ────────► new active version + re-sync
└────────┬────────┘
         │
┌────────▼────────┐   STEP 6  grace-graph-extraction SKILL
│ 6 Graph extract │  *** was gpt-oss ***                       ◄  CLAUDE extracts per doc
│                 │  import_extraction.py (bulk + lookup)  ─────► ArcadeDB vertices/edges
└─────────────────┘
```

## Heat-producing steps eliminated by this bundle (ALL of them)
- Step 2 (CQ generation) → Claude (grace-cq-authoring).
- Step 3 (schema propose) → Claude (grace-ontology-proposal); coverage via nomic-embed.
- Step 5 (property detailing) → Claude (grace-property-detailing). The worst offender:
  native Stage-2 serial detailing ran ~25 min and blew the frontend 15-min poll timeout.
- Step 6 (graph extraction) → Claude (grace-graph-extraction).

## Dropped / bypassed on the Claude path
- **CQ merge — dropped.** Only existed to compress weak-model redundancy (4 blind passes
  → 200+ near-dup CQs). Claude authors one deduped, type-classified pass, so the merge is
  a no-op (verified: 28 CQs → mostly singletons). Coverage (its only useful output) moved
  to propose time via `map_coverage.py`. Native merge stays valid for the local-LLM path.
- **Human ontology ratification — bypassed.** Reviewing types is meaningless to a
  non-technical operator. `auto_accept.py` ratifies + syncs the proposal programmatically.
  grace's `/review` + ratify CODE is untouched — just not required. (Step 4 still has to
  make the schema active + synced, or extraction has nothing to write against — "bypass"
  = auto-accept, not "no ratification.")

## Steps that stay native (acceptable — no gpt-oss)
- Step 0 Docling — CPU OCR/parse, not the 65 GB model.
- Coverage mapping — nomic-embed-text (small embed model, low heat).
- Ratify + ArcadeDB DDL sync — plain HTTP calls, no model.

## gpt-oss is no longer required for onboarding
With every LLM step on Claude, onboarding runs end-to-end with gpt-oss:120b unloaded.
Only embeddings (nomic-embed-text, light) and Docling (CPU) run locally.

## Run order (operator quick-path)
```bash
cd ~/grace
.venv/bin/python ~/grace-claude-skills/scripts/export_corpus.py                                   # 1 export
#   → Claude authors workspace/cqs.json                                                            (2 author CQs)
.venv/bin/python ~/grace-claude-skills/scripts/import_cqs.py --in ./workspace/cqs.json --domain legal
.venv/bin/python ~/grace-claude-skills/scripts/export_seed_reference.py --domain legal           # 3 seed grounding (Option C, optional)
#   → Claude authors + aligns workspace/seed_schema.json                                           (3 propose)
.venv/bin/python ~/grace-claude-skills/scripts/map_coverage.py --in ./workspace/seed_schema.json --domain legal
.venv/bin/python ~/grace-claude-skills/scripts/auto_accept.py  --in ./workspace/seed_schema.json   # 4 auto-accept (ratify + ArcadeDB sync)
#   → (optional 5) grace-property-detailing, then auto_accept again
#   → Claude authors workspace/extraction_<doc>.json                                               (6 extract)
.venv/bin/python ~/grace-claude-skills/scripts/import_extraction.py --in ./workspace/extraction_<doc>.json --doc-id <UUID> --module legal
```
