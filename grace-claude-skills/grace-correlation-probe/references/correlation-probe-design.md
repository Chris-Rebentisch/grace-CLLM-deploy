# grace-correlation-probe — design & decision record (A4, 2026-06-22)

## Mission
Roadmap **A4** — the cross-module **correlation engine**. Three linked goals:
1. Test the deterministic correlation engine LIVE (detection fidelity:
   precision/recall/substrate-honesty for the cross-module patterns).
2. Run **Claude AS the correlation engine** over the same fired signals + evidence
   (diagnostic groundedness, consistency, richness, abstention).
3. Compare, then wire any real Claude-exposed gap into the codebase under full
   discipline (the A1 "Claude-wrapped router" pattern, one layer up).

North star: **detection fidelity** (patterns fire on real signal combinations without
crying wolf) + **diagnostic groundedness** (Claude's root cause is supported by the
fired signals + evidence, not invented — the A2 faithfulness analogue).

## System under test
`src/analytics/correlation_engine/` — CLI-only (D246), out-of-process. Reads
`analytics_signals` + `signal_runs` + a small raw-Prometheus allowlist ONLY (D252);
writes `diagnostic_records` + `correlation_runs`. Orchestrator runs each detector
concurrently over a frozen `CorrelationRunContext`; writer is single-transaction,
idempotent on `(run_id, pattern_name, ontology_module)`.

## The substrate / fireability matrix (the A3 lesson, confirmed)
Each pattern's REAL input requirement (read from source, not docs):

| Pattern | DB signals | Prometheus | Root cause | Fires on this box |
|---|---|---|---|---|
| `schema_drift_per_module` | C≥0.5 AND D≥0.5 same module | — | ontology | **DB-only ✅** |
| `cq_regression_pre_extraction` | F≥0.5 | throughput MK not-decreasing (empty→"no trend"→stable→fires) | discovery | **F alone ✅** |
| `ontology_constraint_conflict` (D535) | E≥0.5 AND B≥0.5 same module | — | ontology | **DB-only ✅** |
| `relationship_gap_propagation` | B≥0.5 | zero_results spike >σ | extraction | Prometheus-gated |
| `extraction_quality_problem` | A≥0.5 | strategy_contributions drop >σ | extraction | Prometheus-gated |
| `graph_or_index_problem` | all 6 <0.3 | retrieval p95 latency spike >σ | graph | Prometheus-gated |

Substrate truth on this box: live `grace.analytics_signals` = 0 rows → engine emits 0
diagnoses (`status: success, records: 0`). All 4 Prometheus allowlist metrics return 0
current series → the 3 telemetry-gated patterns no-op cleanly. **3 of 6 patterns are
DB-only seedable and provable here** (schema_drift, cq_regression, ontology_constraint_conflict);
the other 3 are honest no-ops without TSDB backfill (a documented follow-on, mirrors
A3's A/C/E Prometheus limitation).

## Seeding approach (architect-chosen)
**Direct `analytics_signals` seeding** (not the A3 seed→signal-pipeline chain). The
engine reads `analytics_signals` (D252), so the signal table is the cleanest, most
controllable substrate for the correlation layer. `seed_correlations.py` writes ONE
`signal_runs` row (status=success, now()) + N signals under it (marker `a4probe`), so
the engine's "latest successful run" read picks them all up. Sandbox-guarded (refuses
any db not ending `_test`).

Fixtures: `drift` (C+D recall), `cqreg` (F recall), `econflict` (E+B recall / D535),
`healthy` (C-only precision), `lowf` (F<0.5 precision), `eonly` (E-only precision /
D535), `thin` (lone A abstention). `uncovered` (A+C — covered by NO pattern; opt-in,
exercises the Claude-reasoner richness path).

## What was proven (live, by hand, then gated)
- **Detection fidelity:** schema_drift→ontology 0.75; cq_regression→discovery 0.90;
  ontology_constraint_conflict→ontology 0.70; precision quiet on C-only/E-only/F-low;
  abstention on lone A; 3 Prometheus patterns honest no-op; empty→0; GOLD untouched; heat 0.
- **Claude-as-correlation-reasoner** over the same D252 bundle: groundedness 1.0,
  consistency-vs-engine 1.0 on shared fires, abstains on thin sets, RICHER on the A+C
  `uncovered` case. The scorer discriminates: a hallucinated module + wrong root cause +
  missed engine fire → FAIL (groundedness 0.5, consistency 0, flagged tokens).

## The gap → D535 (the in-tree wiring, architect-approved)
Running Claude AS the reasoner surfaced a cross-signal root cause the static 5-pattern
library missed: **Signal E (domain/range violation) + Signal B (missing edge) same
module → the ontology's relationship constraints are misaligned with what extraction
observes** (extraction declines the edge → B, or writes one that violates the constraint
→ E). Signal E appeared in NO pattern's trigger (only the all-quiet guard of
`graph_or_index_problem`), so a high-E module was un-diagnosable.

