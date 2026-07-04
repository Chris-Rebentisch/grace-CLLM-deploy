---
name: grace-correlation-probe
description: >
  Prove a built GrACE graph can CORRELATE multiple self-monitoring signals into a
  cross-module root-cause diagnosis — the diagnosis layer above A3's single-gap
  detection (roadmap A4). Drives the deterministic correlation engine live via its
  sanctioned CLI (python -m src.analytics.correlation_engine run-all — D246,
  out-of-process) and measures DETECTION FIDELITY for the cross-module PATTERNS: does
  the right pattern fire on a known seeded signal COMBINATION with a sensible
  severity/confidence band (RECALL), stay quiet when a conjunction is unsatisfied
  (PRECISION), abstain on thin/uncorrelated sets (NO CRY-WOLF), and never false-fire
  on empty analytics_signals (SUBSTRATE HONESTY)? It ALSO runs Claude AS the
  correlation reasoner over the same D252 inputs and scores DIAGNOSTIC GROUNDEDNESS +
  consistency-vs-engine + richness (where Claude finds a cross-signal root cause the
  static patterns miss). The engine is LLM-free (PromQL/SQL + Mann-Kendall) so running
  it is HEAT-FREE — only nomic-embed-text ever loads; Claude reasons in-loop (no local
  model). It is a CLIENT/PROBE: it seeds signal combinations into the grace_test
  SANDBOX (never the live grace GOLD corpus), reads diagnostic_records, and never wires
  Claude into the deterministic core. Use after A3 (signal detection) is trusted.
---

# grace-correlation-probe

## ★ NORTH STAR
**Does GrACE correlate multiple signals into a cross-module root cause — and can Claude
do that reasoning faithfully?** A3 asked "does GrACE notice a single gap?" A4 asks "does
it CORRELATE co-occurring signals into a diagnosis?" Two headline properties:
**detection fidelity** (patterns fire on real signal combinations without crying wolf)
and **diagnostic groundedness** (a root-cause is supported by the fired signals +
evidence, not invented — the A2 faithfulness analogue, one layer up).

## Role
You are the **fault-injector + diagnosis auditor, outside the engine loop.** You seed a
*known signal combination* into the `grace_test` sandbox, drive the real engine against
it, and check the right pattern fires with the right root-cause module + band — and that
nothing fires on an unsatisfied conjunction or empty substrate. You ALSO reason AS the
correlation engine in-loop and score your own diagnosis for groundedness/consistency.
You are **not** part of the engine: you drive the sanctioned CLI (D246) and read
`diagnostic_records`; you never import a detector into a route/scheduler, and you never
wire Claude into the deterministic core (D252 read-allowlist preserved).

## When this runs
- **Trigger:** A3 (signal detection) is trusted and you want to confirm the diagnosis
  layer correlates real signal combinations — or to regression-check after a pattern,
  schema, or telemetry change.
- **Depends on A3's output:** the engine's INPUT is `analytics_signals` — which A3
  produces. On GOLD all six signal detectors no-op, so `analytics_signals` is empty and
  the engine no-ops too. The probe seeds signal combinations DIRECTLY (the cleanest
  substrate for the correlation layer; D252 reads `analytics_signals`).
- **Domain-agnostic:** seeded modules are synthetic markers (`a4probe_*`); the
  vocabulary is signals A–F + the pattern names, never domain entities.

## The system under test (read, drive via CLI — D246 out-of-process)
`python -m src.analytics.correlation_engine run-all [--pattern P --ontology-module M --dry-run]`
→ `src/analytics/correlation_engine/`. Reads `analytics_signals` + `signal_runs` + a
small raw-Prometheus allowlist ONLY (D252 — never raw `extraction_claims`/`cq_test_runs`);
writes `diagnostic_records` + `correlation_runs`. **Six** patterns (D250 locked five;
**D535** added the sixth this session), each emitting root_cause + correlation_strength
∈ [0,1] + contributing_signals + evidence:

