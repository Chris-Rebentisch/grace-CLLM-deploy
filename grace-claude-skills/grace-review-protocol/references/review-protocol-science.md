# Review-protocol science — the established GrACE elicitation protocol

This is the source-grounded reference for `grace-review-protocol/SKILL.md`. Everything
here already exists in the GrACE codebase (Chunks 27–30, 44). Verify against these
paths before changing anything — the protocol is established, not invented.

## 1. Five-phase lifecycle
`src/elicitation/models.py` → `PhaseName = Literal["prepare","open","structure","clarify","close","none"]`
- **prepare** — system-side init (queue assembly).
- **open** — user context setting (shipped Chunk 27).
- **structure** — PRIMARY decision phase (Chunk 29, D228). `StructureDecisionPayload`
  carries `evidence_items_viewed`, `evidence_items_available`,
  `declared_certainty_band ∈ {high, medium, low, insufficient_evidence}`.
  Event: `structure_phase_entered`.
- **clarify** — resolve positional disputes (Chunk 29, D228). `ClarifyDecisionPayload`
  carries `position_changed: bool`, `prior_decision_id`. Event: `clarify_phase_entered`.
- **close** — finalization (shipped Chunk 27).
- Actor types: `human | system | agent` (D364). Append-only `elicitation_events` table
  (PostgreSQL trigger, D195). Envelope: `ElicitationEventEnvelope`
  (`models.py` ~1109) — `event_id, event_type, session_id, actor_type, phase_name,
  emitted_at, schema_version, grace_version, payload, payload_schema_version`,
  plus D364 `agent_id, agent_display_name, delegation_source`.

## 2. Teach-Back — the anti-anchoring cognitive-forcing gate (D226)
- Frontend: `frontend/components/instruments/TeachBackInstrument.tsx`.
- MCP: `grace_teachback_capture(session_id, element_name, narrative)` →
  `mcp_teachback_captured` event. Payload `TeachBackCompletedPayload`:
  `item_index, sentence_count, correct_count, wrong_count,
  missing_something_count, correction_chars_total`.
- **Mechanism:** the reviewer reads the source evidence spans, labels each sentence
  `correct | wrong | missing-something`, writes ≤240-char corrections on the latter
  two, and ONLY THEN may disposition (D231). It is a forcing function, deliberately
  adding friction so the human engages the evidence instead of the AI's summary.
- **Why:** anchoring — reviewers mirror AI suggestions 60–80% of the time. Cite
  Buçinca, Malaya & Gajos (2021) "To Trust or to Think: Cognitive Forcing Functions…"
  (CHI); Tsaneva et al. (2024) on KG validation with LLM + human-in-the-loop. (From
  `docs/chunk-30-research.md`, D230–D235.)

## 3. Laddering — decomposition probing
- Frontend: `frontend/components/instruments/LadderingInstrument.tsx`.
- MCP: `grace_laddering_followup(session_id, element_name, question)` →
  `mcp_laddering_followup_emitted`. Payload `LadderingStepCompletedPayload`:
  `step_index, parent_grace_id_hash, child_grace_id_hashes, step_duration_ms`.
- **Mechanism:** probe a concept's constituent parts / the "why" behind it. In
  fact-review it surfaces the human's tacit criteria ("what makes this a Milestone?")
  — the deeper context the operator wants captured.

## 4. Quarantined claim review (the NATIVE write-back — bypassed by our path)
- Routes: `src/api/claim_routes.py` — `GET /api/claims`, `GET /api/claims/{id}`,
  `POST /api/claims/{id}/accept` (with optional `modified_claim` = Edit-and-Accept),
  `POST /api/claims/{id}/reject`.
