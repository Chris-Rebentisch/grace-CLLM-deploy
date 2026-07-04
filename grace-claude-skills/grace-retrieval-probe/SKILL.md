---
name: grace-retrieval-probe
description: >
  Prove a built GrACE knowledge graph can ANSWER, not just store. The graph holds
  the WHAT (entities + relationships) and, where elicited, the WHY (the intent
  meta-layer). This skill is the consume side: it drives the real retrieval API
  (POST /api/retrieval/query — 4-strategy search + RRF + cross-encoder reranker +
  template serializer), then SCORES what comes back against graph ground-truth —
  grounding, recall, ranking, plane (fact vs intent), and the serialized context
  an LLM would actually receive. It is a CLIENT/PROBE: it never modifies retrieval
  internals (CF3 lock). Use after a tranche has seeded the graph, to validate that
  retrieval surfaces the right grounded subgraph — and to catch the two silent
  failure modes (a stale in-memory index and a stale server process) that make a
  200-OK response lie.
---

# grace-retrieval-probe

## ★ NORTH STAR
**Prove the graph we built can answer.** Storing facts and intent is only half the
system; the payoff is retrieval returning the *right grounded subgraph* for a real
question. This skill exercises the real retrieval pipeline live and judges it against
the graph — it does not reimplement retrieval, and it never trusts a 200 response
without checking the answer against ground-truth.

## Role
You are the **retrieval driver + judge, outside the retrieval loop.** You author real
query sets you can verify against the graph, run them through the live API, and score
grounding / recall / ranking / plane. You are **not** part of the retrieval computation
— in the default `template` serialization the pipeline calls **no LLM at all** (pure
embeddings + BM25 + CPU cross-encoder), so there is no "doing it through Claude." Your
judgment is the evaluation layer the API doesn't provide.

## When this runs
- **Trigger:** a graph is seeded (facts exist; intent may exist) and you want to confirm
  retrieval answers, or to regression-check after graph/code/index changes.
- **Sibling to** `grace-review-protocol` (validates facts) and `grace-intent-elicitation`
  (captures why). Those *produce* the graph; this one *consumes* it.
- **Domain-agnostic:** the probe and gate discover anchors from the graph at runtime
  (a node name, a principle statement, a counterfactual). Nothing legal-hardcoded.

## The system under test (read, don't change — CF3)
`POST /api/retrieval/query` → `src/retrieval/pipeline.py`:
1. **Five strategies** run concurrently: `graph` (variable-depth traversal — needs
   `seed_entity_ids`), `semantic` (nomic-embed-text ANN over node text), `bm25`
   (keyword), `temporal` (filter-only unless `temporal_as_strategy`), `chunk_semantic`
   (ANN over `Document_Chunk._embedding`, D466/D467).
2. **RRF fusion** (`rrf_k=60`) → optional **iterative round-2** (`iterative_mode=on|auto|off`).
3. **Cross-encoder reranker** (ms-marco MiniLM, CPU).
4. **Path B / D530** `_fetch_relationships` — *incident* edges (`a IN seeds OR b IN seeds`)
   so boundary edges (governing-law, parties, counterparties) survive even when the far
   endpoint didn't rank; `_hydrate_result_identities` restores real `name`/`type`.
5. **Template serializer** builds `serialized_context` — the payload an LLM consumes.

**CF3 lock:** `src/retrieval/*` is FROZEN except 7 allowlisted files. This harness is a
**client** — it POSTs and evaluates. **Log enhancement findings; do not implement them
inside `src/retrieval/`.** Any real retrieval change needs a new D-number + CF3 allowlist
treatment. `pipeline.py` is a PERMANENT CF3-exempt file (D467/D530), so serializer-hygiene
fixes land there — but still behind a D-number.

## The two silent failure modes (load-bearing — a 200 can lie)
Validated live (session 1). Both are invisible in a raw response:
1. **Stale in-memory INDEX.** The semantic + BM25 indexes are a process-lifetime snapshot;
   they drift from the graph after any write. Symptom: returned `grace_id`s that **don't
   exist in the graph** (phantoms that look like normal ids). Fix: `POST /api/retrieval/build-indexes`
   (admin-key when `GRACE_ADMIN_KEY` set; loopback bypass otherwise). **Always rebuild
   after the review/intent harnesses mutate the graph.**
