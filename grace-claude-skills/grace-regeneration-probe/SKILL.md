---
name: grace-regeneration-probe
description: >
  Prove a built GrACE knowledge graph can ANSWER in its own facts — the second half
  of the consume side. Retrieval (A1) finds the grounded subgraph; regeneration
  decompresses it into a natural-language answer. This skill drives the real
  regeneration prompt-assembly (the D193-frozen src/regeneration PromptAssembler,
  called read-only) over a grounded context, decompresses it with CLAUDE (in-loop,
  or AnthropicProvider direct), and SCORES the headline property — FAITHFULNESS:
  does every entity/number/identifier the answer asserts resolve in the context the
  model was actually given, or did it hallucinate beyond the subgraph? It is a
  CLIENT/PROBE: it never modifies regeneration internals (D193 hard-lock) and never
  routes a heat call through the live regeneration endpoint (which loads the
  configured llama3.3:70b). Use after retrieval is trusted (A1), to validate that
  the graph's answer stays inside the graph's facts.
---

# grace-regeneration-probe

## ★ NORTH STAR
**Prove the graph can ANSWER, faithfully.** A1 proved retrieval finds the right
grounded subgraph; A2 proves regeneration turns that subgraph into a human-readable
answer **stated in the graph's own facts**. The load-bearing test is **faithfulness**:
the decompressed answer must stay strictly within the retrieved subgraph — zero facts
invented, no number or name drifted. This skill exercises the real prompt-assembly
live, decompresses with Claude, and judges the answer against the context it was given.

## Role
You are the **decompression driver + faithfulness judge, outside the regeneration loop.**
You compose the exact prompt regeneration would feed its LLM (heat-free), supply Claude
as the decompression LLM, and score faithfulness / grounding / completeness against the
grounded context. You are **not** part of the regeneration computation — `src/regeneration/*`
is D193 hard-locked; you call its `PromptAssembler` read-only and never its synthesizer.

## When this runs
- **Trigger:** a graph is seeded and retrieval is trusted (A1 green), and you want to
  confirm the graph *answers* — or to regression-check after graph/index/code changes.
- **Composes with** `grace-retrieval-probe` (A1): A1's router produces the grounded
  subgraph regeneration consumes; this harness reuses A1's `cypher_exec` for both
  context production and Layer-2 relation verification.
- **Domain-agnostic:** anchors (an agreement name, an intent node, a real edge) are
  discovered from the graph at runtime. Nothing legal-hardcoded.

## The system under test (read, don't change — D193 HARD-LOCK)
`POST /api/regeneration/query` → `src/regeneration/regeneration_pipeline.py`:
1. **retrieve** — calls the retrieval pipeline → `serialized_context` (the grounded subgraph).
2. **assemble** — `PromptAssembler.assemble()` builds system + context + query under a token
   budget (`total_input_budget_tokens=3000`); only context is truncated (D134). A `phase_state`
   (`prepare|open|structure|clarify|close|none`) selects a style directive.
3. **synthesize** — `ResponseSynthesizer` → `get_provider().generate(...)`. **This is the heat
   trap** (see below).
4. **span_detect** — `ClaimSpanDetector` (sentence-fallback, deterministic): name-substring
   matches result names into sentences and assigns a certainty band from the rerank score.

**D193 lock:** `src/regeneration/*` is FROZEN (`bash scripts/check-regeneration-unchanged.sh`).
This harness is a **client** — it calls `PromptAssembler` read-only and evaluates the output.
**Log enhancement findings; do not implement them inside `src/regeneration/`.** Any real change
needs a new D-number + a D193 carve-out.

## The heat trap (sharp edge — read twice)
`ResponseSynthesizer.synthesize()` calls **`get_provider()`** → the configured provider in
`config/discovery.yaml` = **`ollama` / `llama3.3:70b-instruct-q8_0`** (~70 GB). So calling the
live **`POST /api/regeneration/query`** or **`python -m src.regeneration.cli` WITHOUT `--dry-run`**
tries to load a 70 GB model on an 18 GB box = **HEAT VIOLATION**.
> ⚠ **Do NOT trust `GET /api/regeneration/config`'s `regeneration_model` field** (it reads
> `qwen2.5:7b`, which *would* fit). That field is **vestigial** — `ResponseSynthesizer` calls
> `get_provider()` with no model override (D137: override accepted but not dispatched), so synthesis
> loads `config/discovery.yaml` `llm.model` = **llama3.3:70b**, regardless of what `/config` says.
> The config value is misleadingly reassuring; the heat comes from the discovery.yaml provider.

