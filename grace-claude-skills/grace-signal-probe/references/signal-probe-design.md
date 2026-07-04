# Signal probe — design + decision record

## ★ NORTH STAR
**Prove GrACE notices its own ontology is incomplete — with detection fidelity.** The
six deterministic detectors must fire on a real seeded gap (recall), stay quiet on a
healthy module (precision), and be honest about absent substrate (a quiet detector is
not a clean one). Heat-free throughout (the pipeline is LLM-free).

## 1. What this is (and is not)
- **Is:** a fault-injection + detection-audit harness. Seeds known gaps into the
  `grace_test` SANDBOX, drives the real `signal_pipeline` CLI (D246, out-of-process),
  reads `analytics_signals`, and scores recall/precision/substrate-honesty.
- **Is not:** part of the signal computation. It never imports a detector into a route
  or scheduler, never mutates the live `grace` GOLD corpus, and adds no in-process
  scheduler (the D246 hard "Do not").

## 2. The system under test
`python -m src.analytics.signal_pipeline run-all [--signal X --ontology-module M --config Y --dry-run]`
→ `src/analytics/signal_pipeline/`. Reads Prometheus + Postgres; writes append-only
`analytics_signals` + `signal_runs`. Detectors A–F, strength ∈ [0,1] (see SKILL.md).
**Verified LLM-free:** `grep get_provider src/analytics/signal_pipeline/` → empty.

## 3. Decisions captured this session (the harness contract)
- **D-SP1 Sandbox-only seeding.** Gaps are seeded into `grace_test` (db name must end
  `_test`); `seed_gaps.py` refuses any other db. GOLD is read-only. Mirrors the test-DB
  isolation invariant.
- **D-SP2 Strength is the signal; `emit_threshold` is vestigial.** `emit_threshold` is
  defined in `config.py` but used NOWHERE in the pipeline; `write_run` persists every
  record regardless of strength. "Fired" is read off STRENGTH (>0), not row-existence.
  (The A2 "don't trust config fields" lesson, recurring.)
- **D-SP3 Substrate honesty is a first-class output.** A detector that produces no record
  is reported as either a correct quiet (healthy module) or a substrate no-op (absent
  Prometheus history / <10-point series / empty table) — never conflated with a false
  negative.
- **D-SP4 A/C/E firing-validation deferred (TSDB backfill follow-on).** A/C/E read
  Prometheus (current window vs 14-day baseline, sigma/rate). There is no pushgateway /
  remote-write ingress on this box, and a single synthetic sample cannot create the sigma
  separation; a true fire needs timestamp-controlled TSDB history. The probe proves A/C/E
  RUN and correctly NO-OP; firing-validation is a documented follow-on.
- **D-SP5 Heat 0 is an invariant.** The pipeline is LLM-free; `ollama ps` is checked
  before/after and asserted by the gate. The configured 70B was swapped to qwen2.5:7b
  defensively (campaign-wide) so no path can reach for the unfittable model.

## 4. Findings (the substance)
- **On the live `grace` GOLD corpus, ALL SIX detectors no-op** — and the reasons are
  substrate, not defect:
  - Prometheus `grace_*` namespace has **0 series** (grace-api scrape target down) → A/C/E.
  - `extraction_claims`: 3194 rows but **all on one day (2026-05-25, 28 days stale)** and
    **all `ontology_module = NULL`** → B's `_modules()` returns `[]`; D's daily-count series
    is a single point.
  - `cq_test_runs`: **0 rows** → F.
  - With default config (`current_window_days=1`, `mann_kendall_min_points=10`,
    `baseline_window_days=14`) this is the correct outcome. Confirmed live: `run-all --dry-run`
    → `records: 0`. Matches `analytics_signals=0` after 5 prior runs.
- **Seeded into `grace_test`, B/D/F fire with fidelity** (heat 0):
  - B: `a3probe_orphan` strength **1.0** (3/3 orphan pairs) / `a3probe_healthy` **0.0** (connected).
  - D: `a3probe_deprecate` strength **1.0**, trend decreasing (p≈8e-6) / `a3probe_stable` no record (flat).
  - F: `__global__` strength **1.0**, trend increasing (p≈8e-6).
