# grace-ingestion-harness — design & decision record (C1, 2026-06-23)

## Mission
Roadmap **C1** — the **communication / email ingestion** module (`src/ingestion/`,
`src/ingestion/communications/`, `src/extraction/extraction_bridge.py`). First
Claude-path live run, for a **organization** client (financial / legal / **email**).
Three linked goals:
1. Test the deterministic email pipeline LIVE on a known synthetic corpus — prove
   per-stage fidelity (recall + precision + substrate-honesty) against a manifest.
2. Run **Claude AS the LLM component** at each LLM-gated stage (T4 triage,
   extraction-from-email, voice synthesis) — score on co-signals.
3. Compare; wire real Claude-exposed / live-exposed gaps in-tree under full discipline.

North star: turn an organization's EMAIL into trustworthy, auditable graph facts —
without crying wolf and without leaking privilege. Headline properties (the fidelity
analogues, one modality over): triage fidelity, thread fidelity, extraction
groundedness, corroboration fidelity (no false promotion), **sensitivity fidelity
(privileged never leaks untagged)**, voice fidelity, provenance/audit
(`evidence_origin='communication'`).

## The missing-data solution (same as A3/A4, one modality up)
There is no golden dataset of real organization email (confidential, not loaded).
**Solution: a Claude-authored SYNTHETIC GOLDEN EMAIL CORPUS + ground-truth MANIFEST**,
seeded into the `grace_test` SANDBOX, scored against the manifest, GOLD never touched.
Real RFC-5322 `.eml` files (proper From/To/Date/Message-ID/In-Reply-To/References) so
the ACTUAL adapters parse them — not a shortcut INSERT. Corpus: 11 organization emails
(`corpus/*.eml`) + `corpus/manifest.json`. Domain content lives only in fixtures; the
seeder/gate are domain-agnostic (discover/score against the manifest).