2. **Stale process CODE.** A long-running uvicorn serves whatever code it booted with
   (no `--reload`). Symptom: behavior that contradicts on-disk `pipeline.py` — e.g. nodes
   serialize as bare `Entity: Entity "Obligation"` because the D530 hydration isn't in the
   running process. Fix: restart from the venv. Check: process start time vs
   `pipeline.py` mtime.
**Discipline:** never report a retrieval result without the **grounding check** (every
returned id must resolve in the graph). That single cross-check exposes failure mode 1
and corroborates 2.

## The fix this harness ships: the Claude-wrapped retrieval router
Sessions 1–2 measured the failure: structural/relational questions (governing law, parties, territory,
"how many") scored ~1/6 because the frozen pipeline ranks NODE TEXT and the answers live in EDGES. The
harness solves this **outside CF3** by routing per query class:
- **STRUCTURAL / relational / aggregation / negation → text-to-Cypher.** `retrieval_router.py` builds a
  schema-aware prompt (vertex/edge inventory **with exact edge direction** + key props + 3 generation
  rules), Claude generates one OpenCypher query, and `cypher_exec.py` validates (3-rule lint + ArcadeDB
  EXPLAIN, plan-only) and executes it. Output is grounded by construction and D120/D217-clean (the tool
  strips bookkeeping + numeric confidence). **Measured lift: 1/6 → 11/11 (100%) on the structural battery
  (golden-gate GATE-9).**
- **TOPICAL clause recall + "why"/intent → the existing `POST /api/retrieval/query`.** Those already work
  (sessions 1–2: clause recall strong, intent "why" 6/6); the router is a client of the unchanged pipeline.

Two ways the Claude step runs: **autonomous** (router calls the Anthropic API via `AnthropicProvider`
directly — never `get_provider()`, which would load the configured local llama = heat), or **in-loop**
(`--cypher`: Claude supplies the query it generated from the printed schema prompt; the deterministic tool
executes it). The in-loop path is the operating mode when no valid `LLM_API_KEY` is configured, and is how
the fix was proven this session.

**CF3:** the structural answer is Claude-generated Cypher run directly against ArcadeDB — a client path that
never enters `src/retrieval/*`. No D-number needed for the harness. The in-tree enhancements (node-text
enrichment, `_name_search` fix, serializer denylist) that would help the airgap/semantic path remain logged
for the architect.

## The science (how to judge an answer)
1. **Grounding before ranking.** Resolve every returned `grace_id` against ArcadeDB. A
   phantom id = stale index, not a result. 100% resolution is the floor.
2. **Plane-tag every result** — `fact` vs `INTENT` (Decision_Principle/Rationale/
   Counterfactual/Mandatory_Provision) vs `SYSTEM` (Query_Event/…). Retrieval reaches the
   intent layer; you must see *which* plane answered.
3. **Recall is about the right ANSWER, not topical neighbors.** "Which agreements are
   governed by Delaware?" must return the Delaware *agreements/jurisdiction*, not
   obligations that merely contain "law". Edge-encoded facts (`governed_by`, `party_to`)
   and thin nodes are the hard case — check them explicitly.
4. **Read the serialized context, not just the result list.** The context is what an LLM
   gets. Check: are names grounded? does a numeric confidence leak (D120/D217)? does the
   epistemic fence survive (a Counterfactual must not read as a real term)?
5. **Heat 0.** Only `nomic-embed-text` may load. `template` serialization is LLM-free;
   never set `serialization_format=llm` against a heavy model, never route through
   regeneration/`grace_answer` (loads llama3.3:70b).

## Session flow (step by step)
1. **services** — confirm API `/api/retrieval/config` 200, ArcadeDB reachable, `ollama ps`
   shows no generation model. If the server is long-running, compare its start time to
   `src/retrieval/pipeline.py` mtime (stale-code check).
2. **refresh** — `POST /api/retrieval/build-indexes` if the graph changed since the server
   booted (or if grounding fails). Re-rebuild is cheap and idempotent.
3. **author query sets** — 3–5 queries you can verify against the graph, spanning the
   axes that stress retrieval: a **structural/edge** question (governing law, parties), a
   **specific-entity** question (does it scope?), a **"why"** question (does the intent
   layer answer?), a **pure-fact** question (does intent intrude?), and a **seeded**
   question (does graph traversal fire?).
4. **probe + score** — `scripts/retrieval_probe.py --query "…"` for each. Read grounding,
   plane tags, strategy contributions, and (`--context`) the serialized payload.