- **Signal B is Postgres-only** — both the orphan pairs and the connecting relationships come
  from `extraction_claims` (not the ArcadeDB graph). The window cutoff (`now − current_window_days`)
  and the `ontology_module IS NOT NULL` filter are the two things that silence it on GOLD.
- **Mann-Kendall validity on short series:** D/F skip emission below `mann_kendall_min_points`
  (10). The seeder lays down 12 ordered points to clear the floor — a deliberate property of the
  fixtures, not a detector quirk.

## 5. Validation evidence (why we trust the harness)
- **Golden gate 10/10** (`signal_golden_gate.py`): heat-0 (initial+final), B recall+precision,
  D recall+precision, F recall, substrate-honesty (clean db → 0 signals), A/C/E run+no-op,
  strength-is-the-signal.
- Proved live by hand BEFORE generalizing (the A1/A2 discipline): seeded B in grace_test, ran the
  CLI with `DATABASE_URL` override, verified strengths against the prediction.

## 6. Build status (2026-06-22 — built + gate-green)
- `scripts/seed_gaps.py` — B/D/F sandbox seeder (idempotent, marker `a3probe_`) + `--clean` +
  `--prometheus` substrate report. Inserts a sentinel `ontology_versions` row for F's FK.
- `scripts/signal_probe.py` — CLI client; per-(signal,module) strength + fired/quiet + no-op honesty.
- `scripts/signal_golden_gate.py` — regression anchor + AUDIT.
- `grace-signal-probe/SKILL.md` — north star + science + flow.

## 7. In-tree candidates
- **signal_mapping malformed KGCL — FIXED (D534, 2026-06-22)** (shared with the remediation
  harness): `signal_mapping.py` emitted non-grammar KGCL for 5 of 8 branches (B/C/E/F-rel/F-prop).
  Fixed to the canonical `kgcl_generator.py` forms with quoted names; guarded by
  `tests/ontology/test_signal_mapping.py::test_all_templates_parse`. Ratified D534.
- **`proposal_generator run` heat leak (H1):** line 212 unconditionally calls
  `generate_evidence_summary` → `get_provider()`. Today bounded by the qwen2.5:7b config swap and
  by Ollama 404-ing on an absent model; a cleaner design would gate the NL-summary behind an
  explicit flag. Logged, not built.

## 8. Deferred / follow-on (do not lose)
- **A/C/E firing via TSDB backfill** — a harness that writes timestamp-controlled samples to a
  pushgateway / Prometheus remote-write (a genuine baseline + spike) to exercise the sigma/rate
  detectors. Needs infra (no ingress today).
- **A4 correlation engine** (`grace-correlation-probe`) — cross-module patterns over the fired
  signals (`correlation_engine`, D252). The signals this harness produces are its input.
- **Full live detect→propose→apply loop** — once gap-closure (qwen-gated apply) lands in the
  remediation harness, chain signal → proposal → sandbox apply → re-detect (signal stops firing).

## 9. Acceptance swarm (2026-06-22 — 3 cold agents, log-only)
Three independent cold-start agents (detection / remediation-scoring / apply) drove the harnesses
from the docs alone on isolated `_test` DBs. All three reached "trustworthy + self-sufficient";
detection confirmed B/D/F fire + precision + substrate-honesty. Friction found + FIXED:
- **F-SP1 (blocking, FIXED):** `signal_golden_gate.py` GATE-0 guarded `startswith("grace_test")`
  while the seeder/probe/docs use `endswith("_test")` — the gate refused valid `_test` siblings
  (blocked the swarm's isolated DB). Aligned to `endswith("_test")`; guard verified by running the
  gate 10/10 on a non-`grace_test` sibling.
- **F-SP2 (cosmetic, intentional):** `signal_probe.ollama_clean` flags qwen as "loaded". This is
  CORRECT for the heat-free signal path (qwen loaded = something external); the gates separately
  allow qwen for the apply path. Left as-is by design.
