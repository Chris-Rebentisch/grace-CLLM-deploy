# GrACE — Graph as Auditable Context Engine (Claude-as-the-LLM deployment)

**Operational ground truth for running GrACE at a client site.** This is the
Claude-as-the-LLM build: Claude (via the skills in `grace-claude-skills/` and the
Anthropic provider) does the knowledge-graph reasoning; a small local model
(Ollama) handles embeddings and a few bounded pipeline steps.

> **Installing on a fresh machine? START HERE → `INSTALL.md`** (from-scratch
> prerequisite install for macOS and Windows), then `docs/GrACE-Onboarding-Setup-Manual.md`.

- **Install from scratch (agent: start here)** — `INSTALL.md`
- **What GrACE is** — `docs/GrACE-Product.md`
- **Set it up the first time** — `docs/GrACE-Onboarding-Setup-Manual.md`
- **Use it day to day** — `GrACE-User-Manual.md`
- **Operator runbooks** — `docs/runbooks/`

> The local-LLM-only path (running entirely on a local model, no cloud) is
> supported by the codebase but is a **future** deployment option. Today this
> install uses Claude (Anthropic) as the primary LLM.

## Tech stack

- **Language:** Python 3.14, managed with **uv** (`pyproject.toml` + `uv.lock`,
  interpreter pinned in `.python-version`).
- **API:** FastAPI + Uvicorn on `localhost:8000`.
- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind v4 + shadcn/ui in
  `frontend/`, on `localhost:3000`.
- **Relational DB:** PostgreSQL 17 (`grace`), SQLAlchemy 2.0, Alembic migrations.
- **Graph DB:** ArcadeDB ≥26.5.1 via Docker on `localhost:2480` (OpenCypher for
  DML/DQL, SQL for DDL; native vector index for ANN entity resolution). Data lives
  in the named Docker volume `grace_arcadedb_data` (portable default; bind-mount a
  host path via `docker-compose.override.yml` if you want host-visible files).
  `ArcadeClient()` resolves its target database from `ARCADE_DATABASE` in `.env`
  (default `grace`) — harnesses point it at `grace_test` for sandbox isolation.
- **LLM provider abstraction** (`src/shared/llm_provider.py`): selectable in
  `config/discovery.yaml` under `llm`. This deployment defaults to
  **AnthropicProvider** (`claude-haiku-4-5-20251001`). Key in `.env` as
  `LLM_API_KEY`. OllamaProvider and OpenAI-compatible providers are also
  available. If `discovery.yaml` is missing/unparseable the code falls back to
  Ollama defaults and logs `discovery_yaml_missing_falling_back_to_ollama` at
  error level — treat that log as a setup failure. Vision defaults follow the
  active provider (`llm.vision` block in `discovery.yaml`; Anthropic → the Claude
  model, Ollama → `qwen2.5-vl:32b`), and UI config saves preserve the `llm.vision`
  and `llm.num_ctx` blocks.
- **Local model server:** **Ollama** on `localhost:11434` — required even with the
  Anthropic provider, for embeddings (`nomic-embed-text`, 768-dim) and a small
  model (`qwen2.5:7b`) used by specific bounded pipeline stages. The API probes
  Ollama at startup and logs `embeddings_backend_unreachable` (error level) when
  it's down — retrieval and entity resolution need it. Embedding call timeout is
  overridable via `GRACE_EMBED_TIMEOUT_SECONDS` (default 120).
- **Document processing:** Docling (PDF/DOCX/XLSX/PPTX + image OCR). Requires
  `KMP_DUPLICATE_LIB_OK=TRUE` and `OMP_NUM_THREADS=1` on macOS (in `.env`).
- **Observability:** OpenTelemetry + Prometheus + Grafana + structlog, deployed via
  `docker/docker-compose.observability.yml`.
- **MCP server:** `src/mcp_server/` exposes GrACE tools over stdio to Claude Desktop
  / Claude Code (config sample: `scripts/claude_desktop_config.example.json`).
- **Schema:** Pydantic v2 is the source of truth → JSON Schema (generated, never
  hand-written) → optional Turtle/RDF export.

## Claude-as-the-LLM skills

`grace-claude-skills/` holds the operator skills and live test harnesses that run
**Claude** as the knowledge-graph LLM (in Claude Code / Claude Desktop). Install a
skill by copying its folder to `~/.claude/skills/<name>/`. Highlights:

- `grace-cq-authoring`, `grace-property-detailing`, `grace-ontology-proposal`,
  `grace-graph-extraction`, `grace-corpus-export`, `grace-auto-accept`,
  `grace-intent-elicitation` — the discovery → ontology → extraction workflow.
- `grace-review-protocol`, `grace-testing-protocol` — review and verification.
- Live harnesses: `grace-retrieval-probe`, `grace-regeneration-probe`,
  `grace-signal-probe`, `grace-correlation-probe`, `grace-gap-remediation-harness`,
  `grace-ingestion-harness` — end-to-end checks against a sandbox database.

