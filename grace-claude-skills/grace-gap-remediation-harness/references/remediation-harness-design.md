# Gap-remediation harness — design + decision record

## ★ NORTH STAR
**Prove Claude can propose the FIX — grounded, not invented.** Given a fired gap signal +
its evidence, Claude (at the edge, in-loop) proposes ONE KGCL schema change; the harness
scores it on FOUR co-signals (groundedness, well-formedness, gap-closure, non-regression),
never one headline number. The load-bearing property is **remediation groundedness**: the
change is anchored in the evidence + source chunks + ontology, and thin evidence yields an
honest abstention, not a confident invention.

## 1. What this is (and is not)
- **Is:** a remediation-proposal + scoring harness at the EDGE of the deterministic chain.
  Reads a fired signal's OUTPUT, has Claude propose a KGCL change + rationale, and scores it.
- **Is not:** Claude inside the detectors or `proposal_generator` (that loses determinism /
  auditability and adds heat). Claude reads OUTPUT and proposes; the deterministic pipeline +
  gate decides; the deterministic KGCL executor applies.

## 2. The system under test
- `analytics_signals` (the fired gap, from grace-signal-probe) — **input**.
- `proposal_generator` (Chunk 47) + `signal_mapping.py` (deterministic BASELINE) +
  `evidence_bundle` → `schema_proposals`. **Heat leak (H1):** `proposal_generator run` calls
  `generate_evidence_summary` → `get_provider()`. The harness does not use this path.
- `change_executor parse "<kgcl>"` — pure KGCL validator (no DB/graph/LLM) → the
  well-formedness co-signal. `apply` is mutating + loads an LLM in the CQ-gate (heat).
- `faithfulness_score.py` (A2) — reused wholesale for the groundedness co-signal.

## 3. Decisions captured this session (the harness contract)
- **D-RM1 Four co-signals, never one number.** A proposal is scored on groundedness (1),
  well-formedness (2), gap-closure (3, deferred), non-regression (4, deferred). The headline
  "core" verdict folds only 1 & 2 (the heat-free core); 3 & 4 are explicitly deferred, never
  silently green. (The A2 "a single score is unsafe" lesson.)
- **D-RM2 Groundedness = faithfulness(rationale, evidence).** A KGCL relationship/class NAME is
  Claude's synthesis, not a verbatim token — so the scorer does NOT string-match the name. It
  scores Claude's grounding RATIONALE against the evidence: every salient entity/number must
  resolve, invented facts are flagged, **abstention is credited as faithful**. Reuses the mature
  A2 scorer; adds a placeholder-name check for the mechanical baseline's defaults.
