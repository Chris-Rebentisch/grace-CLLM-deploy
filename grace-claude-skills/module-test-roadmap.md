# GrACE module × harness test roadmap (Claude-as-LLM path)

**Updated:** 2026-06-19. **Protocol for every module:** prove it works END-TO-END through live
action → track friction/enhancements → write the skill + harness. (Same protocol that produced
`grace-review-protocol` and `grace-intent-elicitation`.)

"Tested" here means **exercised end-to-end live on real data via a Claude-driven harness** — not
the pytest unit suite (which is separate and green). Heat rule throughout: only `nomic-embed-text`
may load; `gpt-oss` / `llama3.3:70b` stay unloaded.

---

## ✅ Tested — modules with a live harness

| Module | Harness (skill) | What was proven live | Evidence |
|---|---|---|---|
| `discovery` | grace-corpus-export (1), grace-cq-authoring (2), grace-ontology-proposal (3) | Docling export → CQ authoring → schema-proposal end-to-end | `processed_documents` (25), `competency_questions`, `merge_runs` |
| `ontology` | grace-auto-accept (4), grace-property-detailing (5), **grace-intent-elicitation (7b)** | review/ratify + hash chain; **intent meta-layer v#9–v#11** (principles, rationale, counterfactuals, mandatory-provisions, depends_on, specializes) | `ontology_versions` v1–v11, chain valid; intent layer golden gate 8/8 |
| `extraction` | grace-graph-extraction (6), grace-review-protocol (7), grace-intent-elicitation (7b) | `eval_checkpoint` graph extraction; `graph_review_writer` (review-in-place); `intent_writer` (intent) | 509 obligations, 21+ intent nodes, idempotent |
| `retrieval` | **grace-retrieval-probe (A1)** | `POST /api/retrieval/query` driven live (grounding 100%, intent "why" reachable, Path B confirmed); **structural-recall failure (1/6) FIXED via Claude-wrapped router** (text-to-Cypher → lint+EXPLAIN+execute) → **11/11 (100%)**, CF3-untouched | golden gate **9/9** (GATE-9=router recall 100%); sessions 1–3 logs; 50-query Cypher pilot (88% e2e) |
| `regeneration` | **grace-regeneration-probe (A2)** | Subgraph → NL answer decompressed by Claude; **FAITHFULNESS** scored deterministically (entity/number/snake-id grounding + abstention + Layer-2 graph edge check). Proved faithfulness is **context-relative** (same answer 0% native / 100% A1-Cypher context), the scorer catches entity + relational hallucination, intent reaches as STRUCTURE but the serializer drops the reasoning PROSE (G-F5), intent intrudes cross-deal into fact queries (G-F7). D193-untouched. | golden gate **15/15** + AUDIT (G-F1/2/3/5); sessions 1–4 logs |
| `graph` | (all of the above) | `arcade_client` / `entity_ops` / `relationship_ops`, D106 dedup, vector index | 262+ vertices / 629+ edges live |
| `elicitation` | grace-review-protocol, grace-intent-elicitation | append-only event capture | `elicitation_events` |
| `mcp_server` | grace-review-protocol | **5 of ~37 tools** (the read tools: `grace_relationship_coverage`, `grace_graph_counts`, `grace_graph_aggregate`, `grace_get_neighborhood`, `grace_get_entity`) | review queue driver |
| `shared` | (all) | `embeddings` (nomic), `llm_provider` via the Claude path | throughout |

**Partial / gaps inside tested modules:**
- `extraction.confidence_decay` — decay stamps are written, but the **decay batch has never run** (see Tier B).
- `extraction.image_pipeline` + `discovery` image-OCR — the **image/photo input modality** (Chunk 77a/77b, D499–D503: Docling `InputFormat.IMAGE` OCR + `generate_vision` → `Image_Asset` / `PhotoObservation` / `Document_Chunk` / `derives_from`) ships with a green pytest suite but has **never been run Claude-path end-to-end live**. This is the actual insurance use case (damage photos, scanned forms, handwritten reports). Tracked as **C1-IMG** below.
- `mcp_server` — only the 5 read tools are exercised; the **write-review, session, and other ~30 tools** are not systematically tested.