**D535 (amends D250)** wires this deterministically as the 6th pattern
`ontology_constraint_conflict` (E≥0.5 AND B≥0.5 same module → ontology, strength=(E+B)/2),
structurally mirroring `schema_drift_per_module`. Deliberately complementary to
`relationship_gap_propagation` (also reads B but fires on B + a Prometheus spike → root
cause extraction); the two key distinctly on `pattern_name`.

Surface (full discipline, capture-the-why D356 throughout):
- `PatternNameLiteral` 5→6; new detector `patterns/ontology_constraint_conflict.py`;
  registered in `DEFAULT_DETECTOR_CLASSES` + cli `PATTERN_DETECTORS` (5→6).
- New gauge `grace_correlation_ontology_constraint_conflict_strength`
  (GOLDEN_NAMES 151→152; metric-contract entry added).
- Migration `d535_diag_pattern_6th` widens `ck_diagnostic_records_pattern` 5→6 names
  (drop+re-add, runbook Pattern C; revision-id 21 chars ≤ 32 per D350).
- Unit tests (recall + precision + strength-mean + hermetic no-conjunction).
- Verified: `tests/analytics/correlation_engine` + `test_metric_contract` 28/28; live
  engine fires the pattern (run-all + isolated `--pattern`); golden gate 13/13; A1
  10/10, A2 16/16, A3 signal 10/10 + remediation 11/11; GOLD untouched; heat 0.

D250 explicitly anticipated this: `patterns/__init__.py` said "adding a sixth pattern
requires a new D-series amendment." D535 is that amendment.

## Lessons applied (from A1/A2/A3)
- **Verify "LLM-free" yourself** — grepped `get_provider` in correlation_engine → empty
  (A3 caught two FALSE such claims; trust nothing).
- **Substrate reality check is Step 1 and a first-class output** — distinguished
  DB-seedable patterns from Prometheus-gated no-ops; never conflated a quiet pattern with
  a broken one.
- **No single headline number** — the scorer pairs groundedness with consistency +
  richness + abstention; a hallucinated-but-confident diagnosis is caught by a DIFFERENT
  co-signal than a wrong-root-cause one.
- **Groundedness is relative to the provided context and bounded by upstream** — a thin
  signal set → "insufficient evidence to correlate" (abstention), credited not penalized.
- **Heat 0 is an invariant** — asserted before/after at every step and in the gate.
- **Client/probe discipline** — drove the CLI, read outputs, never wired Claude into the
  deterministic core; the in-tree change went through the full D-number gate with
  architect approval first.

## Known limits / follow-ons (informational AUDIT)
- The 3 Prometheus-gated patterns' FIRING (not no-op) needs timestamp-controlled TSDB
  backfill (no pushgateway on this box) — a documented follow-on harness, identical in
  spirit to A3's A/C/E limitation.
- A fresh post-D535 gap (`uncovered`: A extraction-confidence + C type-drift co-elevated)
  is flagged via the richness path — a candidate cross-module pattern (extraction↔ontology
  drift coupling) for a future architect-gated wiring, NOT built this session.
- `diagnostic_records` is not append-only-triggered (plain unique index) — safe to
  TRUNCATE in the sandbox; the live `correlation_runs` had 1 historical row.

## Step-7 stress test (2026-06-22) — how we tested the harness itself
Full pass across three axes (heat 0, GOLD untouched):
- **Effectiveness (mutation):** break a detector → the gate MUST go red (4/4 caught, right gate
  flips, byte-identical restore). A green gate proves nothing until shown to fail. Scorer defect
  sweep 11/11 — the co-signal design proven: "wrong root_cause" keeps groundedness 1.0 but
  consistency catches it.
- **Friction (cold-agent swarm on isolated `_test` DBs):** 3 fresh agents from the SKILL alone;
  one independently reproduced the gate portability bug. Isolated-DB concurrency works; same-DB
  probe TRUNCATE races (documented constraint).
- **Quality (variance):** N independent reasoners on one bundle. Claude is unanimous on
  unambiguous conjunctions + abstentions, and diverges SYSTEMATICALLY only on contestable calls
  (lone-strong-signal fire; cross-module direction). Variance tracks real ambiguity → "richer"
  and divergent diagnoses are human-review flags, never auto-accept.

Five fixes shipped (approval-gated): (1) gate swarm-portability (`GRACE_GOLD_URL` + GATE-12
self-skip); (2) "richer" requires ≥2 cited signals (single-signal fire ≠ richness); (3) consistency
credits engine `candidate_root_causes` + a `--panel` multi-reasoner divergence mode; (4) compose
instruction (lone-strong-signal diagnosable) + SKILL diagnosis-schema/`--db-url` docs; (5) D535
detector emits `candidate_root_causes=["ontology","extraction"]` + `boundary_case` (additive
evidence; no migration). The harness now surfaces its OWN next candidate pattern (the `--panel`
flagged `uncovered` A+C convergence) — the completeness-critic loop closing on itself.

**Lesson:** a self-monitoring harness must be adversarially tested like the system it guards —
mutate the engine to confirm the gate binds, swarm cold agents to find friction, and run the
non-deterministic reasoner N times to separate reliable reasoning from genuine domain ambiguity.
The deepest finding was that the harness questioned its own wired decision (D535 direction) — that
is the harness working, not failing.
