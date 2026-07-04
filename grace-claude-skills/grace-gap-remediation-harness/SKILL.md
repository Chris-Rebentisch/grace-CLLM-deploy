---
name: grace-gap-remediation-harness
description: >
  Close the loop on GrACE self-monitoring (roadmap A3, goal 2): take a fired gap
  signal + its evidence, have CLAUDE (at the EDGE, in-loop) propose ONE concrete
  KGCL schema change to close the gap, and SCORE that proposal on FOUR co-signals —
  never one headline number. (1) GROUNDEDNESS: is the change supported by the signal
  evidence + source chunks + active ontology, or invented from priors? Computed as
  faithfulness(rationale, evidence), crediting abstention. (2) WELL-FORMEDNESS: does
  it parse via change_executor parse (deterministic, heat-free)? (3) GAP-CLOSURE and
  (4) NON-REGRESSION: deferred to a qwen-gated apply follow-on (the CQ-gate + graph
  DDL need a local model — qwen2.5:7b, NEVER the 70B). Claude is the only LLM and runs
  in-loop at the edge; the deterministic detect→propose chain (signal_pipeline →
  proposal_generator → change_executor) stays LLM-free for auditability + heat. It is
  a CLIENT: it reads OUTPUT (analytics_signals, source chunks) and validates via
  change_executor parse; it never wires Claude into the detectors or proposal_generator.
  Use after grace-signal-probe (A3) proves the detectors fire.
---

# grace-gap-remediation-harness

## ★ NORTH STAR
**Prove Claude can propose the FIX — grounded, not invented.** grace-signal-probe proved
GrACE notices its ontology is incomplete. This harness asks the next question: given a
fired signal + its evidence, can Claude propose a concrete schema change that is
SUPPORTED by the evidence (not priors), PARSES as valid KGCL, closes the gap, and breaks
nothing? The load-bearing property is **remediation groundedness** — the proposed change is
anchored in the signal's evidence + source chunks + current ontology, and thin evidence
yields an honest "insufficient evidence" abstention, not a confident invention.

## Role
You are the **remediation proposer + scorer, at the edge of the deterministic chain.** You
read a fired signal's OUTPUT (evidence, source chunks, active ontology), propose ONE KGCL
change with a grounding rationale, and score it on four co-signals. You are **not** inside
the detectors or `proposal_generator` (that loses determinism/auditability and adds heat) —
the deterministic pipeline + human/earned-autonomy gate decides; the deterministic KGCL
executor applies. You read OUTPUT and propose; you validate via `change_executor parse`.

## When this runs
- **Trigger:** `grace-signal-probe` (A3) has a fired signal, and you want to test whether the
  detect→propose→fix loop produces a grounded, well-formed schema change.