---

## ❌ Needs testing — module → harness to create

Priority = value to the Claude-as-LLM KG goal × heat-safety × dependency order.

### Tier A — consume the graph (highest value; the KG must now *answer*, not just store)
| # | Module | What end-to-end live proof looks like | Harness/skill to create | Heat |
|---|---|---|---|---|
| ~~A1~~ ✅ | `retrieval` | **DONE 2026-06-19** — `grace-retrieval-probe` (committed `3d1e462`), golden gate **10/10**. 7 sessions: proved grounding 100%, intent "why" reachable, Path B (D530); found + FIXED the structural-recall gap (1/6 → **100%**, 18/18 across 6 axes via 6 parallel harness-only agents) **in-harness via the Claude-wrapped router** (`retrieval_router.py` text-to-Cypher + `cypher_exec.py` lint+EXPLAIN+execute), **CF3 untouched**. Also: agg/neg hard-route + false-success guard, vague entity-anchoring (`--anchor`), reconciliation/empty-set footers, intent-chain enrichment. 50-query Cypher pilot 88% e2e. In-tree candidates logged for architect (design record §7b; Backlog test-track pointer). | **grace-retrieval-probe** ✅ | heat-free (Claude=cloud / in-loop; nomic local only) |
| ~~A2~~ ✅ | `regeneration` | **DONE 2026-06-19** (re-run + hardened 2026-06-22) — `grace-regeneration-probe`, golden gate **15/15** + AUDIT. Completes the consume side: A1's grounded subgraph → Claude decompresses → **FAITHFULNESS** scored deterministically. Heat-safe via the real `PromptAssembler` called read-only (never `get_provider()`/the live endpoint, which loads `llama3.3:70b`). Proved: **faithfulness is context-relative** (identical answer 0% on weak semantic context / 100% on A1-Cypher context — regeneration faithfulness is **bounded by retrieval**); the scorer catches invented entities/numbers/principle-ids (Layer-1) and relational hallucination (Layer-2 graph edge check, composes A1 `cypher_exec`); abstention credited as faithful; **intent reaches as STRUCTURE but the template serializer drops the reasoning PROSE** (`why_rejected`/principle `statement`) — rich "why" only faithful when the prose is composed in (G-F5); intent **intrudes cross-deal** into fact queries (G-F7 = A1 R-F8). In-tree D193 carve-out candidates logged for architect (design record §7). | **grace-regeneration-probe** ✅ | heat-free on the Claude path (NEVER the regeneration endpoint, which loads `llama3.3:70b`) |
| ~~A3~~ ✅ | `analytics.signal_pipeline` | **DONE 2026-06-22** — TWO harnesses. `grace-signal-probe` (detection fidelity, golden gate **10/10**): proved B/D/F fire on seeded `grace_test` gaps with sensible strength + trend (recall) and stay quiet on healthy/flat controls (precision), and NEVER false-fire on empty substrate (substrate honesty). On the live GOLD corpus all six correctly no-op (extraction_claims single-day + `ontology_module` NULL; cq_test_runs empty; Prometheus `grace_*` empty). A/C/E run + no-op; firing needs TSDB backfill (no pushgateway/remote-write ingress) — documented follow-on. `grace-gap-remediation-harness` (remediation quality, golden gate **10/10**): Claude (in-loop, edge) proposes ONE KGCL fix from a fired signal + evidence; scored on 4 co-signals — **groundedness** (faithfulness on the rationale, abstention credited), **well-formedness** (`change_executor parse`); **gap-closure (closure-readiness) + non-regression (CQ-gate on qwen2.5:7b) BUILT** as `apply_probe.py` (golden gate 8/8, HEAT-BOUNDED — first non-heat-0 gate; 70B forbidden & never reachable). Proved on a real GOLD Signal-B orphan pair that neither co-signal alone suffices (malformed baseline fails well-formedness; ungrounded `competes_with` fails groundedness). Heat 0 throughout; pipeline verified LLM-free; config model swapped 70B→qwen2.5:7b defensively. In-tree candidate logged: `signal_mapping.py` emits non-well-formed `create edge … between …` for B/F (grammar wants `create relationship '<Name>'`). A1+A2 re-run green (10/10, 16/16). | **grace-signal-probe** ✅ + **grace-gap-remediation-harness** ✅ | heat-free, CLI (Claude in-loop) |
| ~~A4~~ ✅ | `analytics.correlation_engine` | **DONE 2026-06-22** — `grace-correlation-probe`, golden gate **13/13** + AUDIT. Proved DETECTION FIDELITY for the cross-module PATTERNS: seeded signal COMBINATIONS into `grace_test` directly (the D252 input) → the right pattern fires with the right root-cause module + strength band (recall: `schema_drift_per_module`→ontology 0.75 on C+D; `cq_regression_pre_extraction`→discovery 0.90 on F), stays quiet on unsatisfied conjunctions (precision: C-only, E-only, F<0.5), abstains on lone weak signals (no cry-wolf), and never false-fires on empty `analytics_signals` (substrate honesty). The 3 Prometheus-gated patterns (`extraction_quality_problem`, `graph_or_index_problem`, `relationship_gap_propagation`) correctly NO-OP without TSDB telemetry — firing needs pushgateway backfill (documented follow-on, mirrors A3 A/C/E). Ran **Claude AS the correlation reasoner** over the same D252 bundle (`correlation_compose`→in-loop→`correlation_score`): groundedness **1.0**, consistency-vs-engine **1.0** on shared fires, abstains on thin sets, **RICHER** on an A+C case the engine misses; the co-signal scorer FAILs a hallucinated/wrong/incomplete diagnosis. Claude surfaced a cross-signal root cause the static 5-pattern library missed (E domain/range + B missing-edge same module → ontology) — **wired deterministically as the 6th pattern `ontology_constraint_conflict` (D535, amends D250)** with full discipline (migration `d535_diag_pattern_6th`, new gauge GOLDEN_NAMES 151→152, unit tests, capture-the-why). Engine verified LLM-free; heat 0; GOLD untouched. A1/A2/A3 re-run green (10/10, 16/16, 10/10+11/11). | **grace-correlation-probe** ✅ | heat-free, CLI (Claude in-loop) |
| **A5** | `eval` | DeepEval regression on the onboarded graph | **grace-eval-probe** (`python -m src.eval run-suite`) | needs a judge model (qwen2.5:7b) |

