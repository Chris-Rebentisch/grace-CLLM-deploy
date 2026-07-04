# Intent layer — design + decision record

## ★ NORTH STAR
**We are extracting human intent and rationale and connecting it to data in a queryable knowledge graph** — so that future tasks (up to and including agentic, human-inspired resolutions) can ask the graph not just *what* is true but *how and why* a decision should be made.

The KG industry captures the **what** (entities + relationships extracted from documents). It cannot capture the **why** — intent, tradeoffs, constraints, stakes, the rejected path, the unwritten understanding. That tacit decision-layer lives only in a human's head and no extraction pipeline can produce it. This skill + harness is the missing link: it elicits that reasoning from a human and binds it to the facts it explains, as durable, queryable, agent-consumable graph structure.

Everything below serves that one sentence.

---

## 1. What this is (and is not)
- **Is:** a human-in-the-loop elicitation harness that captures the reasoning *behind* facts already in the graph, structures it, and writes it as a new graph layer linked to those facts.
- **Is not:** fact validation. `grace-review-protocol` catches the extractor's mistakes (is this fact right?). This skill captures the why (why was it decided this way?). They are siblings over the same seeded graph; this one runs after / alongside the tranche review.
- **Domain-agnostic:** nothing legal-specific. The same 3 types + 5 edges serve finance, projects, any domain. Authored once, reused everywhere.

## 2. The model (validated over 6 real decisions, 2 validation runs)
Three vertex types + five edge types. **Keep `Decision_Principle` and `Decision_Rationale` separate** — the split is the mechanism: the Principle is the reusable abstraction that transfers across documents/domains; the Rationale is the grounded instance.

| Vertex | Role | Key properties |
|---|---|---|
| `Decision_Principle` | Reusable, domain-agnostic mental model ("control follows value") | `name` (dedup key), `statement`, `applies_when` (scope/trigger), `certainty_band`, `domain_agnostic`, `_embedding` |
| `Decision_Rationale` | The per-decision instance binding a clause to its principle(s) | `name`, `summary`, `constraint`, `stakes`, `leverage`, `negotiation`, `certainty_band`, `resolver` |
| `Counterfactual` | The rejected alternative — **fenced** | `name`, `description`, `demanded`, `why_rejected`, `is_term=false`, `epistemic_status='rejected_alternative'` |

| Edge | Direction | Meaning |
|---|---|---|
| `explains` | Principle → fact | fast agent-retrieval path; where cross-document reuse shows |
| `justifies` | Rationale → fact | the rationale grounds this clause |
| `applies_principle` | Rationale → Principle | this instance applies this abstract rule |
| `rejected_alternative_to` | Counterfactual → fact | the road not taken, fenced |
| `traded_for` | fact ↔ fact | quid-pro-quo: two REAL facts linked as an exchange ("the pair is the real story") |

## 3. Decisions captured this session (the build contract)

