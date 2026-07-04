# GrACE data contracts (for the Claude-as-LLM helpers)

Verbatim field shapes the import scripts validate against. Source of truth is the
Pydantic model in the repo; this is a reading aid. Verified 2026-06-10.

## CompetencyQuestion — `src/discovery/cq_models.py` ; table `competency_questions`
Persisted via `bulk_create_cqs(db, list[CompetencyQuestion])` in `src/discovery/cq_database.py`.
- `canonical_text: str` **(required)** — the CQ text
- `cq_type: str` — SCOPING | VALIDATING | FOUNDATIONAL | RELATIONSHIP | METAPROPERTY | UNCLASSIFIED
- `domain: str` (default "other")
- `priority: str` — HIGH | MEDIUM | LOW | UNSET
- `source: str` **(required)** — **HUMAN_AUTHORED** | LLM_TOP_DOWN | LLM_BOTTOM_UP | LLM_MIDDLE_OUT | LLM_GAP_FILL | LLM_COMBINED | SYSTEM_GENERATED  ← Claude CQs use `HUMAN_AUTHORED` (operator-curated)
- `source_pass: str | None` — top_down | bottom_up | middle_out
- `status: str` (default DRAFT) — DRAFT | ACCEPTED | EDITED | REJECTED | OUT_OF_SCOPE
- `linked_document_ids: list[str]` (JSONB) — processed_documents UUIDs
- `generation_confidence: float`, `verification_confidence: float`
- `metadata_extra: dict` (JSONB) — import_cqs stamps `{authoring_method, rationale}`

## ProposedEntityType / SeedSchema — `src/discovery/schema_models.py`, `src/discovery/schema_merge_models.py`
The review screen consumes a **SeedSchema** (the merged shape). Claude authors that
shape directly (see seed_schema.example.json). Per entity type (MergedEntityType):
- `name, alternative_names[], parent_type|null, description, display_label,`
  `plain_description, example_snippet|null, domain`
- `properties[]` — each: `{name, data_type, description, required, answerable_cqs[]}`
  data_type ∈ string | datetime | float | boolean | integer | reference
- `provenance` (use `"claude_authored"`), `confidence: float`, `source_passes[]`
- `seed_source|null, seed_type_name|null, answerable_cqs[], evidence_document_count`

Per relationship (MergedRelationship):
- `name, alternative_names[], source_type, target_type, description, display_label,`
  `plain_description, example_snippet|null`
- `richness_tier` ∈ simple | attributed | reified ; `richness_rationale`
- `edge_properties[]` (same property shape), `provenance, confidence, source_passes[]`
- `seed_source|null, seed_rel_name|null, answerable_cqs[]`

SeedSchema top level: `entity_types[], relationships[], coverage_matrix[],`
`provenance_summary{}, quality_metrics{}, gap_report{}, extraction_run_id,`
`industry_profile, created_at`.

## Review start — `POST /api/ontology/review/start` (`src/api/review_routes.py`)
Body `StartReviewRequest`:
- `merge_run_id: str` — a completed merge run id (import_proposal auto-fetches from
  `GET /api/discovery/merge-latest`)
- `reviewer: str`
- `seed_schema_data: dict` — the full SeedSchema JSON above
Returns the ReviewSession (has `session_id`). 403 `no_active_matrix` if permission
enforcement is on with no ratified matrix — keep `GRACE_PERMISSION_ENFORCEMENT_ENABLED=0`.

## processed_documents — `src/discovery/database.py`
Columns used: `id, file_path, file_name, extracted_text, domain, word_count,`
`status` (QUEUED | PROCESSING | COMPLETE | FAILED), `file_type`.
Corpus builder: `build_balanced_document_text(db, domain, max_chars=None) -> str`
in `src/discovery/domain_batcher.py` (equal char share per doc, head/middle/tail sampling).

