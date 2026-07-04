# grace-claude-skills

A skill bundle that runs GrACE onboarding **end-to-end with Claude Desktop as the LLM
instead of local gpt-oss:120b** — so every heat-producing inference step (CQ generation,
ontology proposal, property detailing, graph extraction) happens in Claude, not on the
host. Distilled from the 2026-06-09/10 sessions.

> **STAGED — not yet in the grace repo.** These files live in `~/grace-claude-skills/`.
> Review them here; move the skill dirs into `~/.claude/skills/` (and/or the repo
> `skills/`) once you're happy. The `scripts/` import grace's own models, so they stay
> alongside or get copied with a known path.

> **Paths in examples.** Shell examples below assume the repo is checked out at
> `~/grace-CLLM-deploy` (with the skills under `grace-claude-skills/`); adjust `~/grace`
> / `~/grace-claude-skills` to your checkout. The scripts honor `GRACE_ROOT` when the
> repo lives elsewhere.

## The skills (cards)
| Skill | Step | What it does | LLM? |
|-------|------|--------------|------|
| **grace-corpus-export**     | 1 | Dump balanced per-domain document text for Claude to read | none (Postgres) |
| **grace-cq-authoring**      | 2 | Claude authors CQs → import into live pipeline | **Claude** |
| **grace-ontology-proposal** | 3 | Claude authors the skeleton proposal + heat-free coverage | **Claude** |
| **grace-auto-accept**       | 4 | Auto-accept the proposal → active ontology + ArcadeDB DDL sync (human ratify bypassed) | none (HTTP) |
| **grace-property-detailing**| 5 | *(optional)* Claude fills full properties → re-accept new version | **Claude** |
| **grace-graph-extraction**  | 6 | Claude extracts entities/relationships per doc → ArcadeDB | **Claude** |
| **grace-review-protocol**   | 7 | Human-in-the-loop tranche gate — facilitate fact review IN PLACE over the seeded graph (catch the extractor's mistakes) | **Claude** (facilitator) |
| **grace-intent-elicitation**| 7b | Human-in-the-loop — extract human intent + rationale (the *why*) and bind it to facts as a queryable graph layer (ontology v#9 intent meta-layer). Sibling to 7: review catches what's wrong, this captures why it's right | **Claude** (facilitator) |
| **grace-retrieval-probe**   | A1 | **Consume side** — prove the built graph can *answer*. Drives `POST /api/retrieval/query` + a Claude-wrapped Cypher router (text-to-Cypher → lint + EXPLAIN + execute); scores grounding/recall; fixes the structural-recall gap (1/6 → 100%) outside CF3. Golden gate 10/10. | **Claude** (text-to-Cypher) |
| **grace-regeneration-probe** | A2 | Prove the graph can *answer in prose* — drives the D193-frozen PromptAssembler read-only, Claude decompresses the grounded context, scores **faithfulness** | **Claude** (decompression) |
| **grace-signal-probe**      | A3 | Prove the graph *notices* its own ontology gaps — drives the six detectors (Signals A–F) via the sanctioned D246 CLI; scores recall / precision / substrate honesty | none (heat-free CLI) |
| **grace-gap-remediation-harness** | A3 | Close the loop — Claude proposes ONE KGCL schema change per fired gap signal, scored on four co-signals (groundedness, well-formedness, gap-closure, non-regression) | **Claude** (proposer) |
| **grace-correlation-probe** | A4 | Prove signals *correlate* into cross-module root-cause diagnoses — drives the correlation engine via its D246 CLI; Claude runs as the co-signal reasoner | **Claude** (reasoner) |
| **grace-ingestion-harness** | C1 | Prove email → trustworthy graph facts — drives the ingestion CLIs over a Claude-authored synthetic golden email corpus in the grace_test sandbox | **Claude** (corpus author) |
| **grace-testing-protocol**  | — | Three-tier tests, grace_test isolation, heat/Ollama rules (infra) | none |

> **Two tracks now.** Skills 1–7b are the **onboarding/produce** track (seed the graph).
> The A1–A4 probes and the C1 harness form the **consume/test** track (make the graph
> answer, self-monitor, and ingest); `grace-testing-protocol` is shared infra. The
> Claude-as-LLM module-test effort is indexed in `module-test-roadmap.md`; each tested
> module becomes a sibling skill with its own `references/` design record + runnable probes
> + a golden gate (the `grace-retrieval-probe` shape).

All LLM steps run on Claude, so **gpt-oss:120b can stay unloaded for the whole
onboarding.** Only nomic-embed-text (propose-time coverage, light) and Docling (CPU) run
locally. The CQ merge is dropped on the Claude path (see note below).

## Helper scripts (`scripts/`)
- `export_corpus.py` — STEP 1, balanced corpus export (reuses grace's own batcher).
- `import_cqs.py` — STEP 2 persist; validates against `CompetencyQuestion`, `bulk_create_cqs`.
- `export_seed_reference.py` — STEP 3 seed grounding (Option C): renders FIBO/LKIF/Schema.org/PROV-O for a domain via grace's own `format_for_llm` so Claude aligns to proven ontologies. Heat-free.
- `map_coverage.py` — STEP 3 propose-time coverage (nomic-embed; fills `answerable_cqs` + `coverage_matrix`). **Replaces the merge's coverage role, heat-free.**
- `auto_accept.py` — STEP 4: ratify the proposal as active ontology + `POST /api/graph/sync-schema` to ArcadeDB. **Replaces the human review/ratify gate.**
- `import_proposal.py` — *(optional)* opens a browsable grace `/review` record; not required on the bypass path.
- `export_proposal_for_detailing.py` — STEP 5 prep; slims the proposal to the kept subset.
- `import_extraction.py` — STEP 6 persist; two-phase bulk-insert + grace_id lookup.
- `safe_pytest.sh` — pytest wrapper enforcing grace_test isolation.
- `_common.py` — shared repo-root/sys.path/DB-session bootstrap.

> **No CQ merge on the Claude path.** The merge only compressed weak-model redundancy;
> Claude authors a deduped, type-classified set, so it is skipped. Coverage (the only
> useful merge output) is produced at propose time by `map_coverage.py`. The native
> merge stays valid for the local-LLM onboarding path.

## References (`references/`)
- `workflow-pipeline.md` — the full Ollama→Claude substitution map (start here).
- `data-contracts.md` — exact DB/API field shapes the scripts validate against.
- `cq-authoring-method.md` — the combined/A3 multi-perspective CQ method.

## Full quick-path (uvicorn running; gpt-oss UNLOADED)
```bash
cd ~/grace
P=.venv/bin/python; S=~/grace-claude-skills/scripts

$P $S/export_corpus.py                                              # 1 export corpus
#   → grace-cq-authoring: Claude writes workspace/cqs.json          # 2 author CQs
$P $S/import_cqs.py --in ./workspace/cqs.json --domain legal
$P $S/export_seed_reference.py --domain legal                            # 3 seed grounding (Option C, optional)
#   → grace-ontology-proposal: Claude authors + aligns workspace/seed_schema.json  # 3 propose
$P $S/map_coverage.py --in ./workspace/seed_schema.json --domain legal   # 3 coverage (heat-free)
$P $S/auto_accept.py  --in ./workspace/seed_schema.json                  # 4 auto-accept: ratify + ArcadeDB sync (no human gate)
#   → (optional 5) grace-property-detailing: Claude details props, then map_coverage + auto_accept again
#   → grace-graph-extraction: Claude writes workspace/extraction_<doc>.json  # 6 extract
$P $S/import_extraction.py --in ./workspace/extraction_<doc>.json --doc-id <UUID> --module legal
#   → inspect at http://localhost:3000/graph
```

## Decisions locked (operator, 2026-06-10)
1. **CQ import status → ACCEPTED** (default) so the canonical pipeline treats Claude
   CQs as review-ready.
2. **CQ merge dropped on the Claude path** (2026-06-10). The merge only compressed
   weak-model redundancy; Claude authors a deduped set. Coverage moved to propose time
   via `map_coverage.py` (heat-free). Native merge stays valid for the local-LLM path.
3. **review/start merge_run_id → synthetic by default.** Verified in source:
   `review_ops.start_review_session` stores `merge_run_id` for traceability only and
   **never dereferences it** — it builds the session directly from `seed_schema_data`,
   so any id is accepted. With the merge dropped, `import_proposal.py` falls back to a
   synthetic `claude-proposal-<uuid>` id (still auto-uses a real merge-latest id if one
   happens to exist).
4. **CQ provenance → HUMAN_AUTHORED** (operator-curated), not a claude-desktop tag.
   `metadata_extra.authoring_method="combined-a3"` retained for audit.
5. **Human ontology ratification → BYPASSED** (2026-06-10). `auto_accept.py` ratifies +
   syncs the Claude proposal programmatically; grace's `/review` + ratify code stays but
   is not required. Verified standalone: `POST /api/ontology/ratify` accepts a hand-built
   `schema_json` with no review session, then `POST /api/graph/sync-schema` pushes DDL to
   ArcadeDB. See memory `project-skip-human-ontology-ratification`.
6. **Seed grounding → Option C (kept, Claude-as-context)** (2026-06-10). The proven
   domain seeds (FIBO/LKIF/Schema.org/PROV-O) are fed to **Claude** as reference via
   `export_seed_reference.py` (reuses grace's `seed_registry` + `format_for_llm`), so the
   ontology aligns to standards without gpt-oss. Optional per run — skip it for a purely
   internal ontology; use it when interoperability/standards alignment matters. Claude
   fills `seed_source`/`seed_type_name`/`provenance="seed_aligned"` on aligned elements.

## Known limitations to verify before trusting at scale
- **Auto-accept = `source="manual"`, version N+1 each run.** Each `auto_accept.py` ratifies
  a new active `OntologyVersion` (OM4OV diff from predecessor); `sync-schema` is idempotent
  per version. Property detailing (Step 5) re-accepts a new version (no in-place property
  write-back endpoint exists — confirmed in source).
- **Graph extraction has no entity resolution / dedup yet.** Endpoints match by
  `(entity_type, name)` via the lookup endpoint; re-running on overlapping docs can create
  duplicate vertices. Run docs once, or add a resolution pass before scaling. The lookup
  also assumes each type carries a `name` property.
- **Accept-before-extract.** Graph insertion requires the ontology active AND synced to
  ArcadeDB (Step 4 `auto_accept.py` does both); undefined types fail at the DB.

## Non-negotiables baked in
- **Keep gpt-oss:120b unloaded.** The whole point is to not run it. `ollama stop`.
- **Never test against the live `grace` corpus.** Use `safe_pytest.sh` / grace_test.
- **Keep `GRACE_PERMISSION_ENFORCEMENT_ENABLED=0` during onboarding** (else review/start
  403s `no_active_matrix`).
- **Use the repo venv** (`~/grace/.venv/bin/python`); system python3 may be 3.9.