**Architecture (approved by operator):**
- **D-int-1 Model shape.** 3 vertex types + 5 edge types as above. `Negotiation_Event` deferred — promote from a `negotiation` property only once agents query "contested vs settled."
- **D-int-2 Principle ≠ Rationale.** Never collapse them; the abstract↔instance split is what enables generalization.
- **D-int-3 Epistemic fence.** Every intent node carries `epistemic_status ∈ {asserted_fact, human_rationale, rejected_alternative}`. Counterfactuals additionally `is_term=false`. Retrieval must never serve a `rejected_alternative` as a real term. v1 fence is **structural** (counterfactuals are a separate type reached only via `rejected_alternative_to`; fact-retrieval never traverses to them — golden gate proved 0 leaks). Defense-in-depth post-fetch filter (D521 pattern) is a documented follow-on.
- **D-int-4 Ratify, don't propertyize.** The reusable Principle must be a node, so the layer is a hash-chained ontology version (v#9), not properties. Operator sign-off in-thread IS the Earned-Autonomy human review for the new types.
- **D-int-5 Embeddings = `statement + applies_when`.** Embedding the statement alone gave mediocre retrieval (correct principle ranked #3 → #1 once `applies_when` was added). `Decision_Principle._embedding` + `LSMVectorIndex` for server-side `vectorNeighbors()`.
- **D-int-6 Principle canonicalization.** Before creating a principle, semantic-dedup against existing ones (embedding similarity) — else the transferable asset fragments. Exact-name dedup is the floor (D106); semantic dedup is the harness layer.
- **D-int-7 Write topology.** The elicitation agent owns *elicit + structure + the human-confirm gate*, then calls a **deterministic write tool** (`intent_writer`). NO second reasoning agent. The write has zero latitude (stamps, fence, dedup are guaranteed, not improvised). Human confirmation of the structure > a second LLM reviewing it.
- **D-int-8 Provenance, not decay.** Intent nodes stamp `decision_source='human'`, `reviewed_by`, `review_session_id`, `captured_at`, `elicitation_method`, `intent_origin`, `epistemic_status`, `certainty_band`. They do **NOT** carry D452 fact-decay verdicts — captured intent is a fixed historical record, not a degrading fact.
- **D-int-9 `evidence_origin += 'human_intent'`.** Closed enum (`document|communication|hybrid`) needs the new value; until added, origin rides as an `intent_origin` property.
- **D-int-10 Edge-dedup guard mandatory.** `insert_relationship` does not dedup edges; `intent_writer` checks edge existence before insert (mirrors `graph_review_writer._existing_edge_grace_id`). Proven idempotent (re-run wrote 0 dup edges).
- **D-int-11 `traded_for` for quid-pro-quo.** Distinct from a counterfactual; carries `certainty_band` + `documented` (inferred vs from-the-redline). Records that two real facts are a balanced exchange.
- **D-int-12 Certainty bands, never numbers** (D120/D217). `high|medium|low|insufficient_evidence`, plus `resolver` (what would raise it) and optional rival hypothesis. Bands move both ways — laddering resolved Q3 up; Q6 honestly stayed `medium` with an explicit resolver.
- **D-int-13 Code placement.** Domain-agnostic in `src/`: `src/ontology/intent_models.py` (Pydantic source of truth), `src/extraction/intent_writer.py` (sibling to `graph_review_writer`, R1 boundary — never imports `graph_writer`). First-class GrACE infrastructure.
- **D-int-14 Top-K extraction, never blind top-1.** A semantic attractor (`smaller_party_caps_exposure_early`) over-ranks on small-party scenarios; the correct principle was reliably present but often #2. The agent retrieves top-K and selects using `applies_when`, never trusts rank #1.
- **D-int-15 Hash chain remediated first.** Sweep finding #12 (placeholder at v2) fixed before v#9 — chain valid end-to-end, content untouched, backup saved.

**Science (validated live, 6 questions + ladders):**
- **D-int-S1 Anti-anchoring is load-bearing** (D226 / Buçinca 2021). The facilitator NEVER proposes the why — evidence-first, open ask, give the human nothing to mirror. Teach-Back's *principle* carried into intent: the human's "correction" becomes the rationale they supply.
- **D-int-S2 Surgical laddering.** For an expert human, probe the ONE implicit rung they left, not "why ×5". Experts self-ladder to root; find the gap.
- **D-int-S3 Plain language** (D522); **certainty bands not numbers** (D120/D217); **five-phase lifecycle** reused (`prepare→open→structure→clarify→close`), adapted to graph-intent sessions.

## 4. Validation evidence (why we trust this)
- **Round 1** — Xencor/Aimmune (risk allocation), Apollo (Ex Works), Ashworth (board seat). 4 proofs green (why-query, cross-document reuse, fence, novel-transfer).
- **Round 2** — Allison/ValueAct (standstill), Watson (clawback), Accuray (governing-law trade). Surfaced `traded_for`; reused `structural_substitute_for_unobtainable_restriction` cross-domain (golf → activist defense).
- **Golden gate 8/8 PASS** across both batches (`scripts/intent_golden_gate.py`): provenance, fence (0 leaks), fact-retrieval isolation, cross-contract reuse (×2 principles), embeddings, no orphan rationales, `traded_for` integrity, novel-task transfer.
- **Idempotency** — re-run wrote 0 duplicate vertices/edges.
- **Cold-scenario extraction** — an automotive-battery deal (never seen) resolved by intent captured from pharma/golf/transmissions deals. Extraction confirmed; top-K rule earned (D-int-14).

## 5. Registry pointers (proposed)
Maps to grace D-series at next-free pointer (D531+; D530 locked, D522–D529 reserved). The 15 architecture decisions + 3 science decisions above are the source; a consolidated `D531 — Intent layer (Decision_Principle/Rationale/Counterfactual meta-layer, epistemic fence, intent_writer, v#9)` should be entered in `GrACE-Decisions.md` at chunk close.

## 6. Build status (2026-06-18 — harness built)
| Piece | File | Status |
|---|---|---|
| Pydantic source of truth | `src/ontology/intent_models.py` | ✅ done |
| v#9 ratification (hash-chained) | `ontology_versions` v9, active | ✅ done — chain valid, 14 entity / 24 rel types |
| `LSMVectorIndex` on `Decision_Principle._embedding` | ArcadeDB | ✅ done |
| `evidence_origin='human_intent'` enum | `src/graph/entity_models.py` | ✅ done |
| `intent_writer` (deterministic write tool) | `src/extraction/intent_writer.py` | ✅ done — 7 unit tests + live smoke |
| Skill | `grace-intent-elicitation/SKILL.md` + 2 references | ✅ done |
| Harness write tool | `scripts/intent_apply.py` | ✅ done — idempotent through `intent_writer` |
| Harness extract tool | `scripts/intent_query.py` (`--facts/--similar/--ask`) | ✅ done |
| Golden gate | `scripts/intent_golden_gate.py` | ✅ 8/8 PASS |
| Hash-chain remediation (finding #12) | `ontology_versions` v2–v8 | ✅ done — backup saved |

**Repo files changed (commit when ready — not yet committed):** `src/ontology/intent_models.py` (new),
`src/extraction/intent_writer.py` (new), `tests/extraction/test_intent_writer.py` (new),
`src/graph/entity_models.py` (additive enum). DB changes (v#9, vector index, chain remediation) are
already applied to the live `grace` graph + Postgres.

### v#10 (2026-06-19) — session-2 enhancements (P1–P3, IMPLEMENTED)
The first live elicitation session (Adaptimmune/MD Anderson) surfaced three model gaps; all shipped as
ontology v#10 (additive; chain valid, 10 versions):
- **D-int-16 / P1 `Mandatory_Provision`** — some facts have NO discretionary intent (statute boilerplate, e.g.
  Anti-Kickback safe harbor). New vertex (`source_of_compulsion`, `degree_of_freedom`, `basis`;
  `epistemic_status='compelled'`) + `compels` edge. The skill `structure` step routes first: recognize
  compelled-by-statute, capture *why it must exist in any such contract*, never fabricate a chosen "why."
- **D-int-17 / P2 `depends_on` edge** — load-bearing dependency between two real facts ("A only works if B is
  robust"); distinct from `traded_for` (exchange) and `Counterfactual` (rejected). E.g. JSC-parity depends_on
  the escalation clause.
- **D-int-18 / P3 rationale salience** — `Decision_Rationale.controlling_reason` / `subordinate_reasons`: a
  rationale can carry a load-bearing reason plus subordinate ones ("if you record one, record the controlling
  reason").
- Code: `intent_models.py` (+`MandatoryProvision`, +2 edges, +salience, +`compelled` status, +compulsion
  vocab), `intent_writer.py` (+`write_mandatory_provision`), `scripts/intent_apply.py` (+`mandatory_provisions`/
  `depends_on` bundle sections), `SKILL.md`. Tests 7→10; golden gate 8/8.

### v#11 + CLI (2026-06-19) — P4, P5 (IMPLEMENTED)
- **D-int-19 / P4 `specializes` edge** (principle→principle hierarchy), ratified as ontology v#11 (chain valid,
  11 versions). `intent_query.py --similar` now **tiers**: ≥0.93 duplicate / 0.80–0.93 strong-overlap /
  0.62–0.80 related→consider-specializes (weak parent signal at that range — the human decides). An agent can
  fall back from a specific principle to its general parent.
- **D-int-20 / P5 CLI polish** — `intent_query.py --facts --full` + `--fact <gid>` (verbatim + neighborhood +
  captured intent, for elicitation); `intent_apply.py` session-bundle (`{"decisions": [...]}`).
- All session-2 findings (P1–P5) now resolved. F3 (band mapping) is facilitator discipline; F8 (modest
  cross-domain scores) handled by the top-K + `applies_when` rule.

## 7. Deferred / follow-on (do not lose)
1. `Negotiation_Event` promotion (from the `negotiation` property) when agents query contested-vs-settled.
2. Retrieval-side epistemic post-fetch filter (D521 pattern) — defense-in-depth beyond the structural
   fence (which already proves 0 leaks); needs a CF3 allowlist extension + D-number.
3. Round-1 principle embedding backfill to `statement + applies_when` (round-2 + new writes already use it;
   the 6 round-1 principles still carry statement-only embeddings — re-embed for consistency).
4. Server-side `vectorNeighbors()` retrieval over `Decision_Principle` (index created; the harness uses
   client-side cosine which works — server-side is the scale optimization).
5. Enter the consolidated `D531 — intent layer` into `GrACE-Decisions.md` at chunk close.