## DB session — `src/shared/database.py`
`get_session_factory()()` → Session. DATABASE_URL from `.env` via
`src/shared/config.py get_settings().database_url`. Scripts chdir to repo root so
`.env` + `config/discovery.yaml` resolve (see scripts/_common.py).

## merge_run.canonical_count — `src/discovery/merge_models.py`
`MergeRun.canonical_count: int` = collapsed schema-only review-set size (the human-
facing CQ count the onboarding header leads with via `/api/discovery/merge-latest`).

## Auto-accept (Step 4) — ratify + ArcadeDB sync. Verified 2026-06-10.
Two standalone calls (no review session required):

**`POST /api/ontology/ratify`** (`ontology_routes.py` → `schema_store.ratify_version`):
body `{schema_json, schema_modules, source, reviewer?, changelog?, cq_coverage_snapshot?}`.
- `schema_json`: `{entity_types: {NAME: {description, properties, parent_type, domain,
  provenance, confidence}}, relationships: {NAME: {source_type, target_type, description,
  richness_tier, edge_properties, domain, provenance, confidence}}}` (NAME-keyed dicts, not
  lists). Ratify canonicalizes list-shape `properties` → dict-shape.
- `schema_modules`: per-domain partition `{domain: {entity_types:{…}, relationships:{…}}}`
  (mirror `review_ops.partition_schema_by_module`).
- `source`: `VersionSource` ∈ `discovery | guided_review | adaptive_evolution | manual`
  (`src/ontology/models.py`). Auto-accept uses `manual`.
- Creates + activates a new `OntologyVersion` (Postgres `ontology_versions`); returns it
  with `version_number`, `id`, `entity_type_count`, `relationship_type_count`, `hash_chain`,
  `is_active`. No review session / merge run required (test-confirmed standalone).

**`POST /api/graph/sync-schema`** (`graph_routes.py` → `schema_sync.sync_schema_to_graph`):
no body. Reads the active version, generates DDL from `schema_json`, executes
CREATE VERTEX/EDGE TYPE + indexes on ArcadeDB. Idempotent per version. Returns a
`GraphSchemaSyncRecord` with per-statement results. **Required before graph extraction.**

`GET /api/ontology/active` returns the active `OntologyVersion` (same fields) — 404 if none.

## Seed grounding (Option C) — reuse grace's seed machinery. Verified 2026-06-10.
`export_seed_reference.py` builds the Claude-readable seed reference from grace's own
modules (no re-derivation):
- `src/discovery/seed_registry.py`: `load_seed_registry()` → `SeedRegistry` with
  `.sources` (each `SeedSource` has `id`, `source_ontology`, `domains[]`,
  `industry_verticals[]`, `description`) + `.universal_sources` (ids: `schema_org_base`,
  `prov_o_core`). `get_source_by_id(id)`. Registry: `config/seed_registry.json` (11
  sources; for `legal`: fibo_legal_entities/capacity/core, lkif_legal_action/role/norm).
- Parsed cache: `seeds/parsed/<source_id>.json`. Only provisioned sources are present
  (legal has 4 cached: fibo_legal_entities, lkif_legal_role, schema_org_base, prov_o_core).
  **Cache caveat:** the JSON lacks two now-required `SeedReference` fields — patch
  `source_files=[]` + `registry_version=reg.version` before `model_validate`.
- `src/discovery/seed_parser.py`: `format_for_llm(seed_ref: SeedReference) -> str` — the
  exact LLM-text renderer the native path uses (class list + subclass hierarchy + seed
  relationships). `parse_and_cache_seeds(sources)` (in `seed_provisioner.py`) parses RDF
  → cache (rdflib, CPU; needs the seed RDF on disk).

Seed fields on the proposal (carried in SeedSchema; `provenance` lands in the ratified
`schema_json`, the rest stay proposal metadata): `seed_source` (`fibo|lkif|schema_org|prov_o`),
`seed_type_name` / `seed_rel_name`, `seed_alignment`, `provenance="seed_aligned"`.
