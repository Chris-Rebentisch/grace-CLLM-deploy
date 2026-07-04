---
name: grace-signal-probe
description: >
  Prove a built GrACE graph can NOTICE its own ontology is incomplete — the
  self-monitoring layer (roadmap A3). Drives the six deterministic gap detectors
  (Signals A–F: missing types, co-occurrence-without-edge, type drift, deprecation,
  domain/range violations, CQ-driven gaps) live via their sanctioned CLI
  (python -m src.analytics.signal_pipeline run-all — D246, out-of-process), and
  measures DETECTION FIDELITY: does the right detector fire on a known seeded gap
  with sensible strength (RECALL), stay quiet on a healthy module (PRECISION), and
  NEVER false-fire on empty substrate (SUBSTRATE HONESTY)? The signal pipeline is
  LLM-free (PromQL/SQL + Mann-Kendall) so running it is HEAT-FREE — only
  nomic-embed-text ever loads. It is a CLIENT/PROBE: it seeds gaps into the
  grace_test SANDBOX (never the live grace GOLD corpus), reads analytics_signals,
  and never wires into the deterministic core. Use after the consume side (A1
  retrieval, A2 regeneration) is trusted, to validate that GrACE flags the gaps a
  human sweep would.
---

# grace-signal-probe

## ★ NORTH STAR
**Prove GrACE notices its own gaps — without crying wolf.** A1 proved retrieval finds
the grounded subgraph; A2 proved regeneration answers faithfully from it. A3 asks:
does the system detect when its ontology is INCOMPLETE? The headline property is
**detection fidelity** — the deterministic detectors must flag a real gap (recall)
without firing on a healthy module (precision), and must be honest about when they
simply have no substrate to judge (a quiet detector is not the same as a clean one).

## Role
You are the **fault-injector + detection auditor, outside the detector loop.** You
seed a *known* gap into the `grace_test` sandbox, run the real pipeline against it,
and check that the right detector fires with the right strength — and that nothing
fires on clean substrate. You are **not** part of the signal computation: you drive
the sanctioned CLI (D246) and read `analytics_signals`; you never import a detector
into a route or scheduler.

## When this runs
- **Trigger:** the consume side is trusted (A1/A2 green) and you want to confirm the
  self-monitoring layer flags real gaps — or to regression-check after a detector,
  schema, or telemetry change.
- **Feeds** `grace-gap-remediation-harness`: a fired signal + its evidence is that
  harness's input (signal → Claude-proposed KGCL fix → 4-co-signal score).
- **Domain-agnostic:** the seeded modules/types are synthetic markers (`a3probe_*`);
  nothing legal-hardcoded.

## The system under test (read, drive via CLI — D246 out-of-process)
`python -m src.analytics.signal_pipeline run-all [--signal X --ontology-module M --config Y --dry-run]`
→ `src/analytics/signal_pipeline/`. Reads Prometheus + Postgres (`extraction_claims`,
`cq_test_runs`); writes append-only `analytics_signals` (+ `signal_runs`). Six detectors,
strength ∈ [0,1]:
- **A** missing types — rising `verdict="INSUFFICIENT"` rate (Prometheus `grace_extraction_triple_confidence`).
- **B** co-occurrence-without-edge — entity pairs in the same `source_chunk_id` with no relationship claim; `strength = orphans/total`. **Postgres-only.**
- **C** type drift — rising `grace_extraction_validation_failures_total` (invalid_entity_type / schema_version_mismatch).
- **D** deprecation — Mann-Kendall DECREASING daily entity-type count (p<0.05); strength = 1−p.
- **E** domain/range violation — like C (domain_violation / range_violation).
- **F** CQ-driven gaps — Mann-Kendall INCREASING `cq_test_runs` failure rate (p<0.05).

## The heat reality (verify, don't assume)
- The signal pipeline is **LLM-free** — it never calls `get_provider()`. Running it is
  heat-free; only `nomic-embed-text` may load. **Verify yourself** (`grep get_provider
  src/analytics/signal_pipeline/` → empty), and run `ollama ps` before/after.
- The configured provider model is the (unfittable) 70B; this campaign swapped it to
  `qwen2.5:7b` defensively. The signal path doesn't touch it either way.

## The science (how to judge a detector)
- **Substrate first.** Several detectors need a baseline window / time series. On a fresh
  box A/C/E (Prometheus history), D (≥10 days of claims), F (≥10 cq_test_runs) legitimately
  NO-OP. Step 1 is establishing whether the telemetry substrate exists to produce a signal
  at all — and saying so. A quiet detector ≠ a broken detector.
- **Recall:** seed a known gap → the right detector returns a record with strength meaningfully
  > 0 and the right trend direction.
- **Precision:** a healthy/connected module → strength ≈ 0 (or no record).
- **Strength is the signal** — `emit_threshold` in config is VESTIGIAL (used nowhere); every
  record persists regardless of strength. Read "fired" off STRENGTH, not row-existence.
- **No false-fire on empty:** a clean db → zero signals.

## What this session proved (2026-06-22)
- On the live `grace` GOLD corpus **all six detectors correctly no-op** (extraction_claims
  single-day + `ontology_module` NULL; `cq_test_runs` empty; Prometheus `grace_*` empty).
- Seeded into `grace_test`: **B** fires 1.0 on an orphan module / 0.0 on a healthy one; **D**
  fires on a decreasing daily-count trend; **F** fires on a rising CQ failure rate — each quiet
  on its control. **A/C/E** run and correctly no-op (no Prometheus ingress to seed; firing-
  validation is a documented TSDB-backfill follow-on). **Golden gate 10/10, heat 0.**

## Session flow (step by step)
1. **Preflight** — `ollama ps` (heat 0); Prometheus :9090 + Postgres up; confirm test-DB
   isolation (`grace_test` exists, migrated).
2. **Substrate reality check** — `python3 seed_gaps.py --prometheus`; report what's available
   vs empty per detector. Do NOT mistake "no substrate" for "no gap".
3. **Seed + run** — `python3 seed_gaps.py --detector all` then `python3 signal_probe.py`.
   Verify fired/quiet/no-op against expectation.
4. **Regression** — `python3 signal_golden_gate.py` (10 gates + AUDIT). Heat-0 is an invariant.
5. **Log** to `runs/signal-pipeline-probe/session-N-*.md`; never mutate GOLD.

## Hard constraints
- **Sandbox only.** Seed `grace_test` (db name must end `_test`); `seed_gaps.py` refuses any
  other db. Never manufacture a signal by mutating the GOLD graph.
- **Client/probe discipline (D246).** Drive the CLI; read `analytics_signals`. Never import a
  detector into a route/scheduler. In-tree enhancements (e.g. the malformed `create edge`
  baseline) are LOGGED for the architect, never built without a new D-number + capture-the-why.
- **Heat 0 is an invariant.** `ollama ps` clean before and after; the gate asserts it.

## Tools (under ~/grace-claude-skills/scripts/)
- `seed_gaps.py` — fault injector: seeds B/D/F into grace_test (idempotent, marker `a3probe_`);
  `--clean`; `--prometheus` (A/C/E substrate report).
- `signal_probe.py` — runs the pipeline against a target DB (DATABASE_URL override), reports
  per-(signal,module) strength + fired/quiet + substrate-honest no-op.
- `signal_golden_gate.py` — regression anchor (recall/precision/substrate-honesty/heat-0) + AUDIT.
- Design + decision record: `references/signal-probe-design.md`.
