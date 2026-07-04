# Installing GrACE — start here

**If you are an AI agent (e.g. Claude) setting GrACE up on this machine, read this
file top to bottom, then continue with `docs/GrACE-Onboarding-Setup-Manual.md`.**

## What GrACE is (30-second orientation)

GrACE (Graph as Auditable Context Engine) turns an organization's documents — and
optionally its email — into an **auditable knowledge graph** that people query in
plain language. Every answer is grounded in the source material, carries a certainty
band, and links back to its evidence. A human approves the ontology and reviews
flagged extractions before anything is trusted.

This is the **Claude-as-the-LLM** deployment: Claude (Anthropic) is the reasoning
LLM, driving discovery, ontology proposal, extraction, and review via the skills in
`grace-claude-skills/` and the in-app Anthropic provider. A small local model (Ollama)
is still required for embeddings and a few bounded pipeline steps.

## Install flow

```
INSTALL.md (this file)            →  docs/GrACE-Onboarding-Setup-Manual.md
1. Detect the OS                     Bundle B  — bring up the services
2. Install the prerequisites         Bundle C  — install the Claude skills
3. Provision Python + dependencies   First Run — load documents, approve ontology,
4. Pull the local Ollama models                  extract, ask questions
5. Get an Anthropic API key
→ then hand off to the manual at Step 2 (the .env file)
```

## Step 1 — Detect the operating system

- **macOS** → use the Homebrew column below.
- **Windows** → use the winget (PowerShell) column below. Run PowerShell as
  Administrator for the installs.
- **Linux** → not covered here; use your distro's package manager for the same
  components (Docker, PostgreSQL 17, Node 20+), install `uv` and `ollama` from their
  official scripts, then resume at Step 3.

## Step 2 — Install the prerequisites

| Component | macOS (Homebrew) | Windows (winget / PowerShell) |
|-----------|------------------|-------------------------------|
| Homebrew (mac only) | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` | — |
| Docker runtime | `brew install colima docker docker-compose` | `winget install -e --id Docker.DockerDesktop` |
| PostgreSQL 17 | `brew install postgresql@17` | `winget install -e --id PostgreSQL.PostgreSQL.17` |
| Ollama (local models) | `brew install ollama` | `winget install -e --id Ollama.Ollama` |
| Node.js 20+ | `brew install node` | `winget install -e --id OpenJS.NodeJS.LTS` |
| uv (Python manager) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Claude Code (to run skills) | `npm install -g @anthropic-ai/claude-code` | `npm install -g @anthropic-ai/claude-code` |

Start the background services after installing:

- **macOS:** `colima start --cpu 2 --memory 4 --disk 20` (starts the Docker engine),
  then `brew services start postgresql@17` and `brew services start ollama`.
- **Windows:** launch **Docker Desktop** once so the engine is running; PostgreSQL and
  Ollama install as services and start automatically (confirm Ollama is reachable at
  `http://localhost:11434`).

> Python itself is **not** installed separately — `uv` provisions the pinned
> interpreter in Step 3 (the version is fixed in `.python-version`).

## Step 3 — Provision Python and install dependencies

From the project directory:

```bash
uv python install 3.14      # installs the pinned interpreter uv will use
uv sync --extra dev         # creates .venv and installs all dependencies
```

Then activate the virtual environment:

- **macOS / Linux:** `source .venv/bin/activate`
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`

Finally, bootstrap the frontend dependencies (one-time; uses the Node.js installed
in Step 2):

```bash
cd frontend && npm install && cd ..
```

## Step 4 — Pull the local Ollama models

Required even though Claude is the main LLM (embeddings + a small bounded model):

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:7b
```

## Step 5 — Get an Anthropic API key

Create an API key at the Anthropic console (it starts with `sk-ant-`). You will put
it in `.env` in the next step (or run `bash scripts/set-api-key.sh`). **Never commit
the key** — it lives only in the gitignored `.env`.

## → Continue with the onboarding manual

The machine is now provisioned. Continue with
**`docs/GrACE-Onboarding-Setup-Manual.md`** starting at **Step 2 (the `.env` file)**:
configure `.env`, run the database migrations, bring up ArcadeDB / the API / the
frontend, install the Claude skills (Bundle C), and walk the first document →
ontology → extraction → ask loop.

## Platform notes

- **OCR backend auto-selects by OS.** On macOS GrACE uses Apple's Vision framework
  (OcrMac); on Windows/Linux it uses RapidOCR (CPU). This is automatic via
  `config/discovery.yaml` (`document_processing.ocr.backend: auto`) — no action needed.
  The macOS-only `ocrmac` dependency is skipped on Windows by its platform marker.
- **PostgreSQL user.** On macOS the default role is your username; on Windows it is
  `postgres`. Set `DATABASE_URL` in `.env` accordingly (replace `<user>`).
- **The `KMP_DUPLICATE_LIB_OK` / `OMP_NUM_THREADS` settings** in `.env` are macOS
  OpenMP-stability flags; they are harmless on Windows.
- **Single uvicorn worker.** Run one API worker per environment (the scheduler would
  otherwise double-fire). The onboarding manual's bring-up uses a single worker.
