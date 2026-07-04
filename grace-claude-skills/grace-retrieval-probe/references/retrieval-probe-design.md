# Retrieval probe — design + decision record

## ★ NORTH STAR
**Prove the built GrACE graph can answer.** The graph holds the *what* (entities +
relationships) and, where elicited, the *why* (the intent meta-layer, ontology v#9–v#11).
Retrieval is the consume side. This harness drives the real retrieval API and judges the
answer against graph ground-truth. It is a **client** — it never modifies retrieval
internals (CF3). Everything below serves that one sentence.

---

## 1. What this is (and is not)
- **Is:** a client/probe + golden gate that exercises `POST /api/retrieval/query` live and
  scores grounding, recall, ranking, plane (fact vs intent), and serialized-context quality.
- **Is not:** a retrieval implementation, and not a unit test. It reaches the real pipeline
  over HTTP and cross-checks the live `grace` graph. `src/retrieval/*` is FROZEN (CF3); this
  harness reads and judges, it does not touch it.
- **Domain-agnostic:** anchors (a node name, a principle statement, a counterfactual, a seed)
  are discovered from the graph at runtime. Legal corpus is incidental.

## 2. The system under test
`src/retrieval/pipeline.py` — 5 strategies (graph / semantic / bm25 / temporal /
chunk_semantic) → RRF (`rrf_k=60`) → optional iterative round-2 → cross-encoder reranker
(ms-marco MiniLM, CPU) → relationship fetch (Path B / D530, incident edges) → identity
hydration → template serializer. Default config (`RetrievalConfig`):
`serialization_format=template` (no LLM), `temporal_as_strategy=false`,
`iterative_retrieval_enabled=false`, `chunk_semantic_enabled=true`. Heat: nomic-embed-text
(semantic + chunk_semantic) + CPU cross-encoder only.

## 3. Decisions captured this session (the harness contract)

- **D-ret-1 Client/probe topology (CF3).** The harness is a consumer: it POSTs to the API
  and evaluates. It NEVER imports or edits `src/retrieval/*`. Enhancement findings are logged
  with a proposed D-number; they are not implemented in-tree. `pipeline.py` is a PERMANENT
  CF3-exempt file (D467/D530) so serializer-hygiene fixes *can* land there — still behind a
  new D-number.
- **D-ret-2 Grounding before ranking.** Every returned `grace_id` is resolved against
  ArcadeDB before any result is trusted. A non-resolving id is a **phantom** (stale index),
  not a result. This single cross-check is the harness's core value — the API can't tell you
  its own index is stale. 100% resolution is the floor.
- **D-ret-3 Two staleness layers are first-class.** A live 200 response can lie in two ways:
  a stale in-memory **index** (phantom ids) and a stale process **code** (behavior contradicts
  on-disk `pipeline.py`). Both are gated (golden gate GATE-2 / GATE-3) and documented as the
  headline operational finding. Diagnostics: grounding check (index); process-start-time vs
  `pipeline.py` mtime (code).
- **D-ret-4 Plane tagging.** Every result is tagged `fact` / `INTENT` / `SYSTEM`. Retrieval
  reaches the intent plane; you must see *which* plane answered, and whether a SYSTEM vertex
  (Query_Event/…) ever leaks as a result (it should not — the build-index corpus includes them,
  so this is worth watching).
- **D-ret-5 Read the serialized context, not just results.** The `serialized_context` is the
  LLM-facing payload. The harness inspects it for: grounded names, numeric-confidence leaks
  (D120/D217), provenance bloat, and fence survival.
- **D-ret-6 Heat 0.** Only `nomic-embed-text`. `template` serialization is LLM-free; the gate
  asserts no generation model (`gpt-oss`/`llama*`/`qwen2.5:7b`/`mixtral`) is loaded after a
  query. Never route through regeneration/`grace_answer`.
- **D-ret-7 Domain-agnostic anchors.** The probe and gate derive their test inputs from the
  graph (sample a node name; build a why-query from a principle statement; build a fence query
  from a counterfactual name). No domain literals in the tools.
- **D-ret-8 Golden gate = regression guard, AUDIT = known gaps.** Gates encode invariants that
  MUST hold on healthy current code (so a stale-code restart or index drift fails the gate).
  Documented gaps that are *known and accepted pending a D-number* (numeric leak) are reported
  in an informational AUDIT block, not failed — so the gate stays a true signal.

## 4. Findings (the substance — full detail in the session log)
Prioritized; see `../runs/retrieval-probe/session-1-process-log.md` for evidence per finding.

| ID | Sev | Finding | Proposed direction |
|---|---|---|---|
| R-F1 | CRITICAL | Stale-code + stale-index drift silently serve wrong answers behind a 200 | index-freshness signal on `/config` (built_at, indexed vs live count); grounding self-check; rebuild after writes |
| R-F2 | HIGH | Structural / edge-encoded recall fails — `governed_by`→Jurisdiction, `party_to`→Legal_Entity, thin nodes never surface as *results* on unseeded queries | entity-linking / auto-seed so graph strategy fires unseeded; or index edge/neighborhood text |
| R-F3 | HIGH | Entity-scoping fails — a query naming an agreement returns corpus-wide topical matches, not the named subgraph | resolve query mentions → seed the node |
| R-F4 | MED | Dormant strategies — chunk_semantic contributes 0 (0 Document_Chunks) but pays ~80–230 ms/query; temporal filter-only; graph 0 unseeded | COUNT-guard chunk_semantic; revisit RRF/seed defaults |
| R-F5 | HIGH | `confidence_at_verification=0.9` leaks into `serialized_context` (D120/D217) — denylist `_EDGE_INTERNAL_PROP_KEYS` is stale vs review/decay schema | extend denylist or flip to allowlist (in CF3-exempt `pipeline.py`, behind a D-number) |
| R-F6 | MED | Provenance bloat (reviewed_by/verdict/review_rationale-with-full-clause/…) crowds the 2000-token budget | allowlist domain edge props |
| R-F7 | MED | Epistemic fence is implicit-only — a Counterfactual surfaces directly and serializes without `is_term=false`; only the type label/edge-type/name-suffix fence it | serialize `epistemic_status`; D-int-3 / D521-pattern post-fetch filter |
| R-F8 | — | **Headline:** retrieval *reaches* the intent layer (strong for "why"; intrudes into fact answers when topically adjacent) but with **no query-plane routing** | optional query-intent gate (`rationale`/`why`) + explicit fence (R-F7) — architect decision whether planes should separate |

## 5. Validation evidence (why we trust the harness)
- **Grounding** 100% across every probe after rebuild (8/8, 6/6, 5/5).
- **Staleness reproduced and diagnosed live:** phantom ids → `build-indexes` fixed; bare-prefix
  nodes → server start (May 26) vs `pipeline.py` mtime (Jun 10) → restart fixed. Both are now
  golden-gate invariants.
- **Intent reachability proven:** a "why" query returned Rationale+Principle+Counterfactual
  top-3 with the full chain serialized; the gate's GATE-6 surfaced Mandatory_Provision +
  Principle + Rationale on a runtime-derived why-query.
- **Path B (D530)** confirmed: boundary edges (`owed_to`→Xencor Inc., parties) render in the
  serialized context even when the endpoint didn't independently rank.
- **Golden gate 8/8 PASS** on current code; AUDIT correctly flags R-F5.
- **Heat 0** throughout (`ollama ps` empty of generation models).

## 5b. The router fix (session 3, 2026-06-19) — Claude-wrapped structural retrieval
The sessions-1/2 structural-recall failure (~1/6) is solved **in the harness**, outside CF3, by wrapping
Claude-as-the-LLM with a schema prompt + deterministic Cypher execution. Result: **1/6 → 11/11 (100%)** on
the retrieval-fixable axis (golden-gate GATE-9), 0 heat, `src/retrieval/*` untouched.

- **D-ret-9 Route by query class.** A rule-based classifier sends structural/relational/aggregation/negation
  queries to text-to-Cypher and leaves topical + "why"/intent on the existing pipeline (which already handles
  them). The router is a client; it never edits retrieval internals.
- **D-ret-10 Claude generates, a deterministic tool executes.** Mirrors the intent harness's write topology:
  the LLM does the reasoning (NL→Cypher) with zero side effects; `cypher_exec` owns lint + EXPLAIN + execute +
  clean projection. The schema prompt (with **exact edge direction**) is the load-bearing tool — it carried the
  pilot from 94% Cypher-correctness to 100% battery recall by killing the reversed-edge error class.
- **D-ret-11 Heat discipline via direct provider construction.** The router builds `AnthropicProvider`
  directly, never `get_provider()` — the configured provider is `ollama/llama3.3:70b`, so the canonical path
  would load llama and breach the heat rule. Cloud Claude = no local heat.
- **D-ret-12 Two execution modes.** Autonomous (Anthropic API) and Claude-in-the-loop (`--cypher`, the LLM
  supplies the query). The in-loop mode is the proof path this session (stored `LLM_API_KEY` 401s) and the
  operating mode without a valid key; it is faithful to the harness pattern and to how the 50-query pilot ran.
- **D-ret-13 D120/D217 clean by construction on the Cypher path.** `cypher_exec` strips `_embedding`,
  bookkeeping, and `confidence_at_verification` from any node-dicts in results — the structural answer payload
  carries no numeric confidence, sidestepping the frozen serializer's R-F5 leak on this path.
- **D-ret-14 Recall is execution-validated.** A FOUND requires the Cypher to EXPLAIN, execute, and return rows
  containing the expected token. Extraction-gap items (graph lacks the fact — proven by the Task-B audit) are
  tagged and excluded from the retrieval denominator, not hidden.

**Pilot evidence (50 queries, Claude-as-generator):** 100% parse+execute, 94% Cypher-correct, 88% end-to-end;
the 3 Cypher errors were edge-direction, text-conjunction, and morphology — all now addressed by the schema
prompt + `lint_cypher`. **Completeness audit:** 4/5 session-2 structural MISSes were pure retrieval failures
(data present), 1/5 a real extraction gap.

## 6. Build status (2026-06-19 — harness built)
| Piece | File | Status |
|---|---|---|
| Skill | `grace-retrieval-probe/SKILL.md` | ✅ done |
| Design / decision record | `grace-retrieval-probe/references/retrieval-probe-design.md` | ✅ done (this file) |
| Probe / score tool | `scripts/retrieval_probe.py` (`--query/--seed/--mode/--types/--context/--raw`) | ✅ done — grounding + plane tags |
| Golden gate | `scripts/retrieval_golden_gate.py` | ✅ 9/9 PASS (GATE-9 = router structural recall 100%), AUDIT flags R-F5 |
| Session-1 friction log | `runs/retrieval-probe/session-1-process-log.md` | ✅ done — 8 findings + staleness war-story |
| Session-2 parallel battery | `runs/retrieval-probe/session-2-parallel-battery.md` | ✅ done — 24-query / 4-agent recall-by-class |
| **Router fix (cypher_exec + router + battery + run_battery)** | `scripts/cypher_exec.py`, `scripts/retrieval_router.py`, `scripts/run_battery.py`, `runs/retrieval-probe/structural_battery.json` | ✅ done — **structural recall 1/6 → 11/11 (100%)** |
| Session-3 fix log | `runs/retrieval-probe/session-3-router-fix.md` | ✅ done — build + proof + caveats |
| Session-4 harness acceptance (6 parallel agents) | `runs/retrieval-probe/session-4-harness-acceptance.md` | ✅ done — scorecard + 6 findings (S4-1…S4-6) |
| **Session-4 findings FIXED** (agg/neg hard-route + false-success warning; vague entity-anchoring + `--anchor` + deal-summary `node_summary`; no-truncation; empty-set affirmation; schema cookbook) | `cypher_exec.py`, `retrieval_router.py`, `retrieval_probe.py` | ✅ done — golden gate **10/10** (GATE-10 router_safety) |
| Session-5 fix log | `runs/retrieval-probe/session-5-fixes.md` | ✅ done |
| Session-6 acceptance re-run (18 queries) | `runs/retrieval-probe/session-6-acceptance-rerun.md` | ✅ done — **accuracy 18/18 = 100%** across all 6 axes |
| **Session-6 findings FIXED** (S6-1 intent-chain principle edges; S6-3 `--expect-count` + reconciliation footer; S6-4 stored-count note + transitive cookbook; S6-5 surface-form note; S6-2 steer + in-tree D-candidate) | `cypher_exec.py`, `retrieval_router.py` | ✅ done — golden gate **10/10**, GATE-10 extended to 6 checks |
| Session-7 fix log | `runs/retrieval-probe/session-7-s6-fixes.md` | ✅ done |

**No repo `src/` changes** (CF3) — the harness is pure client. The only operational action
taken against the running system was a **dev-server restart** (the live uvicorn was 24-day-stale
code) + an index rebuild — both reversible, both within the prompt's scope.

## 7b. In-tree (CF3) enhancement candidates surfaced by the harness (for the architect)
These need a `src/retrieval/` change → a new D-number + CF3 treatment (mostly `pipeline.py`, the PERMANENT
CF3-exempt file). The harness logs them with live evidence; it does NOT implement them.
- **S6-2 — semantic reranker cross-deal leak.** A "Xencor" query surfaces "Alliance Bancorp" obligations.
  The frozen cross-encoder candidate set/reranker has no entity-scope. Proposed: an entity-scope post-filter
  on the reranked candidates in `pipeline.py` when the query resolves to a node. Harness mitigation already
  shipped: vague/entity queries route to the leak-free anchored path + an intent-route steer to `--anchor`.
- **R-F2/R-F3 (sessions 1–2)** structural/edge recall + entity-scoping inside the semantic pipeline — largely
  superseded for the operator by the router's Cypher path, but still relevant for pure-semantic callers.
- **R-F5/R-F6** serializer numeric-leak + provenance bloat (denylist→allowlist in `pipeline.py`).
- **R-F7** explicit epistemic fence flag in serialization.

## 7. Deferred / follow-on (do not lose)
1. **R-F1 freshness signal** — highest-leverage operational fix; surface index built_at +
   indexed-vs-live entity count so drift is visible without the probe.
2. **R-F2/R-F3 entity-linking / auto-seed** — the biggest *quality* gap; the structural-recall
   failure is what makes "which agreements are governed by X / who are the parties" fail. Needs
   a D-number + `src/retrieval` change (CF3 allowlist treatment).
3. **R-F5/R-F6 serializer hygiene** — denylist→allowlist in `pipeline.py` (CF3-exempt) closes
   the numeric leak + provenance bloat together; behavior change → D-number.
4. **R-F7 explicit fence** — serialize `epistemic_status` / D521-pattern post-fetch filter
   (already flagged by D-int-3).
5. **R-F8 query-plane routing** — architect decision: should fact and intent planes be
   separated/routed at query time, or is type-labelled co-mingling acceptable?
6. **Next probe sessions** — re-run the gate after any of the above lands; add a federation-on
   probe (the route has a federation branch the single-namespace graph never exercised).
