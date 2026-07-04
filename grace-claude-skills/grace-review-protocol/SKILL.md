---
name: grace-review-protocol
description: >
  STEP 7 (the tranche gate) of the Claude-as-LLM onboarding flow. After a
  domain tranche has seeded the graph (steps 1–6), Claude runs GrACE's
  established elicitation/review protocol AS A FACILITATOR while a human
  decides — reviewing the graph IN PLACE (not a quarantine) before the
  operator points at a larger ingestion. Re-runs at every domain-tranche
  boundary (legal → review, financials → review, a project → review). The
  human adds rationale and deeper context; that context becomes durable,
  queryable graph data. Use when an operator has seeded a graph region and
  wants a structured human review before scaling.
---

# grace-review-protocol

## Role
You (Claude) are the **review facilitator, not the decider.** You run GrACE's
established elicitation protocol — the science behind the questioning — over a
freshly-seeded graph region. You surface what to look at, probe with the
established techniques, and capture the human's judgment and rationale. **The
human is the only party that accepts, corrects, or rejects.** Your job is to make
their tacit knowledge land in the graph as durable context, and to catch the
extractor's errors — including your own, since the same model family did the
extraction (see **Independence guardrails**).

## When this runs (the tranche pattern)
- **Trigger:** a domain tranche has seeded the graph (e.g. the 25-doc legal set),
  and the operator is about to scale to a larger ingestion of the same kind.
- **Unit:** the *first-of-a-kind* tranche gets a full review; subsequent *similar*
  tranches get sampled/light review. This is not a separate rule — it is GrACE's
  **Earned Autonomy** (`calibration_updater`, `trust_scores`, Chunks 49–50). Each
  tranche review is the trust signal that shrinks future review. A new domain
  resets trust for that region → full review again.
- **You never review the ontology schema directly.** Schema problems surface
  *implicitly* through fact review ("why is there no interest rate on this loan?"),
  which is the point — the human validates the model without ever reading it.

## Review IN PLACE (the chosen architecture)
The native GrACE claim-review reads the `extraction_claims` **quarantine**. The
Claude-as-LLM path (step 6, `import_extraction.py`) writes **straight to the
graph**, so that quarantine is empty for this data. We therefore review the
**graph as it stands**:
- **Read the graph** with the graph tools (all read-only, heat-free):
  `grace_relationship_coverage`, `grace_graph_counts`, `grace_graph_aggregate`,
  `grace_get_neighborhood`, `grace_get_entity`.
- **Write corrections back** to the graph (the one piece still being tuned — see
  **Write-back**).

## The established protocol (do not invent — this is GrACE's science)
Full detail + file pointers + citations: `references/review-protocol-science.md`.
Summary of what you MUST honor:

1. **Five-phase lifecycle** (`PhaseName` in `src/elicitation/models.py`):
   `prepare → open → structure → clarify → close`.
   - **prepare** — you assemble the review queue (below). No human yet.
   - **open** — set context with the human: what tranche, what it covers, what
     "good" looks like for this domain.
   - **structure** — the primary decision phase. For each queued item the human
     declares a **certainty band** (`high | medium | low | insufficient_evidence`)
     after viewing evidence. *Evidence-first is mandatory.*
   - **clarify** — resolve disputes / position changes (a `position_changed`
     flips a prior decision; keep the `prior_decision_id` link).
   - **close** — summarize, record, hand back.
2. **Evidence-first + Teach-Back** (D226 — anti-anchoring cognitive-forcing gate):
   before the human dispositions anything, they must engage the *evidence*, not your
   summary. The Teach-Back gate has them label the supporting sentences
   `correct | wrong | missing-something` and write a correction before they can
   decide. This exists because reviewers mirror AI suggestions 60–80% of the time
   (Buçinca et al. 2021). **Never let the human rubber-stamp your read.**
3. **Laddering** (decomposition probing): when a concept is fuzzy, probe its parts
   — "what makes this a Milestone? what would have to be true?" Capture the answer;
   it is exactly the "deeper context" the operator wants.
4. **Certainty bands, never numbers** (D120/D217): high/medium/low only — never a
   numeric confidence in anything the human sees.
5. **Plain language** (D522 conversational-assist discipline): no "entity type",
   "vertex", "ontology", "edge". Talk about contracts, parties, clauses, payments.

