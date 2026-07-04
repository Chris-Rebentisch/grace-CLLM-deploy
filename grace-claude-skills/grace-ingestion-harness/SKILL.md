---
name: grace-ingestion-harness
description: >
  Prove a built GrACE system can turn an organization's EMAIL into trustworthy,
  auditable graph facts — the whole second input modality (roadmap C1). Drives the
  deterministic email pipeline live via its sanctioned CLIs (python -m src.ingestion
  run|triage, src.ingestion.communications.sensitivity_tagger|thread_reconstructor —
  D246, out-of-process) against a Claude-authored SYNTHETIC GOLDEN EMAIL CORPUS (real
  RFC-5322 .eml + a ground-truth manifest) seeded into the grace_test SANDBOX (never the
  live grace GOLD corpus). Measures per-stage FIDELITY against the manifest: adapter
  parse (headers/In-Reply-To/References DAG/body), triage (signal passes, noise filtered),
  sensitivity (privileged NEVER leaks untagged — a HARD invariant — + precision), thread
  reconstruction (reply chains group, supersession), and is substrate-honest (empty ->
  quiet) and GOLD-untouched. The heat-free stages (parse, T1-T3 triage, sensitivity,
  thread, email_composer, corroboration math) load NO model — only the LLM-gated stages
  (T4 triage, extraction, voice synthesis) are BOUNDED heat (qwen2.5:7b, never 70B). It
  ALSO runs Claude AS the LLM component (T4 decision, extraction-from-email, voice) and
  scores groundedness + consistency-vs-deterministic + abstention. It is a CLIENT/PROBE:
  it drives the CLIs, reads outputs (communication_events, graph vertices), and never
  imports a pipeline module into a route/scheduler (D246) or wires Claude into the core.
  This harness's first run found the email front door had NEVER worked end-to-end live
  (8 defects behind a green suite); 2 were fixed (D536/D537), the rest are documented.
---

# grace-ingestion-harness

> **Operator note (deployment):** No sample email corpus ships with this repo (no
> demo data). To run the harness, author your own small corpus under
> `grace-claude-skills/grace-ingestion-harness/corpus/` — real RFC-5322 `*.eml`
> files plus a `corpus/manifest.json` ground-truth file following the contract in
> `references/ingestion-harness-design.md`. Seed it into the `grace_test` sandbox
> only; never run against the live `grace` database.

## ★ NORTH STAR
**Can GrACE turn an organization's EMAIL into trustworthy, auditable graph facts —
without crying wolf and without leaking privilege?** The fidelity analogues, one
modality over: triage fidelity, thread fidelity, extraction groundedness, corroboration
fidelity (multi-source promotes; single-source/echo does not), **sensitivity fidelity
(privileged never leaks untagged)**, voice fidelity, and provenance/audit (every
email-derived fact carries `evidence_origin='communication'`).

## Role
You are the **substrate author + fidelity auditor, outside the pipeline loop.** You
author a KNOWN organization email corpus + a ground-truth manifest, seed it into the
`grace_test` sandbox (real `.eml` parsed by the real adapters), drive the sanctioned
CLIs, and score each stage's output against the manifest. You ALSO reason AS the LLM
component (T4 triage, extraction, voice) in-loop and score your own output. You are
**not** part of the pipeline: you drive the CLIs (D246) and read outputs; you never
import a pipeline module into a route/scheduler, and you never write to the GOLD corpus.

## When this runs
- **Trigger:** the consume + self-monitoring stack (A1–A4) is trusted and you want to
  prove the EMAIL input modality — or to regression-check after an ingestion change.
- **First-run reality (2026-06-23):** the email front door had never run end-to-end.
  Read `references/ingestion-harness-design.md` FIRST — it records the 8 defects, the 2
  fixes (D536/D537), and exactly which stages are runnable on this box.

## Hard constraints (inherited from A1–A4)
- **HEAT:** `ollama ps` before AND after every step. The golden gate is heat-free
  (parse/T1-T3/sensitivity/thread). T4/extraction/voice are BOUNDED heat (qwen2.5:7b,
  NEVER llama3.3:70b) — gate them behind `requires_ollama`, like the A3 apply-gate.