Heat-safe boundaries:
- **`--dry-run`** is LLM-free *and* retrieval-free — it assembles with an **empty** context, so it
  shows the prompt SHELL (system + phase directive + token math), **not the grounded subgraph**
  (finding **G-F1**). Use it only to inspect the prompt template.
- **`regen_compose.py`** (this harness) imports + *calls* the REAL `PromptAssembler` (read-only,
  D193-safe) over a context from the retrieval API (or a composed file) → the exact grounded
  prompt regeneration would feed its LLM. Pure string assembly — no `get_provider()`, heat 0.
- **The decompression LLM is CLAUDE**, never the local llama: `regen_decompress.py --answer-file`
  (Claude-in-the-loop, the default) or `--autonomous` (AnthropicProvider DIRECT — never
  `get_provider()`). Only `nomic-embed-text` + the CPU cross-encoder load (via retrieval). Confirm
  `ollama ps` shows no `llama`/`gpt-oss`/`qwen` before and after.

## The science (how to judge a decompressed answer)
1. **Faithfulness is relative to the PROVIDED context.** The same answer is faithful or
   hallucinated depending only on whether the context the model was given contains the facts.
   Score against `context ∪ query`, not against the graph-at-large or your own knowledge.
2. **Grounding before fluency (the A2 mirror of A1's "grounding before ranking").** Every
   salient token the answer asserts — Title-Case name, number/%/$, ALL-CAPS acronym, snake_case
   intent identifier — must resolve in the fact set. An ungrounded token is a candidate
   hallucination, exactly as a non-resolving `grace_id` is a phantom in A1.
3. **Abstention is faithful.** "The context does not identify… / insufficient / cannot determine"
   is the CORRECT behaviour when retrieval under-delivers — never score it as a hallucination.
4. **Faithfulness ≠ completeness.** Regeneration can be 100% faithful and 0% complete when
   retrieval misses the answer. Completeness is a RETRIEVAL property (A1); the scorer reports it
   separately (`--expect`) and never blames regeneration for it.
5. **Three layers.** Layer-1 (token grounding) catches invented entities/numbers/ids; it is BLIND
   to (a) a relational hallucination between grounded entities ("X is governed by Delaware" when
   both X and Delaware are in context but the edge isn't) and (b) an inverted-polarity claim (a
   REJECTED alternative asserted as a real term — both nodes grounded, only the polarity wrong).
   **Layer-2** verifies the EDGE in the graph via A1's `cypher_exec` (run on any relational claim).
   **Layer-3** (`regen_decompress.py --epistemic`) flags a rejected alternative presented as real.
   All three are heat-free.
6. **Heat 0.** Only `nomic-embed-text`. Never the live regeneration endpoint / `grace_answer` /
   `--dry-run`-less CLI while the provider is ollama.

## What this session proved (session 1, 2026-06-19)
- **Faithfulness is context-relative** — the identical answer ("four agreements … Armstrong/Anixa/
  Allison/Arconic") is **0% faithful on the weak semantic context, 100% on the A1 Cypher context**.
  Regeneration faithfulness is **bounded by retrieval quality**.
- **The scorer catches the dangerous case** — invented entities/numbers/principle-ids are flagged
  (0% faithfulness); abstention is credited (100%); relational hallucination is invisible to
  Layer-1 and **caught by Layer-2** (i-Escrow "Delaware" → graph says California → REFUTED).
- **Intent reaches as structure, not prose (G-F5)** — retrieval surfaces intent node names + edges
  (a faithful "why" can name the principles and the rejected alternative) but the template
  serializer **drops the captured reasoning prose** (`why_rejected`, principle `statement`); the
  rich dollar reasoning is faithful only when composed into the context. **Intent improves the
  answer; the improvement is capped at structure until the prose is serialized.**
- **Intent intrudes cross-deal into fact queries (G-F7)** — a parties question surfaced unrelated
  Adaptimmune intent; the faithful path is to abstain on it.
- Full findings + composition evidence: `references/regeneration-probe-design.md`,
  `../runs/regeneration-probe/session-1-process-log.md`.

## Session flow (step by step)
1. **services + heat** — `/api/regeneration/config` 200, ArcadeDB reachable, `ollama ps` shows no
   generation model. If the server is long-running, compare its start time to `pipeline.py` /
   `regeneration_pipeline.py` mtime (A1's stale-code lesson); rebuild the retrieval index after any
   graph write (`POST /api/retrieval/build-indexes`).
2. **author query sets** — 3–5 verifiable questions across the axes: a **fact/structural** one, a
   **"why"/intent** one, a **pure-fact** one (does intent intrude?), and a **vague** one.
3. **compose** — `regen_compose.py --query "…"` to see the exact grounded prompt (native context),
   and/or `--context-file` with A1's Cypher router output (composed context). Heat-free.
4. **decompress + score** — `regen_decompress.py --query "…" [--answer-file ans.txt | --autonomous]`
   → Claude writes the answer, the deterministic scorer reports faithfulness / grounding /
   completeness. **Read `overall_verdict`, not the headline `faithfulness` field alone** — the
   folded verdict includes Layer-2 (edge REFUTED) and Layer-3 (epistemic). Run Layer-2 on any
   relational claim: `--verify-claims "S|edge|O"` (reliable) or `--auto-layer2` (best-effort — it
   reports `auto_layer2_claims_extracted`; **0 extracted is NOT "all clean"**, fall back to
   explicit claims). Logs are quiet by default (WARNING); pass `--verbose` to restore INFO.
5. **stress (research-grounded)** — push the RGB four abilities (noise, negative rejection,
   information integration, counterfactual/graph-as-truth), lost-in-the-middle, truncation, and
   indirect prompt injection. Use `--expect` / `--anti-expect` / `--expect-abstain` as co-signals.
6. **multi-agent acceptance (optional)** — spawn fresh agents equipped with ONLY the harness,
   **log-only / heat-safe**, each a distinct deal × question-type slice, to test cold-start
   self-sufficiency and surface friction the author is blind to.
   - **Efficiency note (session-4):** the by-the-book swarm is ~6 (acceptance) + ~10 (stress) agents.
     Once the module verdict is stable (faithful — established sessions 1–3), a **lean 3-agent swarm
     surfaces the cold-start friction at ~1/5 the cost**; 16 agents re-confirming "faithful" is low
     marginal value. **Scale the swarm to what is UNPROVEN** — point agents at the harness's
     precision boundary and UX, not at re-proving faithfulness.
7. **log** — record friction + enhancement findings in `runs/regeneration-probe/`.
8. **gate** — `regeneration_golden_gate.py` — 16 invariants must pass on healthy current code
   (incl. GATE-14 sentence-initial false-positive guard, GATE-15 refuse-and-explain abstention guard,
   GATE-16 leading-stopword name guard); the AUDIT block reports remaining in-tree gaps (G-F1/F3/F7)
   and marks **G-F5 + SS-1/S7 CLOSED in-tree** (D532/D533, session-5).

## Hard constraints
- **D193:** client/probe only. Never edit `src/regeneration/*`. Call `PromptAssembler` read-only;
  never `ResponseSynthesizer`/`get_provider()`. Log enhancements; don't build them in-tree.
- **Heat:** only `nomic-embed-text`. Never the live regeneration endpoint, `grace_answer`, or the
  `--dry-run`-less CLI while the provider is ollama. Claude (cloud / in-loop) is the only LLM.
- **Faithfulness discipline:** score every answer against the exact context it was given
  (`context ∪ query`); credit abstention; run Layer-2 on relational claims; keep faithfulness and
  completeness separate.
- **Test-DB isolation:** the probe reads the **live** `grace` graph (that is the point). It is
  read-only against the graph and writes nothing. Never wipe or point a test at the GOLD corpus.
- **Domain-agnostic:** anchors discovered at runtime; nothing legal-hardcoded.

## Tools
- **Compose (heat-safe prompt assembly):** `scripts/regen_compose.py`
  - `--query "<nl>"` [`--phase-state …`] [`--top-k N`] → assembled system/context/query + tokens.
  - `--context-file F` / `--context-stdin` — feed a composed grounded subgraph (A1 router output).
  - `--json` — machine output (context, query, grounded_ids) for the scorer.
- **Decompress + score (end-to-end driver):** `scripts/regen_decompress.py`
  - `--query "<nl>"` alone → prints the grounded prompt for the in-loop LLM.
  - `--answer-file F` → score the answer Claude wrote (in-loop, the default mode).
  - `--autonomous` → AnthropicProvider DIRECT (heat-free cloud; degrades to print-prompt on a 401 key).
  - `--decompressor-model <name>` → heat-safe LOCAL decompression via a DIRECT OllamaProvider (size-guarded;
    BLOCKS the 70B/120B/configured model). For exercising production-class *small*-model behaviour.
  - `--context-file F` / `--compose-json J`, `--expect "t1,t2"` (completeness), `--json` (single clean object), `--verbose`.
  - `--expect-abstain` → negative-rejection gate: PASS only if the answer refuses (no positive assertion).
  - `--anti-expect "t1,t2"` → distractor-capture catch: FAIL if a forbidden token (e.g. a competing
    jurisdiction present in the context as a distractor) is captured into the answer.
  - `--epistemic` — **Layer-3** (graph-backed, heat-free): flags a REJECTED alternative
    (Counterfactual / `is_term=false`) asserted as a real term — the inverted-polarity hallucination
    Layer-1 is blind to. Catches an id-cited mislabel; a fenced mention ("…was rejected in favor of…") passes.
  - `--verify-claims "Subject|edge|Object; …"` / `--auto-layer2` — **Layer-2** (graph edge check): verify the
    answer's relational claims against ArcadeDB (CONFIRMED/REFUTED/UNRESOLVED). Endpoints resolve to the
    edge's correct src/dst label. Catches distractor capture + relational hallucination Layer-1 can't see.
    `--auto-layer2` recognizes the arrow form + governing-law + intent-attribution shapes
    (`justifies`/`explains`/`rejected_alternative_to`) and reports `auto_layer2_claims_extracted` so
    "0 extracted" never silently reads as "all clean" (session-4 R4).
  - `overall_verdict` (faithful | abstained | unfaithful) folds L1 + query-echo + anti-expect +
    Layer-2 REFUTED + Layer-3 — the single trustworthy field; logs quiet by default, `--verbose` restores.
- **Faithfulness scorer (deterministic, the deliverable):** `scripts/faithfulness_score.py`
  - `--answer-file A --compose-json C` (or `--context-file --query`) → `overall_verdict`, faithfulness,
    token grounding, hallucinated tokens, optional completeness (`--expect`). Layer-1: names/numbers/
    acronyms/snake_ids, injection-span exclusion, query-echo detection, sentence-initial positional
    demotion (session-4 R1), negation-aware abstention + anti-expect (R2). Flags: `--expect-abstain`, `--anti-expect`.
- **Layer-2 relation verify (composes A1):** `scripts/cypher_exec.py --cypher "<edge check>"` —
  confirm/refute a relational claim against the graph (heat-free, grounded by construction).
- **Validate:** `scripts/regeneration_golden_gate.py` — 16 gates (services, dry-run heat-free+empty,
  assembly fidelity, entity-hallucination catch, abstention credit, context-relative faithfulness,
  intent plane reachable, Layer-2 relation verify, Layer-3 epistemic mislabel catch, counterfactual
  context-fidelity, injection self-grounding caught, query-echo + expect-abstain, GATE-14
  sentence-initial false-positive guard, GATE-15 refuse-and-explain abstention guard (session-4 R3),
  **GATE-16 leading-stopword name guard** (session-5), heat 0) + AUDIT (marks G-F5 + SS-1/S7 CLOSED
  in-tree, D532/D533).
- **Design / decisions / findings:** `references/regeneration-probe-design.md`; session logs in
  `../runs/regeneration-probe/`.