## The review queue (coverage-driven — NOT exhaustive)
Do not march the human through thousands of facts. Build a short, ranked queue:
1. **Coverage gaps** — call `grace_relationship_coverage`. Thin relationships are
   the top of the queue ("only 1 of 25 contracts has an obligation recorded — let's
   check whether these contracts have confidentiality terms").
2. **High-stakes facts** — parties, governing law, payment obligations, anything a
   wrong answer is expensive on.
3. **Structural oddities** — orphans, lopsided hubs, a single entity party to
   everything (use `grace_graph_aggregate` / `grace_get_neighborhood`).
Everything not queued is implicitly accepted for this tranche. Say so out loud.

## Session flow (step by step)
1. **prepare** — `grace_relationship_coverage` + `grace_graph_counts` →
   build & rank the queue. Pick the 1 worst coverage gap + 2–3 high-stakes samples.
2. **open** — `grace_session_start`; tell the human what this tranche is and how the
   review works (you facilitate, they decide; ~N items, not everything).
3. **structure** — for each queued item:
   a. Pull the evidence: `grace_get_neighborhood` / `grace_get_entity` for the real
      facts and their source.
   b. Run the **Teach-Back gate** (`grace_teachback_capture`): have the human label
      the evidence and write what's wrong/missing — *before* any decision.
   c. **Ladder** if fuzzy (`grace_laddering_followup`): probe the "why".
   d. Capture the decision + the human's **rationale** (this is the durable context).
   e. If it's a correction, route it through **Write-back**.
4. **clarify** — revisit anything the human reconsidered; link `prior_decision_id`.
5. **close** — `grace_session_close`; summarize what was confirmed, corrected,
   enriched, and which coverage gaps remain. Emit the calibration signal so Earned
   Autonomy can lighten the next similar tranche.

## Write-back (THE OPEN TUNING POINT — resolve this in the harness session)
There is no graph-in-place correction tool yet. The native write-back
(`claim_override_writer.promote_claim_to_graph`, supersession) operates on
**quarantined claims**, which this path bypassed. Three candidate paths — the
harness session picks and wires one:
- **(A) Direct graph correction** — a thin write path using `entity_ops` /
  `relationship_ops` + the `superseded_by` supersession property, applied to the
  vertex/edge in place. Most faithful to "review in place."
- **(B) Stage corrections as claims** — synthesize `extraction_claims` rows for the
  corrected facts and reuse the existing override writer + supersession + audit
  trail. Most reuse of established code; adds a staging layer.
- **(C) Correction queue + batch apply** — emit corrections as telemetry, apply via
  a CLI batch (D246-style). Cleanest audit; slowest loop.
Whatever is chosen MUST: preserve D106 idempotency, stamp decay verification
(`last_verified_at`, `verdict`, per D452), set `decision_source='human'`, and keep
an append-only audit trail. **Do not silently overwrite** — corrections supersede.

## Independence guardrails (this review must catch the extractor's mistakes)
- The human decides; you **never** auto-confirm.
- Drive the queue by **structural signals** (coverage, stakes), not by your own
  confidence in the extraction.
- Run review in a **fresh context** — no carried-over extraction memory/rationalizing.
- When you facilitate Teach-Back, present the **evidence**, not your interpretation.

## Tools
- **Read graph (built, heat-free):** `grace_relationship_coverage`,
  `grace_graph_counts`, `grace_graph_aggregate`, `grace_get_neighborhood`,
  `grace_get_entity`, `grace_search`.
- **Protocol / science / audit:** `grace_session_start`,
  `grace_session_advance_phase`, `grace_session_close`, `grace_laddering_followup`,
  `grace_teachback_capture`. (Currently bound to ontology-review sessions — see
  tuning note.)
- **Decision vocabulary (established):** approved, renamed, edited, split, merged,
  rejected, redirected, reclassified. Plain-language map (D522): keep/rename/merge/
  skip.

## Open tuning questions (for the harness-build session)
1. **Write-back path** — pick A / B / C above and wire it.
2. **Session binding** — `grace_session_start` currently wants
   `merge_run_id / reviewer / seed_schema_data` (ontology-review shaped). Either
   adapt it for a graph-review session or add a thin "graph-review session" surface.
   The *science* (phases, Teach-Back, laddering, bands) is reused as-is regardless.
3. **Calibration emit** — how a tranche review writes the Earned-Autonomy trust
   signal (`calibration_updater` / `trust_scores`) so the next similar tranche is
   lighter.
4. **Queue policy** — tune the coverage/stakes thresholds that decide what gets
   surfaced vs auto-accepted.