See `grace-claude-skills/README.md` and `grace-claude-skills/module-test-roadmap.md`.

## How to run things

- **Install deps:** `uv sync --extra dev` then `source .venv/bin/activate`
- **Start ArcadeDB:** `docker compose -f docker/docker-compose.arcade.yml up -d`
- **Apply migrations:** `alembic upgrade head` (one-time `createdb grace` first)
- **Start API:** `uvicorn src.api.main:app --reload --port 8000`
- **Start frontend:** `cd frontend && npm install && npm run dev`
- **Observability stack:** `docker compose -f docker/docker-compose.observability.yml up -d`
- **Run tests:** `python -m pytest tests/ -q` (auto-targets an isolated `grace_test`
  database — never the live `grace` data; one-time setup:
  `createdb grace_test && DATABASE_URL=…/grace_test alembic upgrade head`; if the
  `grace_readonly` role exists on your cluster, also grant it SELECT inside
  `grace_test` — see `docs/runbooks/pytest-db-safety.md`). Service-dependent tests
  auto-skip when Ollama / ArcadeDB / nltk / OCR are absent. Known-failure skips
  live in the `docs/test-suite-allowlist.md` registry (≤5 entries, parsed by
  `tests/conftest.py`).
- **MCP server (manual):** `python -m src.mcp_server`
- **Set the Anthropic key:** `bash scripts/set-api-key.sh` (or edit `.env`)

Full first-run sequence: `docs/GrACE-Onboarding-Setup-Manual.md`.

## User-reported issues (`.issues/` tracker)

Local-first issue log: one markdown file per issue under `.issues/` (YAML
frontmatter for state), `BUGS.md` at the root is the GENERATED index (never
hand-edit). CLI: `python3 scripts/issue.py new|list|show|close|push|index` —
`push` optionally mirrors one issue to GitHub Issues via `gh` (one-way, per
issue, deliberate). Full workflow: `.issues/README.md`. Agents: when the user
reports a bug/tweak, file it with `new` (don't just fix silently); check
`list` for open items before proposing housekeeping; cite `fixes ISS-NNNN`
in resolving commits.

## Critical rules

- **Secrets never committed.** `LLM_API_KEY` and all credentials live only in local
  `.env` (gitignored). Never paste real secret values into chat, logs, code, docs,
  or commits — refer to variable names only.
- **The human approves the ontology.** The LLM proposes schema and extractions; a
  human reviews and approves before anything is trusted. High-risk schema changes
  (hierarchy restructuring, type deprecation, domain/range changes) are always
  human-reviewed.
- **Pydantic is the source of truth.** JSON Schema is generated via
  `model_json_schema()` — never hand-written.
- **Provenance is mandatory.** Every extracted fact carries temporal validity,
  confidence, and a source link. Confidence reaches the UI as certainty *bands*
  (High / Medium / Low / Insufficient), never raw numbers.
- **Test-DB isolation.** `pytest` auto-redirects to a `grace_test` sibling database;
  never point the test suite at the live `grace` database.
- **Long-running pipelines run out-of-process.** Signal/correlation/eval/decay and
  other batch jobs run via their CLIs (scheduled with cron / launchd), not inside
  the API process. Sample schedules: `scripts/launchd/`.
- **Dev credentials are dev-only.** ArcadeDB (`root`/`gracedev`) and Grafana
  (`admin`/`gracedev`) defaults must be rotated before any non-localhost exposure;
  the API logs `arcade_default_credentials_in_use` at startup while the default
  password is active.
- **Email triage entity types are configurable.** Tier-2 sender lookup and the
  corroboration scorer read their graph vertex types from
  `config/triage_rules.yaml` (`tier2.entity_types`) and
  `config/corroboration_config.yaml` (`sender_entity_types`); the default
  `["Person", "Organization", "Legal_Entity"]` covers ontologies that model
  people/orgs as `Legal_Entity` only.
- **Don't guess.** If a configuration or architectural choice is unclear, stop and
  ask rather than picking one.

## Directory layout

```
src/                  Backend modules (discovery, ontology, extraction, graph,
                      retrieval, regeneration, analytics, ingestion, mcp_server,
                      permissions, federation, api, shared, …)
frontend/             Next.js web UI
alembic/              Database migrations
config/               Module configuration (YAML/JSON)
seeds/                Reference ontologies (FIBO, LKIF, PROV-O, Schema.org)
docker/               Docker Compose (ArcadeDB, observability)
grace-claude-skills/  Claude-as-the-LLM skills + live test harnesses
scripts/              Setup, scheduling, and operator utility scripts
tests/                Test suite (mirrors src/)
docs/                 Product overview, onboarding manual, operator runbooks
```
