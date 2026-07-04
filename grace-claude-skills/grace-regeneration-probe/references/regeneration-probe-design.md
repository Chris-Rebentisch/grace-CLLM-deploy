# Regeneration probe — design + decision record

## ★ NORTH STAR
**Prove the built GrACE graph can ANSWER in its own facts.** A1 (retrieval) proved the
graph surfaces the right grounded subgraph; A2 (regeneration) proves that subgraph
becomes a human-readable answer that **stays inside the subgraph** — the headline
property is **FAITHFULNESS**. This harness drives the real prompt-assembly, decompresses
with Claude, and judges the answer against the context it was given. It is a **client** —
`src/regeneration/*` is D193 hard-locked; the harness calls `PromptAssembler` read-only and
never the synthesizer. Everything below serves that one sentence.

---

## 1. What this is (and is not)
- **Is:** a client/probe + golden gate that (a) assembles the exact grounded prompt
  regeneration would feed its LLM (heat-free), (b) decompresses it with Claude (in-loop or
  AnthropicProvider direct), and (c) scores faithfulness / token-grounding / completeness
  against the provided context, with a Layer-2 graph edge check for relational claims.
- **Is not:** a regeneration implementation, and not a unit test. It reaches the real
  `PromptAssembler` read-only and the live `grace` graph. `src/regeneration/*` is FROZEN
  (D193); this harness reads and judges, it does not touch it.
- **Composes with A1:** the retrieval router (`cypher_exec.py`) both *produces* the grounded
  subgraph regeneration consumes and *verifies* relational claims (Layer-2 faithfulness).

## 2. The system under test
`src/regeneration/regeneration_pipeline.py` — retrieve → assemble → synthesize → span_detect.
`PromptAssembler` (deterministic, D134: only context truncated; budget 3000 tok) + phase-state
style directives (`prepare|open|structure|clarify|close|none`) + `ResponseSynthesizer`
(`get_provider()` — the heat trap) + `ClaimSpanDetector` (sentence-fallback name-substring,
deterministic). The configured provider is `ollama` / **`llama3.3:70b-instruct-q8_0`**.

## 3. Decisions captured this session (the harness contract)

- **D-reg-1 Client/probe topology (D193).** The harness consumes regeneration: it imports + calls
  `PromptAssembler`/`RegenSettings`/`RetrievalResponse` (read-only) and POSTs to the retrieval API.
  It NEVER imports/edits `src/regeneration/*` and NEVER calls `ResponseSynthesizer`/`get_provider()`.
  Enhancement findings are logged with a proposed direction; never implemented in-tree.
- **D-reg-2 Heat is the sharp edge.** The live `/api/regeneration/query` and the `--dry-run`-less
  CLI both call `get_provider()` → the configured llama3.3:70b (~70 GB) = HEAT on an 18 GB box.
  Heat-safe entry is ONLY: `--dry-run` (LLM-free, but empty context — G-F1), `regen_compose.py`
  (real PromptAssembler over a real context, pure string), and Claude as the decompression LLM
  (in-loop / AnthropicProvider direct — never `get_provider()`). nomic-embed-text + CPU
  cross-encoder are the only local loads. Gated by golden-gate GATE-2 / GATE-9.
- **D-reg-3 Faithfulness is relative to the PROVIDED context.** The scorer's fact set is
  `context ∪ query` — the exact text the model saw — not the graph-at-large and not the judge's
  knowledge. The same answer is faithful against one context and hallucinated against another;
  proven live (Delaware answer: 0% native / 100% composed). This is the core A2 invariant.
- **D-reg-4 Grounding before fluency (the A1 mirror).** Every salient token the answer asserts
  (Title-Case name, number/%/$, ALL-CAPS acronym, snake_case intent id) must resolve in the fact
  set. An ungrounded token is a candidate hallucination, as a non-resolving `grace_id` is a phantom
  in A1. Deterministic, domain-agnostic, no LLM.