- Writer: `src/extraction/claim_override_writer.py` →
  `promote_claim_to_graph(claim, reviewer, notes, session, arcade_client)`. Uses
  `entity_ops.insert_entity` / `relationship_ops.insert_relationship` directly (NOT
  `graph_writer.write_batch`) → preserves D106 idempotency. Stamps decay props at
  accept (D452: `last_verified_at`, `confidence_at_verification`, `verdict='SUPPORTED'`),
  sets `decision_source='human'`, `human_decided_at`.
- Status enum (`src/extraction/claim_models.py`): `AUTO_ACCEPTED, QUARANTINED,
  REJECTED, SUPERSEDED`. Verdict: `PENDING, SUPPORTED, REFUTED, INSUFFICIENT`.
- **Supersession:** Edit-and-Accept creates a new claim with `supersedes_claim_id`;
  original flips to `SUPERSEDED`. Graph supersession uses the `superseded_by` vertex/
  edge property (D514).
- MCP: `grace_list_quarantined_claims`, `grace_get_claim`, `grace_accept_claim`,
  `grace_reject_claim`.
- **Why this is bypassed:** our onboarding writes straight to the graph, so
  `extraction_claims` has 0 rows for the tranche (verified: 0 `legal` claims). Hence
  "review in place" + the Write-back tuning decision in the SKILL.

## 5. Conversational review assistant (D522 — non-technical accessibility)
- `src/ontology/review_assist.py` → `run_review_assist(element, other_type_names,
  history, message, provider)` → `AssistResponse{reply, suggested_action}`.
- `SUGGESTED_ACTIONS = ("keep","rename","merge","skip","none")` → maps to
  `ReviewDecisionType` (`keep→approved, rename→renamed, merge→merged, skip→rejected`).
- System prompt forbids ontology/graph/entity jargon; 2–4 sentence plain answers;
  proposes exactly ONE action; never invents document facts. This is the plain-language
  discipline the review facilitator must adopt.

## 6. MCP session + review tools (the harness primitives)
`src/mcp_server/tools_session.py`:
- `grace_session_start(phase_state="prepare")` → POST `/api/ontology/review/start`
  (body `merge_run_id, reviewer, seed_schema_data`). **Ontology-review shaped — the
  graph-review binding is a tuning point.**
- `grace_session_advance_phase(session_id, target_phase)` → `phase_entered` event.
- `grace_session_close(session_id)` → close-summary + close-confirm.

`src/mcp_server/tools_review.py`:
- `grace_review_next_element(session_id)` — GET; element + CQ context + certainty band.
- `grace_review_session_summary(session_id)` — GET; reviewed/total + per-decision-type.
- `grace_review_decide(session_id, element_name, decision, rationale)` — decisions:
  `approved, renamed, edited, split, merged, rejected, redirected, reclassified,
  auto_approved`.
- `grace_laddering_followup(...)`, `grace_teachback_capture(...)` — see §2–3.

## 7. Graph-read tools added for review-in-place (this session, D530-adjacent)
- `grace_relationship_coverage` — per-relationship completeness, thinnest-first
  (the queue driver). `GET /api/graph/relationship-coverage`.
- `grace_graph_aggregate(edge_type, direction)` — ranked GROUP-BY ("which X has the
  most Y"). `GET /api/graph/aggregate`.
- `grace_graph_counts` — exact per-type counts. `GET /api/graph/counts`.
- `grace_get_neighborhood(grace_id, depth)` — an entity's edges + props in place.
- `grace_get_entity`, `grace_search` — lookup + semantic find.
All read-only, heat-free, domain-agnostic (parameterized; nothing legal hardcoded).

## 8. Discipline carried into review
- **D120/D217** — certainty bands only; no numeric score reaches the human.
- **D106** — canonical dedup idempotency on any write-back.
- **D452** — decay verification stamps on human-confirmed facts.
- **D195** — append-only telemetry; reviews are auditable events.
- **Earned Autonomy (Chunks 49–50)** — `calibration_updater`, `trust_scores`: tranche
  reviews feed the trust signal that lightens subsequent similar tranches.
