---
name: grace-ontology-proposal
description: >
  STEP 3 of the Claude-as-LLM onboarding flow. Claude reads the corpus + canonical CQs
  and authors the ontology proposal (entity types + relationships, skeleton-first) —
  replacing the local gpt-oss schema-extraction and schema-merge passes. Produces a
  seed_schema.json that import_proposal.py turns into a live /review session. Use when
  the operator wants Claude, not Ollama, to propose the ontology.
---

# grace-ontology-proposal

## Role
You (Claude) are the schema extractor + merger. You replace GrACE's native
`schema_extractor` + `schema_merge` (both call gpt-oss; the native Stage-2 detailing
took ~25 min and timed out the UI). You produce a **skeleton-first** proposal: types
and relationships with light properties, ready for human review and ratification.
Property detailing happens later, on the ratified subset only (refined Option B).

## Inputs
- Corpus bundle(s) from **grace-corpus-export**.
- The CQ set authored in **grace-cq-authoring** (the questions the schema must answer).
  Pull them from `GET /api/discovery/cqs` or the DB (`competency_questions`).
- **Seed reference (Option C grounding)** — `workspace/seed_reference_<domain>.md` from
  the step below. Read it so your types align to proven domain ontologies.
- Contract reference: `references/data-contracts.md` (the exact SeedSchema shape).

## Seed grounding (Option C — align to proven domain ontologies)
GrACE ships proven domain ontologies (FIBO, LKIF, Schema.org, PROV-O) as seeds. The
native path feeds them to gpt-oss; on the Claude path you read the SAME reference and
align to it — Claude reasons, the seed grounds. Build the reference first:
```bash
cd ~/grace
.venv/bin/python ~/grace-claude-skills/scripts/export_seed_reference.py --domain legal
# add --provision to parse any uncached seed RDF (rdflib, CPU, no gpt-oss)
```
Then **read `workspace/seed_reference_<domain>.md`** as you author. For each proposed
type/relationship:
- If it matches a seed class, **adopt the seed's name and hierarchy** (e.g. FIBO
  `LegalPerson` → consider naming/parenting your `Legal_Entity` accordingly), and set
  `seed_source` (`fibo|lkif|schema_org|prov_o`), `seed_type_name` (or `seed_rel_name`),
  `seed_alignment` (short note), and `provenance="seed_aligned"`.
- If the documents need a type the seed lacks, **keep it** with `provenance="claude_authored"`,
  `seed_source: null`. The seed grounds the model; it does not constrain it.
- Use the seed to add structure the docs imply but don't spell out (e.g. a Legal_Entity
  subtype hierarchy) — this is where grounding raises quality.

> **Skip seeding** (omit this step, leave `seed_source: null`) when you only need an
> internal ontology that fits the documents and don't care about FIBO/LKIF alignment.
> Grounding matters when standards/interoperability matter.

## Method
1. **Cluster the CQs into entity classes.** Each recurring subject → a candidate type
   (PascalCase_With_Underscores, e.g. `Legal_Entity`, `Insurance_Policy`).
2. **Derive relationships** from RELATIONSHIP/middle-out CQs. Set `richness_tier`:
   - `simple` — bare link, no edge data.
   - `attributed` — the edge carries properties (e.g. ownership_percentage).
   - `reified` — the relationship is itself an entity with its own lifecycle.
2b. **Model transactional facts as first-class event types** (ISS-0005 / validation-run
   F-024 lesson). When CQs ask about amounts, prices, bids, offers, payments,
   closings, or dated commitments ("what did X sell for", "which bid won",
   "when does it close"), those facts need an EVENT/FACT type of their own —
   e.g. `Transaction`, `Bid`, `Payment` — carrying `amount`, `date`/`status`,
   and relationships to the parties. Do NOT expect them to hang off the static
   entities involved; a validation-run graph with no transaction type left every
   purchase-price, closing-date, and bid CQ permanently unanswerable, and the
   gap was flagged independently by the reviewer, the CQ tester, and Signal F.
   Rule of thumb: if the fact has money + a date + two parties, it is a type.