- **D-RM3 Claude proposes in-loop, at the edge.** No `get_provider()`, no local model, no API
  key for the propose step (`LLM_API_KEY` has historically 401'd → in-loop is the default). The
  CQ-gate inside `apply` (co-signal 4) has no Claude seam — it's an internal `get_provider()`
  call → runs on **qwen2.5:7b** in the gated follow-on, NEVER the 70B.
- **D-RM4 Parse-only core; apply is a gated follow-on.** The heat-free core ships co-signals 1
  & 2. Gap-closure + non-regression need `change_executor apply` (graph DDL + qwen CQ-gate) → a
  separate, heat-checked, sandbox-only step. Per-detector gap-closure is multi-step (Signal B's
  orphan lives in `extraction_claims`; adding a relationship TYPE does not retroactively remove
  the orphan — closure needs re-extraction).
- **D-RM5 Heat 0 invariant for the core.** `ollama ps` clean before/after; the gate asserts it.

## 4. Findings (the substance)
- **Neither co-signal alone suffices — proven live on real GOLD evidence** (AFSALA Bancorp ↔
  Amsterdam Federal, a real Signal-B orphan pair from a real Agency Agreement):
  - grounded + well-formed (`create relationship 'affiliated_with'`) → **accept**.
  - grounded but MALFORMED (the `create edge …` baseline) → **reject on well-formedness**
    (groundedness still passes).
  - well-formed but UNGROUNDED (`competes_with` + invented "Albany mortgage market" rationale) →
    **reject on groundedness** (scorer flagged `competes_with`, `Albany`; well-formedness passes).
  - thin-evidence abstention → **accept** (credited).
  The two failure modes are caught by DIFFERENT co-signals — a single headline score would miss one.
- **Groundedness is RELATIVE + BOUNDED by upstream** (the A2 retrieval-bounds-regeneration lesson,
  recurring): the SAME proposal+rationale is grounded against rich evidence, ungrounded against
  empty evidence. A proposal is only as good as the signal evidence + source chunks the harness feeds.
- **The deterministic baseline is partially broken for relationship signals:** `signal_mapping.py`
  emits non-well-formed KGCL (`create edge … between …`) that the executor's own parser rejects —
  surfaced by proof 2b and confirmed by the well-formedness co-signal.

## 5. Validation evidence (why we trust the harness)
- **Golden gate 10/10** (`remediation_golden_gate.py`): grounded+well-formed accept; malformed-
  baseline rejected on well-formedness; ungrounded rejected on groundedness; the two-failure-mode
  discrimination; abstention credited; placeholder flagged; closure+non-regression deferred;
  groundedness context-relative; heat-0 (initial+final).
- Proved live by hand BEFORE generalizing: the real GOLD AFSALA case, four candidates,
  `change_executor parse` + groundedness judged by hand, then formalized in the scorer.

## 6. Build status (2026-06-22 — built + gate-green)
- `scripts/remediation_score.py` — four-co-signal scorer (groundedness via faithfulness on the
  rationale; well-formedness via `change_executor parse`; closure/non-regression stubbed/deferred).
- `scripts/remediation_golden_gate.py` — regression anchor + AUDIT.
- `grace-gap-remediation-harness/SKILL.md` — north star + science + flow.

## 7. In-tree candidates
- **signal_mapping malformed KGCL — FIXED (D534, 2026-06-22).** The harness's well-formedness
  co-signal surfaced that `signal_mapping.py` emitted non-grammar KGCL for **5 of 8 branches**
  (B/F-rel `create edge … between …`, C `change property … on …`, E `change domain of … from …`,
  F-prop `create property … on …`) → `proposal_generator` persisted proposals `change_executor`
  could never apply. Fixed to the canonical `kgcl_generator.py` forms with quoted names; E emits
  domain + range as two commands. Capture-the-why in the module docstring; CI guard
  `tests/ontology/test_signal_mapping.py::test_all_templates_parse`. Ratified D534.
- **`proposal_generator` NL-summary heat leak (H1):** `generate_evidence_summary` →
  `get_provider()` runs unconditionally on `proposal_generator run`. Gate behind an explicit flag.

## 8. Deferred / follow-on (do not lose)
- **Co-signals 3 (gap-closure) + 4 (non-regression) — BUILT 2026-06-22 (`apply_probe.py`).**
  Co-signal 3 = closure-readiness (HEAT-FREE): `change_executor._apply_change_to_schema` mutate
  + assert the missing element now exists in the schema. Co-signal 4 = the real CQ non-regression
  gate (`run_non_regression_gate`) on **qwen2.5:7b** (heat-guarded; 70B forbidden & never
  reachable). Fixture via `seed_gaps.py --cq-fixture` (active version + ACCEPTED CQs). Regression
  anchor `apply_golden_gate.py` **8/8** — the campaign's first deliberately non-heat-0 gate, with
  a HEAT-BOUNDED invariant (qwen allowed; llama3.3/70b/gpt-oss forbidden). Architect-approved v1 =
  closure-READINESS (not a full re-detect loop). **Still deferred:** (a) the full closure loop
  (apply → re-extract → signal clears — the orphan lives in `extraction_claims`, not the schema);
  (b) a richer baseline CQ fixture so co-signal 4 shows green pass/fail DISCRIMINATION (the minimal
  Party/Agreement schema yields pass_rate 0, so v1 asserts the gate machinery + heat bound, not a
  specific pass_rate).
- **Compare Claude vs the deterministic baseline at scale** — run both proposers over a batch of
  fired signals and tabulate the 4-co-signal deltas (groundedness + well-formedness uplift).
- **Wire the loop:** signal (grace-signal-probe) → Claude proposal (this harness) → sandbox apply
  → re-detect (signal stops firing) → calibration (B1) trust-score update.

## 9. Acceptance swarm (2026-06-22 — 3 cold agents, log-only)
A cold agent invented a fresh aviation-domain remediation case and confirmed the scorer
discriminates grounded / ungrounded / malformed / abstain correctly on novel inputs (each failure
mode caught by the right co-signal); the apply agent confirmed heat stays bounded to qwen2.5:7b
(70B never loaded). Friction found + FIXED:
- **F-RM1 (real usability, FIXED):** writing the COINED relationship/class name as a token in the
  grounding rationale (e.g. `affiliated_with`) made the groundedness scorer flag it as ungrounded
  → false REJECT (contradicting D-RM2). The proposed element is new by definition (not yet in the
  evidence), so `remediation_score.score()` now grounds the parsed `target_name` BY CONSTRUCTION
  (appends it to the fact set). Does NOT weaken ungrounded-detection — other invented entities still
  flag. Guard: `remediation_golden_gate.py` GATE-11 (named-token rationale must ACCEPT).
- **F-AP1 (efficiency, FIXED):** `apply_probe.py` co-signal 4 ran the qwen CQ-gate even on an
  unparseable KGCL (needless heat). Now short-circuits — a malformed proposal fails well-formedness,
  so co-signal 4 returns "skipped (unparseable)". Guard: `apply_golden_gate.py` GATE-9.
- **F-AP2 (cosmetic, left):** the heat-note is an instantaneous `ollama ps`, so back-to-back runs
  can read inconsistently (resident-qwen vs unloaded) even though no heat-free path loads a model.