- **D-reg-5 Abstention is faithful.** A sentence that hedges ("context does not… / insufficient /
  cannot determine") is the CORRECT behaviour under weak retrieval and is scored faithful, never as
  a hallucination. The scorer tags ABSTAIN explicitly.
- **D-reg-6 Faithfulness ≠ completeness.** Regeneration can be 100% faithful and 0% complete when
  retrieval misses. Completeness is a RETRIEVAL property; the scorer reports it separately
  (`--expect`) and never charges regeneration for a retrieval gap.
- **D-reg-7 Three-layer faithfulness.** Layer-1 (token grounding) catches invented
  entities/numbers/ids; it is BLIND to (a) relational hallucination between grounded entities and
  (b) inverted-polarity claims (a rejected alternative asserted as real). Layer-2 (graph EDGE check
  via A1 `cypher_exec`) catches (a); **Layer-3** (`epistemic_violations()` + `regen_decompress.py
  --epistemic`, added session 2 / H-2) catches (b). All three heat-free; GATE-8 + GATE-9.
- **D-reg-8 Claude generates, the scorer judges deterministically.** Mirrors the A1/intent write
  topology: the LLM (Claude, in-loop or AnthropicProvider) does the decompression; a pure-Python,
  no-LLM scorer owns the verdict. The autonomous path uses `AnthropicProvider` DIRECT (heat-free
  cloud); the stored `LLM_API_KEY` currently **401s**, so the in-loop `--answer-file` path is the
  operating mode — faithful to the harness pattern and the proof path this session.
- **D-reg-9 Golden gate = regression guard, AUDIT = known gaps.** Gates encode invariants that MUST
  hold on healthy current code (a stale server, heat breach, assembly regression, or broken scorer
  fails a gate). Documented gaps accepted pending a D-number (G-F1/2/3/5) are reported in the AUDIT
  block, not failed, so the gate stays a true signal.
- **D-reg-10 Domain-agnostic anchors.** The probe and gate derive inputs from the graph at runtime
  (sample an Agreement name, an intent node, a real `governed_by` edge). No domain literals.

## 4. Findings (the substance — full detail + evidence in the session log)
| ID | Sev | Finding | Proposed direction |
|---|---|---|---|
| **G-F1** | MED (harness) | `--dry-run` assembles with an **empty** context — the prompt shell, not the grounded subgraph. Misleads anyone treating it as "what regeneration sends". | Harness fills it (`regen_compose.py`). Possible in-tree: dry-run runs retrieval-only + assemble (D193 carve-out). LOG. |
| **G-F2 / G-F8** | HIGH | **Relational hallucination is invisible to Layer-1** — "X governed by Delaware" passes when X + Delaware are both in context but the edge isn't. | Layer-2 edge check via `cypher_exec` (shipped, GATE-8). |
| **G-F3 (= A1 R-F5)** | MED | The native serialized_context regeneration consumes leaks `confidence_at_verification=0.9` (D120/D217) + heavy provenance bloat. One paraphrase from surfacing in an answer. | denylist→allowlist in CF3-exempt `pipeline.py`, behind a D-number (same as A1 R-F5). LOG. |
| **G-F4** | — (headline) | FAITHFULNESS and COMPLETENESS are **separable**; regeneration can be faithful + incomplete when retrieval under-delivers. | Harness + gate invariant (D-reg-6). |
| **G-F5** | HIGH (intent) | The template serializer ships intent NODE NAMES + EDGES but **drops the reasoning PROSE** (`why_rejected`, principle `statement`/`applies_when`). The captured human reasoning — the point of the intent layer — does not reach the LLM natively; a faithful "why" is capped at structure. | Serialize intent prose for INTENT-type nodes (CF3-exempt `pipeline.py`, D-number). Harness mitigation: compose the prose via Cypher. LOG. |
| **G-F6** | MED (scorer) | snake_case intent ids weren't caught by the Title-Case detector; an invented principle name would slip Layer-1. **Fixed in-harness** (`_SNAKE_RE`). Edge case: a name whose only distinctive token is <4 chars can false-positive (rare). | Fixed in `faithfulness_score.py`. |
| **G-F7** | HIGH | Intent plane **intrudes uninvited and cross-deal** into pure-fact queries (Adaptimmune intent on an Aura/Zanotti parties question) = A1 R-F8 (no query-plane routing). Faithful path: abstain on the irrelevant intent. | Query-plane routing (architect, A1 R-F8). Harness: route fact queries to Cypher/anchored context. |

## 5. Validation evidence (why we trust the harness)
- **Context-relative faithfulness reproduced both directions** — Delaware answer 0%→100% native→
  composed; rich "why" answer 50%→100% native→composed. The SAME text, two contexts.
- **Scorer catches the dangerous class** — entity hallucination 0% (7 invented names flagged);
  invented principle ids flagged; abstention 100%; relational hallucination invisible to Layer-1
  and **caught by Layer-2** (i-Escrow → graph California → REFUTED).
- **Intent reach measured** — native "why" context contains the counterfactual + rationale +
  principle edges (structure reaches) but not the `why_rejected`/`statement` prose; composing the
  prose makes the dollar reasoning faithful.
- **Heat 0** throughout (`ollama ps` only ever `nomic-embed-text`); autonomous key 401 confirmed →
  in-loop default; `--dry-run` confirmed LLM-free + empty-context.
- **Golden gate 9/9 PASS** on current code; AUDIT correctly flags G-F1/2/3/5.

## 6. Build status (2026-06-19 — harness built + acceptance-hardened)
| Piece | File | Status |
|---|---|---|
| Skill | `grace-regeneration-probe/SKILL.md` | ✅ done (+ H-4 heat-config warning, Layer-3 docs) |
| Design / decision record | `references/regeneration-probe-design.md` | ✅ done (this file) |
| Compose client (retrieval → real PromptAssembler, heat-free) | `scripts/regen_compose.py` | ✅ done |
| Faithfulness scorer (L1: name/number/acronym/snake_id + abstention; L3: `epistemic_violations`) | `scripts/faithfulness_score.py` | ✅ done (+ H-1 false-positive fix, H-2 Layer-3 helper) |
| Decompress driver (Claude in-loop / AnthropicProvider; `--epistemic` Layer-3) | `scripts/regen_decompress.py` | ✅ done |
| Layer-2 relation verify (composes A1) | `scripts/cypher_exec.py` (reused) | ✅ done |
| Golden gate | `scripts/regeneration_golden_gate.py` | ✅ **16/16 PASS** + AUDIT (GATE-9 epistemic, GATE-10 counterfactual, GATE-11 injection, GATE-12 query-echo, GATE-14 sentence-initial FP, GATE-15 refuse-and-explain abstention, **GATE-16 leading-stopword name FP**); AUDIT marks G-F5 + SS-1/S7 CLOSED in-tree |
| Session-1 process log | `runs/regeneration-probe/session-1-process-log.md` | ✅ done — 8 findings + 4 question types |
| Session-2 acceptance log (6 parallel agents) | `runs/regeneration-probe/session-2-harness-acceptance.md` | ✅ done — self-suff 6/6, H-1/H-2 fixed, H-3/H-4 |
| Session-3 stress test (10 agents, RGB-grounded) + F1–F6 fixes | `runs/regeneration-probe/session-3-stress-test.md` | ✅ done — 7 findings; **all 6 fixes implemented + validated** |
| Session-4 playbook re-run + process audit (lean 3-agent swarm) + R1–R6 fixes | `runs/regeneration-probe/session-4-playbook-rerun.md` | ✅ done — 7 findings; **R1–R6 implemented + validated**, gate 13→15 |
| Session-5 in-tree carve-outs (G-F5 + clause + budget bump) | `runs/regeneration-probe/session-5-in-tree-carveouts.md` | ✅ done — **D532 (CF3) + D533 (first D193 carve-out)**; gate 15→16; A1 10/10, A2 16/16; pending Decisions ratification |

### Session-4 fixes (operator-approved, all harness-side / D193-safe — severity order)
- **R4 (HIGH)** `--auto-layer2` claim extractor now recognizes the arrow form + intent-attribution
  shapes (`justifies`/`explains`/`rejected_alternative_to`), not just governing-law; emits
  `auto_layer2_claims_extracted` so "0 extracted" is no longer indistinguishable from "all clean".
  (Was: a fully cross-wired answer extracted 0 claims → scored faithful 1.0 end-to-end.)
- **R1 (HIGH)** sentence-initial positional-capitalization demotion in `faithfulness_score.py`: a
  single-word, sentence-initial, non-CamelCase, digit-free Title-Case "name" whose lowercased form is
  a QUERY word (absent from context) is the user's own word, not an asserted entity → not flagged.
  (Was: "Delivery" at a sentence start dropped a faithful+complete answer to 0.5.)
- **R2 (MED-HIGH)** negation-aware abstention + anti-expect: a "refuse + explain" answer that names the
  distractors it dismisses is now credited as abstaining, and a token inside a negated/dismissive
  clause no longer trips `--anti-expect`. (Was: only a *terse* zero-token refusal was credited.)
- **R5 (MED)** folded `overall_verdict` (faithful | abstained | unfaithful) over L1 + query-echo +
  anti-expect + Layer-2 REFUTED + Layer-3 — the single trustworthy field; `injection_markers_*`
  documented as a per-CONTEXT hazard flag (not a per-answer discriminator).
- **R6 (MED)** `route_logs_to_stderr(quiet=…)` raises the log floor to WARNING by default (the ~40
  per-call `arcade.query` INFO lines + Pydantic UserWarning — the unanimous cold-start friction);
  `--verbose`/`-v` restores INFO on every probe CLI.
- **R3 (LOW-MED)** golden gate gains GATE-14 (sentence-initial false-positive guard) + GATE-15
  (refuse-and-explain abstention guard) so the R1/R2 precision class can't silently regress → **15/15**.

**Process-efficiency finding (session-4):** the by-the-book swarm is ~6 + ~10 agents; once the module
verdict is stable, a **lean 3-agent swarm finds the cold-start friction at ~1/5 the cost**. Scale the
swarm to what is UNPROVEN (harness precision + UX), not to re-proving faithfulness. (Mirrored in SKILL
§Session flow step 6.)

### Session-3 fixes (operator-approved, all harness-side / D193-safe)
- **F1** injection-span exclusion + query-echo detection + `--expect-abstain` (closes both SS-1 false-pass classes: injection self-grounding, query-echo).
- **F2** Layer-2 graph edge verify (`--verify-claims` / `--auto-layer2`, label-aware endpoint resolution) + `--anti-expect` (distractor capture). The scorer is now **3-layer with an explicit relational check**.
- **F3** comprehensive sentence-opener stoplist (SS-2 false-positives).
- **F4** `regen_decompress --json` emits one clean object.
- **F5** `--decompressor-model` heat-safe local decompression (size-guarded; never the 70B).
- **F6** fact-level `coverage` + `--assert-truncation` in `regen_compose`; counterfactual/injection/query-echo regression gates (GATE-10/11/12).

**No repo `src/` changes** (D193) — the harness is pure client; all session-2 fixes (H-1 scorer
false-positives, H-2 Layer-3 epistemic check, H-4 doc) are harness-side. No operational action against
the running system; heat 0 throughout both sessions.

### Session-2 acceptance findings (6-agent swarm — full detail in the session-2 log)
- **H-1 (FIXED)** scorer false-positives on sentence-initial words / list ordinals → under-reported faithful answers; fixed with list-marker stripping + expanded stoplist; ungrounded-entity catch preserved.
- **H-2 (FIXED)** epistemic-polarity blind spot — a rejected alternative asserted as real scored 100%; added Layer-3 (`--epistemic`) + GATE-9. The mislabel that fooled Layer-1 is now caught.
- **H-3 (logged)** `--expect` completeness is coarse (token-present-for-wrong-reason / prose-not-id).
- **H-4 (FIXED, doc)** `/config`'s `regeneration_model: qwen2.5:7b` is vestigial (D137); synthesis loads discovery.yaml `llama3.3:70b` — heat warning added to the SKILL.
- **G-F5 / G-F7 re-confirmed** independently across 4 deals; remain in-tree D193 candidates.

## 7. In-tree carve-outs (operator-directed 2026-06-22, session-5)
Full record: `runs/regeneration-probe/session-5-in-tree-carveouts.md`. **Proposed D532 / D533 — pending
ratification in `docs/GrACE-Decisions.md`.** Heat 0; D193 + CF3 guards both pass; A1 gate 10/10, A2 gate 16/16.
- **G-F5 intent-prose serialization — ✅ DONE (D532, CF3).** Root cause was hydration, not the
  serializer: `pipeline.py::_hydrate_result_identities` re-fetched only name/labels, so intent nodes arrived
  with empty `properties`. Now it merges the reasoning-prose subset (`_INTENT_PROSE_KEYS`, free-text only,
  D120/D217 preserved) into intent-type results. Verified live: a native "why" answer is now faithful 1.0 /
  complete 1.0. `pipeline.py` was already CF3-allowlisted (D467); new purpose under D532 + capture-the-why.
- **SS-1/S7 data-vs-instruction clause — ✅ DONE (D533, FIRST D193 carve-out).** The
  `system_prompt_template` default in `regeneration_config.py` now treats context as untrusted DATA, never
  instructions. `scripts/check-regeneration-unchanged.sh` gained an exact-filename allowlist (mirrors CF3)
  for that one file. The scorer-side F1/GATE-11 catch backstops.
- **Relevance-aware packing — config budget bump instead (Claude path).** `REGENERATION_TOTAL_INPUT_BUDGET_TOKENS=32000`
  in `.env` (no source edit). The serializer rank-interleave restructure is **deferred** to a future
  decision (would need `serializer.py` on the CF3 allowlist + a new D-number; high blast radius; only the
  airgap/small-context path needs it — native results are already rank-ordered).

### Still-open in-tree candidates (logged, not built)
- **G-F3 (= A1 R-F5) serializer hygiene** — denylist→allowlist to drop `confidence_at_verification`
  (D120/D217) + provenance bloat from the context. (AUDIT still reports the numeric leak.)
- **G-F1 dry-run grounded preview** — make `--dry-run` run retrieval-only + assemble (currently empty).
- **G-F7 query-plane routing** (= A1 R-F8) — should fact and intent planes be separated at query time so
  intent doesn't intrude into fact answers?

## 8. Deferred / follow-on (do not lose)
1. **Vague-query decompression** — feed A1's `--anchor` deal-summary context to `regen_compose` and
   score the profile answer (pattern shown; a dedicated run not yet logged).
2. **Autonomous path end-to-end** — once a valid `LLM_API_KEY` is wired, run `--autonomous` across
   the battery and confirm parity with the in-loop scores.
3. **Layer-2 automation** — session-4 R4 broadened `--auto-layer2` to the arrow + intent-attribution
   shapes (catches cross-wiring), but it is still regex best-effort. The durable answer is an in-loop
   S-R-O triple extractor that emits the verification Cypher for *every* relational claim (today
   `--verify-claims` is the reliable path; `--auto-layer2` is best-effort with a 0-extracted warning).
4. **Phase-state battery** — score the same question across all six `phase_state` directives
   (close-phase narrative vs structure-phase terse) for faithfulness drift.
5. **Re-run the gate** after any in-tree enhancement (G-F5/F3/F1/F7) lands.
