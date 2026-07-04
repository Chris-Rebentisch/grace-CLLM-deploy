# Operator First-Chunk Walkthrough

**D494 — Chunk 75b**

A 5-day scripted walkthrough for new operators. All commands are copy-pasteable and verified against `CLAUDE.md` "How to Run Things" as of 2026-05-28.

---

## Day 1 — Environment Setup

### Prerequisites

- macOS with Homebrew
- Python 3.14
- Node.js (LTS)
- Docker (via Colima)

### Step 1: Clone and enter the repository

```bash
cd ~/grace
```

### Step 2: Install Python dependencies

```bash
pip install --break-system-packages pydantic pydantic-settings sqlalchemy psycopg2-binary alembic \
  fastapi uvicorn httpx structlog rdflib deepdiff jsonpatch instructor pysbd python-dateutil \
  sentence-transformers bm25s hdbscan umap-learn scikit-learn python-igraph pydantic-yaml \
  pymannkendall opentelemetry-api opentelemetry-sdk opentelemetry-exporter-prometheus \
  prometheus-client hypothesis deepeval docling 'mcp[cli]>=1.27.0,<2.0'
```

### Step 3: Install and start PostgreSQL 17

```bash
brew install postgresql@17
brew services start postgresql@17
/opt/homebrew/opt/postgresql@17/bin/createdb grace
```

### Step 4: Install and start Ollama

```bash
brew install ollama
ollama serve &
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

Verify:

```bash
ollama list
curl http://localhost:11434/api/generate -d '{"model":"qwen2.5:7b","prompt":"test"}'
```

### Step 5: Start Docker (Colima) and ArcadeDB

```bash
colima start --cpu 2 --memory 4 --disk 20
docker compose -f docker/docker-compose.arcade.yml up -d
```

Verify ArcadeDB:

```bash
curl -s http://localhost:2480/api/v1/server -u root:gracedev
```

### Step 6: Install frontend dependencies

```bash
cd ~/grace/frontend && npm install
npx next telemetry disable
cd ~/grace
```

### Step 7: Configure environment

```bash
cp .env.example .env
# Edit .env as needed — see CLAUDE.md "Environment" section
```

### Step 8: Apply database migrations

```bash
cd ~/grace && alembic upgrade head
```

---

## Day 2 — First Launch

### Step 1: Restore GOLD database dump

The GOLD dump loader (chunk 75a, D485) provides a pre-populated database for development:

```bash
bash scripts/setup/load-gold-dump.sh <path-to-dump>
```

If no GOLD dump is available, follow `docs/runbooks/first-boot.md` for manual first-boot setup instead.

### Step 2: Start the backend

```bash
cd ~/grace && uvicorn src.api.main:app --reload --port 8000
```

Verify:

```bash
curl http://localhost:8000/metrics
curl http://localhost:8000/api/graph/info
```

### Step 3: Start the frontend

In a new terminal:

```bash
cd ~/grace/frontend && npm run dev
```

Verify: open `http://localhost:3000` in a browser.

### Step 4: Verify observability (optional)

```bash
docker compose -f docker/docker-compose.observability.yml up -d
```

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001` (admin/gracedev)

---

## Day 3 — Pipeline Orientation

### Step 1: Pipeline status

```bash
cd ~/grace && python3 scripts/pipeline/run_pipeline.py status --json
```

### Step 2: Understand the stage flow

The pipeline lifecycle is: **research → outline → spec → prompt → code → audit → handoff**.

Each stage has a slash command (e.g., `/research-grace-chunk`, `/outline-author`, `/spec-author`, `/prompt-author`, `/code-auditor`, `/handoff-author`).

### Step 3: Explore pipeline configuration

```bash
cat config/pipeline_automation.json | python3 -m json.tool | head -50
```

Key fields: `enabled`, `stages`, `providers` (mock vs live), `breakpoints`.

### Step 4: Run a mock pipeline (dry run)

```bash
cd ~/grace && python3 scripts/pipeline/run_pipeline.py run --chunk 99 --provider mock
```

This runs the full pipeline lifecycle with a mock provider — no LLM calls, no real artifacts.

### Step 5: Examine canonical docs

```bash
cat docs/GrACE-Backlog.md | head -60
cat docs/GrACE-Decisions.md | head -40
cat docs/GrACE-Doc-Map.md | head -40
```

---

## Day 4 — First Real Chunk

### Step 1: Identify the next chunk

```bash
grep -n "planned\|researching\|building" docs/GrACE-Backlog.md | head -5
```

### Step 2: Run research

Either use the slash command `/research-grace-chunk` in Claude Code, or run via the orchestrator:

```bash
cd ~/grace && python3 scripts/pipeline/run_pipeline.py run --chunk <N> --provider live --from-stage research
```

### Step 3: Run outline → spec → prompt

Each stage produces a versioned artifact in `docs/`:

```bash
# Check what was produced
ls docs/chunk-<N>-*
```

The orchestrator manages the review loop automatically. Each stage iterates until the artifact is approved.

### Step 4: Run the code stage

```bash
cd ~/grace && python3 scripts/pipeline/run_pipeline.py run --chunk <N> --provider live --from-stage code
```

The code-stage preflight runs automatically:

```bash
bash scripts/pipeline/preflight-code.sh <N>
```

---

## Day 5 — Audit, Handoff, and Close

### Step 1: Run the code auditor

Use `/code-auditor` in Claude Code, or run via the orchestrator.

The auditor produces `docs/chunk-<N>-audit-v{X}.md` with a verdict:
- **PASS** — handoff can proceed
- **PASS_WITH_DEVIATIONS** — requires architect override (D489) or deviation fixes
- **FAIL** — must fix before proceeding

### Step 2: Run handoff author

Use `/handoff-author` in Claude Code. The handoff gate checks:

```bash
bash scripts/handoff/find-next-handoff-version.sh <N>
```

### Step 3: Verify CI guards

```bash
cd ~/grace && bash scripts/check-regeneration-unchanged.sh
cd ~/grace && bash scripts/check-retrieval-unchanged.sh
cd ~/grace && bash scripts/check-no-third-party.sh
cd ~/grace && bash scripts/lint/check-migration-revision-ids.sh
```

### Step 4: Run full test suite

```bash
cd ~/grace && python3 -m pytest tests/ -v
```

### Step 5: Run live-server smoke

```bash
cd ~/grace && bash scripts/smoke-live-server.sh
```

### Step 6: Review canonical docs

After chunk close, the canonical docs should be updated:

- `docs/GrACE-Backlog.md` — chunk row updated to `shipped`
- `docs/GrACE-Decisions.md` — new D-numbers locked, pointer advanced
- `docs/GrACE-Doc-Map.md` — new docs indexed
- `CLAUDE.md` — critical rules and run commands updated

---

## Quick Reference

| Task | Command |
|------|---------|
| Start backend | `uvicorn src.api.main:app --reload --port 8000` |
| Start frontend | `cd frontend && npm run dev` |
| Run tests | `python3 -m pytest tests/ -v` |
| Frontend tests | `cd frontend && npm test` |
| Start ArcadeDB | `docker compose -f docker/docker-compose.arcade.yml up -d` |
| Apply migrations | `alembic upgrade head` |
| Pipeline status | `python3 scripts/pipeline/run_pipeline.py status --json` |
| Smoke test | `bash scripts/smoke-live-server.sh` |
| Check Ollama | `ollama list` |