| Pattern | Inputs (conjunction) | Prometheus? | Root cause | Seedable here |
|---|---|---|---|---|
| `schema_drift_per_module` | C≥0.5 AND D≥0.5 same module | no | ontology | **DB-only ✅** |
| `cq_regression_pre_extraction` | F≥0.5 (+ throughput not-decreasing) | optional (empty→"no trend"→fires) | discovery | **F alone ✅** |
| `ontology_constraint_conflict` (D535) | E≥0.5 AND B≥0.5 same module | no | ontology | **DB-only ✅** |
| `relationship_gap_propagation` | B≥0.5 + zero-results spike | YES | extraction | Prometheus-gated |
| `extraction_quality_problem` | A≥0.5 + contributions drop | YES | extraction | Prometheus-gated |
| `graph_or_index_problem` | all 6 <0.3 + latency spike | YES | graph | Prometheus-gated |

## The heat reality (verify, don't assume)
- The correlation engine is **LLM-free** — it never calls `get_provider()`. **Verify
  yourself** (`grep -rn get_provider src/analytics/correlation_engine/` → empty; the
  golden gate asserts this, GATE-2). Run `ollama ps` before/after — only
  `nomic-embed-text` may load. Claude-as-correlation-reasoner runs **in-loop** (this
  chat) — no local model.

## The science (how to judge)
- **Substrate first.** The 3 Prometheus-gated patterns need current-vs-14d-baseline TSDB
  telemetry to FIRE; on a box with no pushgateway they correctly NO-OP. A quiet pattern ≠
  a broken one — report the no-op honestly (GATE-10), never conflate it with breakage.
- **Recall:** seed a signal combination → the right pattern returns a record with the
  right root-cause module and a sensible strength (a BAND in the UI; the raw float lives
  only in the engine output, D120/D217).
- **Precision:** an unsatisfied conjunction (C without D; E without B; F<0.5) → no record.
- **No cry-wolf:** a single weak signal (lone A=0.55) → no record / Claude abstains.
- **Empty → 0:** clean `analytics_signals` → zero diagnoses (GATE-11), GOLD untouched
  (GATE-12).
- **Diagnostic groundedness (Claude path):** every claim in Claude's diagnosis (cited
  signals, module, root cause, evidence) must resolve in the input bundle; abstention is
  grounded by construction. A single "looks plausible" number is unsafe — pair it with
  **consistency-vs-engine** + **richness** (grounded diagnoses the engine misses are
  credited) + **abstention**.

## What this session proved (2026-06-22, A4)
- Live by hand: `schema_drift_per_module` fires on C+D (ontology, 0.75);
  `cq_regression_pre_extraction` on F (discovery, 0.90); precision quiet on C-only/F-low;
  the 3 Prometheus-gated patterns no-op cleanly; empty `analytics_signals` → 0; GOLD
  untouched; heat 0.
- **Claude AS the correlation reasoner** over the same D252 bundle: 100% grounded, 100%
  consistent with the engine on shared fires, abstains on thin sets, and surfaced a
  cross-signal root cause the static 5-pattern library MISSED — E (domain/range
  violation) + B (missing edge) same module → ontology constraint conflict. **Wired
  deterministically as the 6th pattern (D535, amends D250).** A fresh post-D535 gap
  (A+C co-elevated) is flagged via the richness path for future consideration.
- **Golden gate 13/13, scorer discriminates (good→pass, hallucinated/missed→fail), heat 0.**

## Session flow (step by step)
First set your sandbox once: `SANDBOX=postgresql+psycopg2://<user>@localhost:5432/<you>_test`
(must end `_test`; the seeder/probe/compose all refuse anything else). Pass `--db-url
"$SANDBOX"` on every command so you never rely on ambient `DATABASE_URL`.
1. **Preflight** — `ollama ps` (heat 0); Postgres + Prometheus :9090; your `_test` sandbox
   exists, migrated to head (incl. `d535_diag_pattern_6th`).
2. **Substrate reality check** — `run-all --dry-run` on GOLD → `records: 0` (empty
   analytics_signals); which patterns are DB-seedable vs Prometheus-gated here.