### Tier B — close the autonomy loop (the review/intent sessions PRODUCE these inputs)
| # | Module | End-to-end live proof | Harness/skill | Heat |
|---|---|---|---|---|
| **B1** | `ontology.calibration_updater` + `agent_daemon` | Feed the tranche-review + intent-session outcomes (the `tranche-outcome-*.json`, confirm bands) into trust scores; watch Earned Autonomy lighten the next tranche | **grace-calibration-harness** | heat-free, CLI |
| **B2** | `extraction.confidence_decay` | Run the decay batch over the graph (stamps already present); age facts, watch confidence bands move | **grace-decay-probe** (CLI `--observation-time`) | heat-free, CLI |

### Tier C — other input modalities / governance (orthogonal to the document path)
| # | Module | End-to-end live proof | Harness/skill | Heat |
|---|---|---|---|---|
| **C1** | `ingestion` (communications / email) | Email → triage → graph; voice/tone; thread reconstruction; corroboration | **grace-ingestion-harness** (a whole second input modality) | mixed; CLI |
| **C1-IMG** | `extraction.image_pipeline` + `discovery` image-OCR (Chunk 77a/77b) | **The image/photo modality — the actual insurance use case.** Drop a damage photo / scanned form / handwritten report into the corpus → `POST /api/extraction/jobs` `job_kind='image'` → CLI spawns `image_pipeline` → OCR (77a) + `generate_vision` photo-class (77b) → `Image_Asset` (+ `PhotoObservation`, `Document_Chunk`, `derives_from`) in ArcadeDB. Prove: doc-image OCR text lands as chunks; real-world photo gets a structured `PhotoObservation`; sensitivity tags + D470 path-allowlist + D232 airgap guard hold; image facts are retrievable. | **grace-image-ingestion-harness** — author an image probe set, drive the jobs route, score what landed in the graph; golden gate on `Image_Asset`/chunk creation + airgap routing | **split:** 77a OCR **heat-free & runnable now** (OcrMac confirmed installed — Apple Vision, airgap-compliant, no LLM); 77b vision needs a vision model → won't fit 18 GB locally, so route to **cloud Claude** (`LLM_API_KEY`) — gated like A2/A5 |
| **C2** | `change_directives` | Author an intentional-change directive + EvidenceCriterion NL→Cypher, watch realization snapshots | **grace-directive-harness** | heat-free |
| **C3** | `permissions` / sensitivity | Hypothesis → matrix ratify → drift; sensitivity tagging + two-zone enforce | **grace-permissions-harness** | heat-free |
| **C4** | `decomposition` | Org-decomposition pipeline over an archive (has data; never Claude-path tested) | **grace-decomposition-harness** | heat-free, CLI |
| **C5** | `federation`, `connectors`, `support` | Namespace federation; a connector sync; a remote-support session | three small harnesses | heat-free |
| **C6** | `mcp_server` (remainder) | Exercise the write-review + session + remaining tools through Claude Desktop | extend grace-review/intent harnesses | heat-free |