5. **log** — record friction + enhancement findings in `runs/retrieval-probe/` (see the
   session-1 log for the format). Distinguish *operational* (staleness), *quality*
   (recall), and *serialization* (leak/fence) findings.
6. **gate** — `scripts/retrieval_golden_gate.py` — 8 domain-agnostic invariants must pass
   on healthy current code; the AUDIT block reports documented gaps.

## What this session proved (session 1, 2026-06-19)
- **Grounding integrity 100%** after rebuild; **Path B (D530)** surfaces boundary edges in
  the serialized context.
- **Intent retrieval is the standout:** a "why" question returns Rationale+Principle+
  Counterfactual as top-3 with the full reasoning chain serialized.
- **Structural recall is the gap:** edge-encoded facts + thin nodes (Jurisdiction,
  Legal_Entity) don't surface as results on unseeded queries; the 5-strategy system
  behaves as semantic+bm25 (graph needs seeds; chunk_semantic is a no-op with 0
  Document_Chunks but still pays embedding latency).
- **Serialization gaps:** a numeric `confidence_at_verification=0.9` leaks into the LLM
  payload (D120/D217); review/decay provenance bloats the token budget; the epistemic
  fence is implicit-only (type label, not an explicit flag).
- Full findings + the staleness war-story: `references/retrieval-probe-design.md` and
  `../runs/retrieval-probe/session-1-process-log.md`.

## Hard constraints
- **CF3:** client/probe only. Never edit `src/retrieval/*`. Log enhancements; don't build
  them in-tree. A real retrieval change = new D-number + CF3 allowlist.
- **Heat:** only `nomic-embed-text`. Never a generation model; never regeneration/`grace_answer`.
- **Grounding discipline:** every result's `grace_id` must resolve in the graph before you
  trust it. Rebuild the index after any graph write.
- **Test-DB isolation:** the probe reads the **live** `grace` graph (that is the point —
  you are validating the real corpus). It is read-only against the graph; it never writes.
  Do not point it at, or wipe, the GOLD corpus from a test path.
- **Domain-agnostic:** anchors are discovered at runtime; nothing legal-hardcoded.

## Tools
- **Structural retrieval (the fix — Claude-wrapped router):**
  - `scripts/retrieval_router.py --query "<nl>"` — classify + dispatch across **4 routes**: structural
    (Claude text-to-Cypher → validate+execute), intent ("why" → semantic **+ a grounded intent chain that
    guarantees principle-layer edges**), **vague** (entity-anchored → deal-summary profile), semantic
    (topical). **Aggregation/negation cues hard-route to Cypher** (the semantic path can't answer them and
    reports false success). Flags: `--cypher "<C>"` Claude-in-the-loop (no API key needed); `--anchor
    "<entity>"` force the deal-summary path; `--no-llm` print the generation prompt; `--force
    structural|semantic|intent|vague`; `--expect "<sub>"`; `--expect-count N` (assert exact row count).
  - `scripts/cypher_exec.py --schema` (print the directed schema prompt **+ COOKBOOK**: lookup-on-name,
    negation both-directions, multi-hop, transitive, aggregation, stored-count + surface-form notes) /
    `--cypher "<C>"` (deterministic lint + EXPLAIN +
    execute + clean output + empty-set affirmation on NOT-patterns).
  - `scripts/run_battery.py` — run `runs/retrieval-probe/structural_battery.json`, report
    retrieval-axis recall (the 1/6 → 100% proof; seeds GATE-9).
- **Probe / score (semantic path):** `scripts/retrieval_probe.py`
  - `--query "<nl>"` — strategy contributions, latency, plane-tagged results, grounding check (phantom detection);
  - `--top-k N`, `--mode auto|on|off`, `--seed <gid,…>`, `--types T1,T2`, `--expect "<sub>"` (FOUND/CONTEXT-ONLY/MISS), `--context`, `--raw`.
- **Validate:** `scripts/retrieval_golden_gate.py` — 10 gates (services, grounding [stale-index guard],
  hydration [stale-code guard], strategy plurality, heat, intent reachability, fence label, Path-B edges,
  **structural-recall via the router**, **router-safety** [agg/neg→Cypher, vague→anchored, empty-set note])
  + an informational AUDIT for known gaps.
- **Index refresh:** `POST /api/retrieval/build-indexes` (admin-key when set).
- **Design / decisions / findings:** `references/retrieval-probe-design.md`; session logs in `../runs/retrieval-probe/`.