3. **Seed + probe** — `python3 seed_correlations.py --combination all --db-url "$SANDBOX"`,
   then `python3 correlation_probe.py --db-url "$SANDBOX" --json`; verify fired/quiet/no-op.
4. **Claude-as-reasoner** — `python3 correlation_compose.py --db-url "$SANDBOX" --json
   --include-engine > bundle.json`; reason in-loop to a diagnosis JSON (schema below);
   `python3 correlation_score.py --diagnosis claude.json --compose-json bundle.json`. For a
   reliability read, run several reasoners and pass them all to `--panel a.json b.json …`.
5. **Regression** — `python3 correlation_golden_gate.py` (13 gates + AUDIT). Heat-0 is an
   invariant. Mirrored at CI: `tests/smoke/test_a4_harness_gates.py`. For a parallel swarm on
   isolated DBs, set `GRACE_GOLD_URL` to your real GOLD db (or GATE-12 self-skips when the
   `<name>` sibling of your `_test` DB does not exist).
6. **Log** to `runs/correlation-probe/session-N-*.md`; never mutate GOLD.

## Diagnosis JSON schema (the Claude-as-reasoner artifact)
The reasoner (you) emit exactly this shape; `correlation_score.py` consumes it:
```json
{"diagnoses": [
   {"module": "<from the bundle>",
    "root_cause": "extraction|retrieval|graph|ontology|discovery",
    "band": "low|medium|high",
    "cited_signals": ["C", "D"],
    "rationale": "phrase in the signal-legend vocabulary so tokens ground in the context"}
 ],
 "abstentions": ["<modules you decline to diagnose>"]}
```
- `cited_signals` are **bare letters**; `root_cause` is one of the five modules.
- `band` is **advisory** (reported, not gated) — don't agonize over it.
- On a module the engine also fires, match **any** of its `candidate_root_causes` (boundary
  patterns like D535 emit two) to score as *agree*, not *disagree*.
- A grounded diagnosis the engine has no pattern for is credited as **richer** only when it
  cites **≥2 signals** (a real cross-signal correlation); a grounded single-signal fire is
  reported as an aggressiveness flag, not richness.

## Hard constraints
- **Sandbox only.** Seed `grace_test` (db name must end `_test`); `seed_correlations.py`
  refuses any other db. Never manufacture a diagnosis by mutating GOLD.
- **Client/probe discipline (D246/D252).** Drive the CLI; read `diagnostic_records`. Never
  import a detector into a route/scheduler; never wire Claude into the deterministic core;
  the engine's D252 read-allowlist (`analytics_signals` + `signal_runs` + raw-metric
  allowlist) is preserved.
- **In-tree changes need full discipline.** D535 (the 6th pattern) shipped with a ratified
  D-number + capture-the-why (D356) + migration + metrics + GOLDEN_NAMES contract + unit
  tests + re-run prior gates — STOP for architect approval first. Never relax a lock
  unilaterally.
- **Heat 0 is an invariant.** `ollama ps` clean before and after; the gate asserts it.

## Tools (under ~/grace-claude-skills/scripts/)
- `seed_correlations.py` — fault injector: seeds signal COMBINATIONS into grace_test
  directly (marker `a4probe_`, one `signal_runs` row); `--combination all|drift|cqreg|
  econflict|healthy|lowf|eonly|thin|uncovered`; `--clean`.
- `correlation_probe.py` — drives the engine CLI against a sandbox (DATABASE_URL override),
  clears outputs, reads back `diagnostic_records` as structured ground truth.
- `correlation_compose.py` — read-only pull of the D252 input bundle (+ engine diagnoses)
  for the Claude-as-correlation-reasoner harness.
- `correlation_score.py` — diagnostic-groundedness co-signal scorer (groundedness +
  consistency-vs-engine + richness + abstention); reuses `faithfulness_score.py`.
- `correlation_golden_gate.py` — regression anchor (13 gates: heat, LLM-free, recall,
  precision, D535, Prometheus no-op honesty, substrate honesty, GOLD untouched) + AUDIT.
- Design + decision record: `references/correlation-probe-design.md`.