---

## Recommended order
~~**A1 retrieval**~~ ✅ done 2026-06-19 (`grace-retrieval-probe`). ~~**A2 regeneration**~~ ✅ done 2026-06-19
(`grace-regeneration-probe`) — the consume side is **complete**. ~~**A3 signal-pipeline**~~ ✅ done 2026-06-22
(`grace-signal-probe` + `grace-gap-remediation-harness`): the self-monitoring + remediation-proposal layer
is now proven — detectors fire on seeded gaps with fidelity, and Claude proposes grounded, well-formed KGCL
fixes scored on 4 co-signals. ~~**A4 correlation**~~ ✅ done 2026-06-22 (`grace-correlation-probe`): the
diagnosis layer is now proven — cross-module patterns fire on real signal COMBINATIONS without crying wolf,
Claude correlates faithfully (groundedness/consistency/richness), and the gap Claude exposed is wired in
(D535, the 6th pattern). **Next: B1 calibration** (closes the loop the review/intent sessions feed). The
**qwen-gated apply follow-on** (remediation co-signals 3 gap-closure + 4 non-regression) is the natural
extension once B1 lands. **A5 eval** still wants a judge model (Claude can serve as the DeepEval judge —
also unblocked). Two in-tree (D193/CF3) enhancement candidates are now logged for the
architect from A2: **intent-prose serialization** (G-F5, highest-leverage for the intent layer's value) and
**serializer hygiene** (G-F3 = A1 R-F5, the numeric leak).

**Out-of-tier callout — C1-IMG image/photo ingestion** is the user's actual insurance use case and its
**77a OCR half is heat-free and runnable right now** (OcrMac confirmed installed), so it can jump the Tier-C
queue whenever the insurance corpus is the focus. Only the 77b *vision* half (real-world photo understanding)
is gated on a vision model (cloud Claude via `LLM_API_KEY`, since Qwen2.5-VL won't fit 18 GB) — so a first
session can prove the document-image OCR → `Document_Chunk` path end-to-end without touching the heat budget,
and defer the vision half.

Each becomes a sibling skill under `~/grace-claude-skills/` with its own `references/` and harness scripts,
following the `grace-intent-elicitation` shape: SKILL.md (north star + science + flow) + a design/decision
record + runnable `*_probe.py` / `*_apply.py` tools + a golden-gate validator.