- **Composes with** `grace-signal-probe` (its fired signal + evidence are this harness's input)
  and reuses A2's `faithfulness_score` for the groundedness co-signal.
- **Domain-agnostic:** evidence (orphan pair, source text, ontology types) is read at runtime;
  nothing hardcoded into the scorer.

## The system under test (read OUTPUT, validate via parse)
- `signal_pipeline → analytics_signals` (the fired gap) — **input**.
- `proposal_generator` (Chunk 47) maps signal → KGCL via the static `signal_mapping.py`
  (deterministic BASELINE) + `evidence_bundle` → `schema_proposals`. **Note the heat leak:**
  `proposal_generator run` calls `generate_evidence_summary` → `get_provider()`. The harness
  does NOT depend on it; Claude proposes at the edge instead.
- `change_executor parse "<kgcl>"` — pure KGCL validator, **no DB/graph/LLM (heat-free)** — the
  well-formedness co-signal. `apply` (parse→mutate→diff→CQ-gate→ratify→DDL) is **mutating +
  loads an LLM in the CQ-gate** → the qwen-gated follow-on, not the heat-free core.

## The heat reality (verify, don't assume)
- Claude proposes **in-loop** — no `get_provider()`, no local model, no API key for the
  propose step. `change_executor parse` is pure. The heat-free core is co-signals 1 & 2.
- The ONLY local-model need is the CQ non-regression gate inside `apply` (co-signal 4) →
  **qwen2.5:7b** in the gated follow-on. **Never the 70B** (won't fit / can't be pulled on a
  28GB box; `/api/generate` 404s on the absent model — verified — so even an accidental call
  degrades, but don't rely on the accident: the config is swapped to qwen2.5:7b).

## The science (four co-signals, never one number — the A2 lesson)
1. **Groundedness** = faithfulness(rationale, evidence). Every salient entity/number the
   rationale asserts must resolve in the evidence; **abstention is credited as faithful**
   (thin evidence → a faithful proposal is a refusal). Groundedness is RELATIVE to the
   evidence and BOUNDED by the upstream signal — only as good as what the harness feeds.
2. **Well-formedness** = `change_executor parse` (deterministic). Also flags mechanical
   PLACEHOLDER names (RelatedType/UnknownType) the baseline emits.
3. **Gap-closure** = [BUILT — `apply_probe.py`, heat-free v1] closure-READINESS: apply the real
   `change_executor` mutate to the active schema and assert the missing element now exists
   (re-extraction WOULD realize it). Full re-detect loop deferred (B's orphan lives in
   `extraction_claims`, not the schema).
4. **Non-regression** = [BUILT — `apply_probe.py`, qwen-gated] the real CQ non-regression gate on
   **qwen2.5:7b** (heat-guarded; 70B forbidden). v1 proves the gate machinery + heat bound; a
   green pass/fail needs a richer baseline CQ fixture (follow-on).

**Why two co-signals are not enough (proven live on real GOLD):** a grounded-but-MALFORMED
proposal (the `create edge` baseline) passes groundedness, fails well-formedness; a well-formed-
but-UNGROUNDED proposal (`competes_with` when the source says the parties are affiliates) passes
well-formedness, fails groundedness. Neither co-signal alone catches both.

## What this session proved (2026-06-22)
- On a real GOLD Signal-B orphan pair (AFSALA Bancorp ↔ Amsterdam Federal, from a real Agency
  Agreement), a grounded Claude proposal `create relationship 'affiliated_with'` scored ACCEPT
  (groundedness faithful, well-formed); the deterministic baseline `create edge …` REJECTED on
  well-formedness; `competes_with` + invented rationale REJECTED on groundedness (flagged the
  invented tokens); thin-evidence abstention ACCEPTED. **Golden gate 10/10, heat 0.**
- **In-tree candidate logged for the architect:** `signal_mapping.py` emits non-well-formed KGCL
  for B/F relationship signals (`create edge … between …` — grammar wants `create relationship
  '<Name>'`). The mechanical baseline can't pass its own executor's parser for relationship gaps.

## Session flow (step by step)
1. **Preflight** — `ollama ps` (heat 0); confirm config model is qwen2.5:7b (not 70B).
2. **Take a fired signal** (from grace-signal-probe) — pull its evidence + the real source
   chunk text (read-only from `processed_documents`) + active ontology types into an evidence file.
3. **Propose (in-loop)** — as Claude, read the evidence and emit ONE KGCL change + a one-paragraph
   grounding rationale. Prefer abstention over invention when evidence is thin.
4. **Score** — `python3 remediation_score.py --kgcl "<…>" --rationale-file r.txt --evidence-file ev.txt`.
   Read all four co-signals; never collapse to one number.
5. **Regression** — `python3 remediation_golden_gate.py` (10 gates + AUDIT). Heat-0 invariant.
6. **Log** to `runs/signal-pipeline-probe/session-N-*.md`.

## Hard constraints
- **Deterministic core, reasoning at the edge.** Never put Claude inside the detectors or
  `proposal_generator`. Claude reads OUTPUT and proposes; the deterministic chain + gate decides;
  the deterministic KGCL executor applies.
- **Parse-only core; apply is gated.** Default to propose + parse + groundedness/well-formedness.
  Gap-closure + non-regression need `apply` (graph DDL + qwen CQ-gate) — a separate, heat-checked,
  sandbox-only follow-on.
- **In-tree changes need the full discipline.** Logged candidates (the malformed baseline) require
  a new D-number + capture-the-why (D356) + a CI-guard carve-out — STOP for architect approval.
- **Heat 0 is an invariant.** `ollama ps` clean before and after; the gate asserts it.

## Tools (under ~/grace-claude-skills/scripts/)
- `remediation_score.py` — heat-free co-signals 1 & 2 (groundedness via faithfulness on the
  rationale + well-formedness via change_executor parse). Co-signals 3 & 4 delegated to apply_probe.
- `remediation_golden_gate.py` — regression anchor for 1 & 2 (two-failure-mode discrimination,
  abstention, placeholder flag, context-relativity, heat-0) + AUDIT.
- `apply_probe.py` — qwen-gated co-signals 3 (closure-readiness, heat-free) + 4 (CQ non-regression
  gate on qwen2.5:7b, heat-guarded). `--closure-only` for the heat-free half.
- `apply_golden_gate.py` — regression anchor for 3 & 4 (closure-readiness, heat guard rejects the
  70B, CQ-gate runs on qwen, HEAT-BOUNDED) + AUDIT. First deliberately non-heat-0 gate.
- `seed_gaps.py --cq-fixture` — active version + ACCEPTED CQs for the co-signal-4 gate.
- Reuses `faithfulness_score.py` (A2), `change_executor` parse + mutate + CQ-gate (heat-free / qwen).
- Design + decision record: `references/remediation-harness-design.md`.