## The headline result: the email front door has NEVER worked end-to-end live
Despite a green pytest suite, **no adapter pull had ever persisted an event through this
pipeline** (GOLD's 38 `communication_events` were seeded directly). Running the real
pipeline against a known corpus surfaced **8 defects**, each masked by a different test
shortcut (mocked adapters, unit-only scoring, mocked arcade clients):

| # | Defect | Status |
|---|---|---|
| 1 | `run`/`cycle` CLI never imports the adapter packages → `KeyError: Unknown adapter type 'eml'` (registry empty). Masked by `test_pipeline.py` patching `get_adapter` with a mock. | **FIXED D536** |
| 2 | Every adapter stamps `self._source_id = uuid4()`; pipeline persisted it → FK violation on EVERY insert, silently caught + **mislabeled `duplicate_message_id`**, run reports success with 0 rows. | **FIXED D537** |
| 3 | Triage T2 + corroboration query `Person`/`Organization` vertex types **absent from the deployed legal ontology** (only `Legal_Entity`). Per the ratified D274/D430 contract — a deployment/ontology mismatch, not a triage bug. | DEFERRED (architect) |
| 4 | Thread supersession async bug: `error='Event loop is closed'` → `superseded_by` graph writes silently fail. | DEFERRED |
| 5 | `mail-parser-reply` declared+locked but not installed → `email_composer` import fails. | FIXED (pip install) |
| 6 | Corroboration `run` CLI is a **stub** — logs start→complete with no graph query/score/promote ("In a real run, this would…"). | DEFERRED |
| 7 | `ArcadeClient()` (no config) hardcodes `database="grace"` and ignores `ARCADE_DATABASE`; triage Tier 2 (`pipeline.py:206`) uses it → always queries GOLD graph. Sandbox-isolation hazard + why a grace_test registry never matched. | DEFERRED |
| 8 | `EmlAdapter` omits `raw_headers` → `raw_headers_json` null → header-based T1 detectors (auto-reply/calendar/List-Unsubscribe) dead; only sender-pattern rules fire. | DEFERRED |

## Per-stage HEAT profile (verified per-file — the A3 lesson)
- **Heat-free:** thread_reconstructor, supersession, sensitivity_tagger, email_composer,
  bootstrap_pipe, triage T1-T3, corroboration `run` CLI (LLM fallback exists in
  `classify_stance` but `use_llm_fallback=False` default and **no CLI flag enables it**).
- **Bounded (loads qwen2.5:7b, never 70B):** triage **T4** (`tier4_llm`), **extraction**
  (`ExtractionLLMClient`), **voice/tone** (profile_generator + feature/signature/recipient/
  redactor), retriage (re-runs T4).
The golden gate covers ONLY the heat-free + sandbox-safe stages → heat 0 throughout.

## Substrate / runnability matrix (the A3/A4 lesson)
| Stage | Runnable on this box | Why |
|---|---|---|
| Adapter parse | **YES** (post D536/D537) | real EmlAdapter; Postgres |
| Triage T1 (sender-pattern) | **YES** | noreply@/mailer-daemon@ rules; Postgres |
| Triage T1 (header) | NO (#8) | raw_headers not persisted |
| Triage T2 (sender-known) | NO (#3 + #7) | Person/Org absent + ArcadeClient→GOLD |
| Triage T3/T4 | NO (gated by T2) | nothing reaches them |
| Sensitivity | **YES** | Postgres-only, heat-free |
| Thread reconstruction | **YES** (Postgres part) | supersession no-ops (#4) |
| Extraction → graph | NO | bounded heat + blocked by T2 + needs sandbox graph |
| Corroboration | NO (#6) | run CLI is a stub |
| Voice | NO | needs ≥50 principal emails + bounded heat |

## Seeding approach
`scripts/seed_emails.py` — sandbox-guarded (refuses non-`_test`). Resets via TRUNCATE
(bypasses the append-only DELETE guard), seeds a `Person`/`Organization` registry into
the `grace_test` ArcadeDB db, INSERTs an `eml` source, and runs the REAL IngestionPipeline
pull (post-D536/D537) to parse the corpus. `--clean` resets + drops the registry.
Gotchas: `config_json` must embed the `source_type` discriminator; `communication_events`
and `ingestion_runs` are append-only (TRUNCATE, not DELETE).

## What was proven (live, by hand, then gated) — heat 0, GOLD untouched
`scripts/ingestion_golden_gate.py` — **10/10 GREEN**:
- **Parse fidelity** — all 11 `.eml` parse; In-Reply-To + References DAG arrays correct
  (`deal-003 → [deal-001, deal-002]`); bodies intact.
- **Triage T1** — newsletter + bounce filtered via sender pattern; unknown-sender T2
  precision (cold outreach correctly filtered).
- **Sensitivity — privileged HARD invariant** (recall 100% + precision): only the counsel
  email tagged `privileged`; no leak. `pii_dense` on the 6 figure/PII emails, not on noise;
  bar-form `|tag|`.
- **Thread** — deal thread 01←02←03 grouped at positions 0/1/2; the auto-reply (same
  subject, no refs) correctly standalone (header DAG, not subject).
- **Substrate honesty** — `--clean` → 0 events → stages quiet.
- **GOLD untouched** — `grace` Postgres `communication_events` 38→38; graph
  `Legal_Entity` 53 unchanged. Swarm-portable (skips GOLD check on isolated `_test`).
- **Claude-as-extractor (in-loop, heat-free)** — grounded entities/edges + provenance
  from the composed deal email (groundedness 1.0); deal-002 (Robert) corroborates the same
  $5M-Summit fact (corroboration ground truth = 2 distinct senders).

## Step-7 — the gate binds (a green gate proves nothing until shown to go RED)
Mutation test: blanking `privilege_phrases` in `config/sensitivity_rules.yaml` (hash-backed,
restored byte-identical) → **GATE-3 + GATE-4 flip RED** (privileged `actual=[]`), 8/10;
restore → 10/10 GREEN. The privileged hard-invariant gate flips on a real defect, and the
RIGHT gates flip. (Fuller mutation matrix + cold-agent swarm + Claude-component variance =
next-step stress layer.)

## The in-tree fixes (architect-approved: "both combined")
- **D536** — `pipeline.py` imports the adapter packages at the `get_adapter` chokepoint.
- **D537** — `pipeline._run_inner` reconciles `row_data["source_id"] = source_id` before
  INSERT; the `IntegrityError` path now only catches genuine duplicates.
Capture-the-why comments at both sites; guard tests
`tests/ingestion/test_d536_d537_adapter_wiring.py` (clean-interpreter registry test +
hermetic source_id-reconcile test). No migration / schema / GOLDEN_NAMES change.

## CI wiring
`tests/smoke/test_c1_harness_gates.py`: golden gate `@pytest.mark.smoke` (heat-free; needs
Postgres `grace_test` + ArcadeDB) + a Tier-1 manifest-contract unit test (default suite).
`c1-harness-gates` job in `.github/workflows/ci.yml`.

## Deferred work (architect decisions + bounded-heat layer)
- **#3** — make triage T2/corroboration entity-types configurable to target `Legal_Entity`
  (amends locked D430, new D-number) OR grow the deployed ontology with `Person`/`Organization`
  OR seed a registry as a deployment step. Architect chose "investigate first" — the contract
  is D274/D430.
- **#7** — route triage/thread/corroboration arcade access through `get_arcade_client()` so
  `ARCADE_DATABASE` is honored (sandbox isolation + config correctness).
- **#8** — `EmlAdapter` should capture `raw_headers` so header-based T1 detectors work.
- **#6** — implement the corroboration `run` flow (query `evidence_origin='communication'`
  entities → EntityCorroboration → score → promote).
- **#4** — fix the supersession event-loop lifecycle.
- **Bounded-heat apply-gate** — once #3/#7 unblock T2, an `apply_golden_gate`-style harness
  (qwen behind `requires_ollama`) for extraction-from-email groundedness + manifest entity
  recall + provenance, and the Claude-as-component co-signal scorer (groundedness +
  consistency-vs-deterministic + abstention + richness; multi-judge panel for variance).
- A bulk-principal fixture (≥50 emails) for the voice gate.