3. **Skeleton properties only.** Add the 2–5 obvious properties per type (the ones CQs
   directly demand — IDs, names, key dates). Do NOT exhaustively detail every property;
   that is deferred to the post-ratify detailing pass.
4. **Map each CQ to the type(s)/relationship(s) that answer it** (`answerable_cqs` and
   the `coverage_matrix`) so the reviewer sees coverage.
5. **Ground in evidence.** Put corpus filenames in `evidence_documents` /
   `evidence_document_count`.
6. **Align to the seed** (if grounding — see the Seed grounding section above): match
   types/relationships to seed classes, adopt their names/hierarchy, and fill
   `seed_source` / `seed_type_name` / `provenance="seed_aligned"`.

## Output format
Emit one JSON object matching `templates/seed_schema.example.json`:
- `entity_types[]` — each with name, description, display_label, plain_description,
  domain, properties[], confidence, answerable_cqs, and seed fields: `provenance`
  (`"seed_aligned"` when grounded, else `"claude_authored"`), `seed_source`
  (`fibo|lkif|schema_org|prov_o` or null), `seed_type_name` (or null), `seed_alignment`.
- `relationships[]` — each with name, source_type, target_type, richness_tier,
  richness_rationale, edge_properties[], confidence, plus `provenance`, `seed_source`,
  `seed_rel_name` (or null).
- `coverage_matrix`, `provenance_summary`, `quality_metrics`, `gap_report` (may be {}),
  `extraction_run_id` (`"claude-authored"`), `industry_profile`, `created_at` (ISO).

Write it to `~/grace/workspace/seed_schema.json`. Leave `answerable_cqs` and
`coverage_matrix` empty — the next step fills them automatically.

## Map CQ coverage (heat-free — replaces the old merge coverage step)
Run `map_coverage.py` to attach which CQ each type/relationship answers. It embeds the
authored CQs and the proposal types/relationships with nomic-embed-text (light, no
gpt-oss) and populates `entity_types[].answerable_cqs`, `relationships[].answerable_cqs`,
`coverage_matrix`, and `quality_metrics.cq_coverage_rate` in place:
```bash
cd ~/grace
.venv/bin/python ~/grace-claude-skills/scripts/map_coverage.py \
  --in ./workspace/seed_schema.json --domain legal
```
The printout lists any **uncovered CQs** — if a CQ has no answering type/relationship,
that is a gap in your proposal; add the missing type/relationship and re-run. This is
the one useful thing the CQ merge used to do, now done at propose time with no merge
stage and no heat.

> **No CQ merge on the Claude path.** The merge only existed to compress weak-model
> redundancy; Claude authors a deduped set, so it is skipped entirely (the native merge
> stays valid for the local-LLM path).

## Next → auto-accept (Step 4), NOT a human review screen
Human ontology ratification is **bypassed** on the Claude path — operator decision
2026-06-10 (see `grace-claude-skills/README.md` §Decisions locked; reviewing types is
meaningless to a non-technical operator). Do not re-add a human gate without
revisiting that decision. Hand the
coverage-enriched `seed_schema.json` straight to **grace-auto-accept**:
```bash
cd ~/grace
.venv/bin/python ~/grace-claude-skills/scripts/auto_accept.py --in ./workspace/seed_schema.json
```
That ratifies the proposal as the active ontology AND syncs the DDL to ArcadeDB — no
review screen, no human gate.

### Optional: open a browsable review record
If you ever DO want the grace `/review` screen for inspection, `import_proposal.py` still
opens a read-only session (it does not gate anything):
```bash
.venv/bin/python ~/grace-claude-skills/scripts/import_proposal.py --in ./workspace/seed_schema.json --reviewer glenny
```
Keep `GRACE_PERMISSION_ENFORCEMENT_ENABLED=0` or `review/start` 403s `no_active_matrix`.

## After ratify — property detailing (deferred)
The native batched Stage-2 detailing engine (`detail_types`, batch_size 4) fills
properties on the **ratified subset only**. That UI action is still pending wiring;
until then you can re-run grace-ontology-proposal on just the ratified types with a
"detail properties fully" instruction, or run the native engine via CLI.

## Heat / safety
- Claude's own inference — no gpt-oss. Keep the model unloaded.
- Writes the live `grace` DB via the API (intended).
