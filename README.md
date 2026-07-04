# GrACE — Graph as Auditable Context Engine

**Claude-as-the-LLM deployment.**

GrACE turns an organization's documents (and, optionally, email) into an
**auditable knowledge graph** you can ask questions of in plain language. Every
answer is grounded in the source material, carries a certainty band, and links
back to the evidence that produced it. A human approves the ontology and reviews
flagged extractions before anything is trusted.

In this deployment, **Claude (Anthropic)** is the reasoning LLM — driving discovery,
ontology proposal, extraction, and review through the skills in
[`grace-claude-skills/`](grace-claude-skills/) and the in-app Anthropic provider. A
small local Ollama model handles only embeddings and a few bounded pipeline steps —
Ollama is not the primary LLM here.

> **Local-LLM-only operation** (no cloud, fully airgapped) is supported by the
> codebase and is a planned future deployment option. This install uses Claude today.

## Getting started

**Installing on a fresh machine (macOS or Windows)? Start with the from-scratch
install guide, which an AI agent (Claude) can follow end to end:**

➡️ **[INSTALL.md](INSTALL.md)** — install prerequisites, then →
**[docs/GrACE-Onboarding-Setup-Manual.md](docs/GrACE-Onboarding-Setup-Manual.md)** —
configure, bring up, and run the first document → ontology → extraction → ask loop.

In brief:

1. **Install prerequisites** (`INSTALL.md`): Docker, PostgreSQL 17, Ollama, uv (which
   provisions Python 3.14), Node.js 20+, and an Anthropic API key.
2. `cp .env.example .env` and fill in `LLM_API_KEY` (Anthropic) and
   `GRAFANA_POSTGRES_PASSWORD`.
3. `uv sync --extra dev` → start ArcadeDB → `alembic upgrade head` →
   start the API and the frontend.
4. In the web UI: pick your documents → review and approve the proposed ontology →
   extract → review flagged claims → ask questions in Chat.

Day-to-day usage is covered in **[GrACE-User-Manual.md](GrACE-User-Manual.md)**.

## What's in here

| Path | What it is |
|------|------------|
| `src/` | Backend modules (discovery, ontology, extraction, graph, retrieval, regeneration, analytics, ingestion, MCP server, …) |
| `frontend/` | Next.js 15 web interface |
| `grace-claude-skills/` | **Claude-as-the-LLM** operator skills + live test harnesses |
| `alembic/` | Database migrations |
| `config/` | Module configuration (YAML/JSON) |
| `seeds/` | Reference ontologies (FIBO, LKIF, PROV-O, Schema.org) |
| `docker/` | Docker Compose for ArcadeDB and the observability stack |
| `scripts/` | Setup, scheduling, and operator utilities |
| `tests/` | Test suite (mirrors `src/`) |
| `docs/` | Product overview, onboarding manual, operator runbooks |

## Operating principles

- **The LLM proposes; the human decides.** No schema or extracted fact is trusted
  without human review.
- **Everything is provenanced.** Temporal validity, confidence bands, and source
  links on every fact.
- **Secrets stay local.** `.env` is gitignored; never commit credentials.

See [`CLAUDE.md`](CLAUDE.md) for the operational reference and
[`docs/runbooks/`](docs/runbooks/) for operator procedures.