- **SANDBOX ONLY:** seed `grace_test` (Postgres `_test` sibling + `grace_test` ArcadeDB
  db). The seeder refuses any db not ending `_test`. Assert GOLD `grace` unchanged.
- **CLIENT/PROBE:** drive the CLIs; never import `src.ingestion.*` pipeline modules into
  a route/scheduler (D246 guards exist) and never wire Claude into the deterministic core.
- **In-tree changes need full discipline:** ratified D-number + capture-the-why (D356) +
  guard test + heat validation + re-run prior gates (A1–A4). STOP for architect approval.

## How to run
Env selectors (read this — they are not obvious):
- **`DATABASE_URL`** picks the SANDBOX. The seeder/gate take it as-is if it ends `_test`,
  else append `_test`; the ArcadeDB graph db name is derived from it. For a swarm slice
  use a per-agent DB, e.g. `DATABASE_URL=postgresql+psycopg2://<user>@localhost:5432/grace_a_test`.
- **`GRACE_GOLD_URL`** is the explicit GOLD reference for the GOLD-untouched gate (GATE-7).
  On an isolated `*_test` DB whose `<name>` sibling does not exist (e.g. `grace_a_test`→`grace_a`),
  GATE-7 cannot find GOLD and PASSES with detail "NOT VERIFIED" — set `GRACE_GOLD_URL` to the
  real GOLD (`...grace`) to actually enforce it. CI and the pytest smoke set it.
- **`GRACE_ROOT`** points at the grace repo (default `~/grace`); needed for CI portability.

```
# seed the sandbox (real adapter parse + Person/Org registry); --clean to reset
DATABASE_URL=postgresql+psycopg2://<user>@localhost:5432/grace_test \
  python3 ~/grace-claude-skills/scripts/seed_emails.py --json
# the golden gate (heat-free; 10/10 GREEN on healthy code; GOLD-untouched ENFORCED)
GRACE_ROOT=~/grace \
  DATABASE_URL=postgresql+psycopg2://<user>@localhost:5432/grace_test \
  GRACE_GOLD_URL=postgresql+psycopg2://<user>@localhost:5432/grace \
  python3 ~/grace-claude-skills/scripts/ingestion_golden_gate.py
# as a pytest smoke (excluded from default suite; conftest redirects DATABASE_URL to _test)
python -m pytest tests/smoke/test_c1_harness_gates.py -m smoke -v
```

## The golden gate (what MUST hold on healthy code)
Scored against `corpus/manifest.json` (the linchpin). 10 gates, heat-free, GOLD-untouched:
parse fidelity (+ References DAG), T1 sender-pattern noise, **privileged recall=100% +
precision (HARD)**, sensitivity tag-set match, thread DAG grouping, substrate honesty,
GOLD Postgres untouched, heat clean pre/post. Stages blocked by deferred findings (T2
signal-pass #3/#7, header-T1 #8, extraction bounded-heat, corroboration #6, voice) are
reported in an AUDIT block, not asserted as green gates (the A4 Prometheus-no-op pattern).

## Mutation discipline (Step 7 — the gate must be shown to go RED)
A green gate proves nothing until a deliberate defect flips the RIGHT gate. Proven:
blanking `privilege_phrases` → GATE-3/GATE-4 RED (privileged `actual=[]`), restore
byte-identical → GREEN. Always back up (hash), mutate, run, restore in `finally`, verify.

## Co-signal rubric for the Claude-component layer (bounded-heat / in-loop)
Never one headline number. Per stage: groundedness (every extracted fact resolves in the
email text; abstention credited), consistency-vs-deterministic (agree or grounded-richer
→ flag for review, not auto-accept; require ≥2 signals for "richer"), and a multi-judge
panel for variance (legitimate ambiguity vs reasoner error).

## Reusable pieces
`scripts/_common.py`, `scripts/seed_emails.py`, `scripts/ingestion_golden_gate.py`,
`scripts/cypher_exec.py` (graph ground truth), `scripts/faithfulness_score.py` (adapt for
extraction-from-email + voice grounding). Corpus + manifest under `corpus/`.

## Outputs
Log runs to `~/grace-claude-skills/runs/ingestion-probe/session-N-*.md`. In-tree
enhancement ideas → architect (next-free D-pointer: verify in `docs/GrACE-Decisions.md`).
