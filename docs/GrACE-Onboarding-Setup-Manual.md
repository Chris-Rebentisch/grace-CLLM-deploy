# GrACE — Onboarding & Setup Manual

**Claude-as-the-LLM deployment.**

**Purpose:** Walk an operator through a complete first run — machine prep, service
bring-up, installing the Claude skills, and the first document → ontology →
extraction → ask loop. Today these steps run by hand; the target is to collapse the
prep and bring-up bundles into a one-click installer.

In this deployment **Claude (Anthropic)** is the reasoning LLM. A small local model
(Ollama) is still required for embeddings and a few bounded pipeline steps, so you
install both. The steps are grouped into bundles:

- **Bundle A — Prepare** (Steps 1–3): one-time machine prep.
- **Bundle B — Bring-up** (Steps 4–11): start the services.
- **Bundle C — Claude skills** (Step 11.5): install the Claude-as-the-LLM skills.

> Run everything from inside the project directory, top to bottom.

---

## Bundle A — Prepare (Steps 1–3)

*One-time setup of the machine. In a packaged install, this whole bundle would be handled by the installer / appliance image.*

### Step 1 — Install / confirm prerequisites

**On a fresh machine, follow `../INSTALL.md` first** — it has the exact from-scratch
install commands for macOS and Windows. These must be present before anything else
works:

- [ ] **Docker runtime** — Colima (macOS) or Docker Desktop (Windows)
- [ ] **PostgreSQL 17** — main database
- [ ] **Ollama** — local model server (`localhost:11434`), for embeddings + a small model
- [ ] **Python 3.14** — provisioned by `uv` (pinned in `.python-version`)
- [ ] **uv** — Python package manager
- [ ] **Node.js 20+** — for the web interface
- [ ] **Anthropic API key** — Claude is the reasoning LLM for this deployment
- [ ] **Claude Desktop or Claude Code** — to run the GrACE skills (Bundle C)

Pull the two local models Ollama needs (embeddings + small-model pipeline steps):

- [ ] `ollama pull nomic-embed-text`
- [ ] `ollama pull qwen2.5:7b`

### Step 2 — Set up the environment file
GrACE reads its config from `.env`.

- [ ] `cp .env.example .env`
- [ ] Set **`LLM_API_KEY`** to your **Anthropic** API key (or run `bash scripts/set-api-key.sh`)
- [ ] Set **`GRAFANA_POSTGRES_PASSWORD`** to a real value (replace `CHANGEME`)
- [ ] Set **`DATABASE_URL`** — replace `<user>` with your local Postgres user
      *(macOS: the default role is your username; Windows: it is `postgres`)*

*This deployment uses Claude (a cloud provider), so **airgap mode is OFF** and the
provider defaults to **Anthropic** in `config/discovery.yaml`. Ollama is still
required for embeddings and a small local model — keep it running.*

### Step 3 — Install Python dependencies

- [ ] `pip install --break-system-packages uv`  *(one-time bootstrap of uv only; safe to re-run — it installs only the uv bootstrap tool)*
- [ ] `uv sync --extra dev`
- [ ] Activate the virtual environment:
  - **macOS / Linux:** `source .venv/bin/activate`
  - **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`

---

## Bundle B — Bring-up (Steps 4–11)

*Starting the services. In a packaged install, this whole bundle would collapse into a single `docker compose up` that also runs migrations, creates the read-only role, and self-checks health.*

### Step 4 — Start the Docker runtime
- [ ] **macOS:** `colima start --cpu 2 --memory 4 --disk 20`
- [ ] **Windows:** launch **Docker Desktop** once so the engine is running *(the Colima flags don't apply)*

### Step 5 — Start the graph database (ArcadeDB)
- [ ] `docker compose -f docker/docker-compose.arcade.yml up -d`
- Check later at `http://localhost:2480` (login `root` / `gracedev`)
- *`root` / `gracedev` is a **dev default** — rotate it before any non-localhost exposure (same goes for `GRAFANA_POSTGRES_PASSWORD=CHANGEME` in `.env.example`).*

### Step 6 — Create the read-only database role
Restricted account the dashboards read through (can never modify data).

- [ ] `GRAFANA_POSTGRES_PASSWORD="$GRAFANA_POSTGRES_PASSWORD" bash scripts/setup/bootstrap_grace_readonly.sh`
- [ ] Verify: add `--check` to the same command
- *The script targets the database named by `PGDATABASE` (default `grace`) — set it first if your database is named differently.*

### Step 7 — Build the database tables (migrations)
- [ ] *(fresh machine only)* `createdb grace` — required **once** if the `grace` database doesn't exist yet; skip if it already does. Safe to check first with `psql -l` (look for `grace` in the list).
- [ ] `alembic upgrade head`

### Step 8 — Start the monitoring stack
- [ ] `docker compose -f docker/docker-compose.observability.yml up -d`
- Grafana at `http://localhost:3001` (login `admin` / `gracedev`); Prometheus at `http://localhost:9090`
- *`admin` / `gracedev` is a **dev default** — rotate it before any non-localhost exposure, and make sure `GRAFANA_POSTGRES_PASSWORD` in `.env` is no longer `CHANGEME` (Step 2).*

### Step 9 — Check monitoring came up cleanly
- [ ] `bash scripts/preflight/grafana_health_check.sh`  *(prints a diagnostic if anything's wrong)*

### Step 10 — Start the GrACE backend (API server)
- [ ] `uvicorn src.api.main:app --reload --port 8000`  *(leave running)*
- This command **blocks the terminal** — open a second terminal for Step 11 (the frontend)
- Confirm at `http://localhost:8000/docs`

### Step 11 — Start the web interface (frontend)
- [ ] `cd frontend && npm install && npm run dev`  *(npm install only needed the first time)*
- Open `http://localhost:3000` — lands on the Chat screen

---

## Bundle C — Install the Claude-as-the-LLM skills (Step 11.5)

*This is what makes Claude the LLM. The skills in `grace-claude-skills/` run inside
Claude Desktop / Claude Code and drive discovery, ontology authoring, extraction,
review, and the live test harnesses. The MCP server lets Claude call GrACE directly.*

### Step 11.5 — Install the skills and (optionally) the MCP server
- [ ] Copy each skill you want into Claude's skills folder, e.g.
      `cp -R grace-claude-skills/grace-cq-authoring ~/.claude/skills/`
      (repeat for `grace-graph-extraction`, `grace-ontology-proposal`,
      `grace-property-detailing`, `grace-review-protocol`, `grace-testing-protocol`,
      `grace-intent-elicitation`, and any harnesses you'll use).
- [ ] *(Optional but recommended)* wire up the MCP server so Claude can call GrACE:
      copy `scripts/claude_desktop_config.example.json` into your Claude Desktop
      config, replacing `/Users/YOUR_USERNAME/grace` with this project's path and
      pointing `command` at this project's `.venv/bin/python`.
- [ ] Confirm the API (Step 10) is running — the skills and MCP server talk to it at
      `http://127.0.0.1:8000`.

See `grace-claude-skills/README.md` for what each skill does.

---

## Verify it's up

| Service | URL | Expected |
|---|---|---|
| Web app | `http://localhost:3000` | Chat screen |
| API docs | `http://localhost:8000/docs` | Swagger UI |
| Graph DB | `http://localhost:2480` | ArcadeDB login |
| Grafana | `http://localhost:3001` | Grafana login |
| Prometheus | `http://localhost:9090` | Prometheus UI |
| Ollama | `http://localhost:11434` | Version response |

*The Graph DB (`root`/`gracedev`) and Grafana (`admin`/`gracedev`) logins are dev defaults — rotate both before any non-localhost exposure (see `.env.example`, `GRAFANA_POSTGRES_PASSWORD=CHANGEME`).*

---

## First Run — Configure & Load (Steps 12–17)

*Using the running system. Unlike Bundles A and B (plumbing that should be automated), these are genuine first-run actions — a mix of client decisions and operator steps. A guided first-run UI should eventually own these; today they're partly UI, partly CLI/API.*

### Step 12 — Set your first-run options (Settings screen)
Open `http://localhost:3000` → **Settings**.

- [ ] Confirm **airgap mode** is **OFF** — this deployment uses Claude, a cloud provider
- [ ] Confirm the **AI model / provider** is **Anthropic (Claude)** — the default for this build (your `LLM_API_KEY` was set in Step 2)
- [ ] *Remote deployments only:* set **`GRACE_ADMIN_KEY`** in `.env` and restart the API (Step 10). This is an `.env` edit, **not** a Settings toggle. Skip on a localhost-only install.

### Step 13 — Point GrACE at your documents (Sources screen)
Open the **Sources** screen — this selects which files GrACE reads. It does *not* extract anything yet.

- [ ] **Scan a directory**; review the discovered folders (sensible ones are pre-checked, "suggested include")
- [ ] Adjust the selection → **Continue** → confirm at the summary (file count + estimated time)

GrACE then **processes** the files: converting each document (PDF, Word, Excel, …) into clean text and splitting it into chunks. Preparation only — nothing is in the knowledge graph yet.

### Step 14 — Review and approve the ontology (Guided Review screen)
Open the **Review** screen. From your processed documents, GrACE proposes an **ontology** — the entity and relationship types your domain needs. Nothing is extracted into the graph until a human approves these boundaries.

- [ ] Work through the proposed schema elements; **accept / refine / reject** each
- [ ] Flag anything that's a deliberate change rather than an error
- [ ] **Approve** the schema once it reflects your domain
- [ ] On approval, the **CQ non-regression gate** runs — it checks the schema can still answer the competency questions and blocks promotion below the pass-rate threshold *(this is where CQ validation happens — against the **schema**, not the extracted data)*

Principle in action: **the LLM proposes, you decide.** The approved ontology becomes extraction's rulebook.

### Step 15 — Extract documents into the graph (choose routing)
GrACE reads the processed text against the approved ontology and writes **entities, relationships, their temporal validity (`valid_from` / `valid_to` / `extracted_at`), confidence, and provenance** into the graph — the "compression" step. This is also where the **extraction router** picks which AI engine handles each document.

Pick a provider outright, or let a strategy auto-route:

- **`sensitivity`** — privileged docs → the **local** model (airgap-safe); everything else → the **cloud** provider (the "both" split)
- **`size_tier`** — routes by file size (small / medium / large) per `config/extraction_router.yaml`

Run it one of two ways (operator CLI or API — no web screen for this yet):

- [ ] CLI: `python -m src.discovery.batch_runner --source-dir data/corpus/ --job-id <id> --router-strategy sensitivity`
- [ ] or API: `POST /api/extraction/jobs` with a `router_strategy` (or an explicit `provider` / `model`)

Claims that pass verification land in the graph automatically; anything that fails verification or a constraint check is **quarantined** for human review in Step 16.

### Step 16 — Review the quarantined claims (Claims screen)
Open the **Claims** screen. Extraction already auto-wrote the claims that passed verification + constraints; the ones it flagged (refuted, insufficient evidence, or an ERROR-severity constraint violation) are **quarantined** and wait here for a human. This is the human-in-the-loop gate on extracted graph content — do it **before** you rely on the graph for answers.

- [ ] Review each quarantined claim alongside its highlighted **evidence**
- [ ] Complete the **Teach-Back** step (confirms you've read the evidence)
- [ ] Choose a disposition: **Accept** (promote to graph), **Reject** (discard), or **Edit-and-Accept** (correct it; the original is superseded)

*The gate is on the **flagged subset only** — high-confidence, constraint-clean claims already wrote to the graph in Step 15. CQ validation is not repeated here; it ran against the schema back in Step 14.*

### Step 17 — Ask questions in Chat (the payoff)
Open the **Chat** screen — what the whole setup was for.

- [ ] Type a question in plain language and submit
- GrACE retrieves the relevant slice of the graph (multiple strategies, merged + re-ranked), regenerates a grounded answer, and replies
- Each claim carries a **certainty band** (High / Medium / Low / Insufficient Evidence — never raw numbers) and a one-click **source link**
- Use the **Inspector** screen to reconstruct exactly which evidence produced any answer

That closes the core loop: **documents → processed → ontology approved (CQ-gated) → extracted → quarantine reviewed → asked and answered, with provenance.**

---

## Optional — Bringing In Email (Steps 18–22)

*Optional enrichment. Email is the noisy input stream, so it runs **after** the graph is warm from documents — triage needs the people and organizations GrACE already knows. GrACE only ever **reads** mail; it never sends, replies, or deletes. Two ways in: connect to a live server, or import exported files.*

### Step 18 — Open the ingestion setup wizard and choose a deployment path
Email is optional enrichment, and it runs **after** your graph is warm from documents — triage needs the people and organizations GrACE already knows, so make sure the document pipeline (Steps 13–16) is done first.

Go to `http://localhost:3000/ingestion/setup`. The first thing the wizard asks for is a **deployment path** — this tells GrACE how warmed-up the graph is, so it knows how much to verify before triaging:

- **Path A — Direct ingestion.** The graph is already populated from your documents; bring email in directly. The common case once Steps 13–16 are complete.
- **Path B — Bootstrapped ingestion.** The graph isn't warm yet; you first hand-pick a small, representative sample of emails to seed it, then ingest the rest.
- **Path C — Curated ingestion.** You deliberately work from a hand-picked selection of emails.

- [ ] Click your path. GrACE saves it to the deployment config and reveals the next choice.

For a normal first run where documents are already loaded, choose **Path A.** (Pick B or C only if you want to seed or scope from a curated sample first — that adds a curation step.)

### Step 19 — Choose how email comes in, then configure the source
This is the heart of it — GrACE gives you **two ways to bring email in**:

- **Connect to a live server** — GrACE logs into the mailbox read-only and pulls directly: **IMAP**, **Exchange / Microsoft 365**, or **Gmail**. Best for ongoing sync.
- **Import exported files (download)** — GrACE reads mail you've already exported: **Mbox file**, **EML directory**, **Outlook MSG** folder, or **Outlook PST archive**. Best for one-time historical loads and fully offline sites.

Once you pick a source type, the wizard shows the fields for it:

- [ ] **Files** → the path to the file or folder (`.mbox` / `.pst` = file path; `.eml` / `.msg` = directory)
- [ ] **IMAP** → server host, username/mailbox, and a password (or the name of an env var holding an app password)
- [ ] **Exchange** → Microsoft Graph URL, username, and your Azure AD tenant ID *(sign-in happens in Step 21 via OAuth)*
- [ ] **Gmail** → nothing to type here; *sign-in happens in Step 21 via OAuth*
- [ ] *(Live servers only)* set a **schedule** — Recurring interval (e.g. every 6 hours) or One-time run
- [ ] Assign an **ontology module / segment** (the domain area this mail belongs to), then click **Save source**

Worth repeating: every one of these is **read-only** — GrACE pulls a copy and can't send, reply to, or delete anything — and credentials stay local.

### Step 20 — Test the connection and check readiness
Before committing to a full run, the wizard gives you two checks.

**Test connection** — confirms GrACE can actually reach the source:

- [ ] Click **Test connection.** GrACE samples up to ten messages and reports success plus the **date range** it found (oldest → newest).
- A pass means the mailbox is reachable (or the files are readable); a fail returns the specific error so you can fix the host, path, or credentials.

**Readiness gate** — confirms the graph is warm enough for triage to be meaningful:

- [ ] Read the gate result: **Ready**, **Not ready**, or **Bootstrap pending**.
- Under the hood it checks, per segment, that GrACE already knows enough **people and organizations** and has enough **accepted competency questions**. "Not ready" almost always means that segment needs more **documents** loaded first (Steps 13–16).
- On **Path B**, you'll see **Bootstrap pending** until you curate a seed sample.

Think of it as: *test connection* = "can I get the mail?" and *readiness* = "do I know enough to tell signal from noise?"

### Step 21 — (Exchange & Gmail only) Authorize read-only access via OAuth
Exchange and Gmail sign in through **OAuth**, so GrACE never sees or stores your password — and it requests **read-only mail** scope only (`Mail.Read` for Exchange, `gmail.readonly` for Gmail). File imports and IMAP skip this step.

- [ ] Click **Start OAuth flow.** GrACE generates a secure authorization link (with a one-time anti-forgery token).
- [ ] Open the link, **sign in** to Microsoft or Google, and **grant read-only mail access.**
- [ ] You'll land on a **callback URL.** Copy that full URL and **paste it** into the wizard's callback box.
- [ ] Click **Submit authorization.**

On success: the source flips to **ready**, the refresh token is stored **locally in `.env`** (referenced by an env var) and redacted everywhere in the UI, and any schedule you set in Step 19 is registered now.

Caveat from how it's built: the authorization token is **one-time and expires after ~10 minutes** — if you get interrupted, click **Start OAuth flow** again for a fresh link.

### Step 22 — Start the run, then monitor
This is the finish line for email.

- [ ] Click **Start ingestion run.** For a **live server**, GrACE pulls new mail **and** runs triage in one pass; for a **file import**, it loads the messages (triage can run as a separate action).

Every message runs the **four-tier triage** before anything reaches the graph — cheap filters first, expensive ones last:

1. **Noise rejection** — auto-replies, bulk mail, footers.
2. **Known-entity mention** — does it reference people/organizations GrACE already knows?
3. **Ontology relevance** — does it touch the ratified schema?
4. **AI semantic filter** — a final judgment on whatever survives.

Only the small fraction carrying genuinely new rationale is kept; the rest is filtered out, not stored.

Then watch it:

- [ ] Open the **Ingestion dashboard** (`/ingestion`) — **source health** (ready / error / disabled), the **triage funnel** (how many dropped at each tier, shown as bands), and **run status** (running / completed / failed / paused).
- [ ] Click a source to browse its **individual messages and triage outcomes**, or to **Re-authorize** if an OAuth token has expired.

What survives triage flows exactly like documents did: it becomes entities and relationships with provenance, any flagged claims land in **Claims** for review (Step 16), and you can ask about it in **Chat** (Step 17).

*Path B/C only: use the curation step (`/ingestion/setup/curate`) to pick a representative sample first — GrACE previews sender / thread / date diversity as bands. Operators can run it headless: `python -m src.ingestion cycle --source-id <id>` (pull + triage). Ingested email passes the **Sensitivity Gate** (tagged and withheld per policy); a running ingestion can be paused, and disabling a source stops scheduled runs.*

---

## Further first-run decisions (optional)

These are not blocking gates, but a thorough first run should cover them:

- **Sensitivity-rule sign-off.** Review `config/sensitivity_rules.yaml` with whoever
  owns privilege/PII policy for this client. The Sensitivity Gate tags and withholds
  sensitive content based on these rules; confirm they fit the client's data before
  relying on email ingestion.
- **Optional QA pass.** Two non-blocking quality checks are available once the graph
  is populated: **MINE-1 retention sampling** (does extraction preserve the facts in a
  document?) and **Reconciliation Gap Reports** (does the graph cover what the source
  documents say?). Run them when you want a confidence read on a segment.
- **Scheduled batch jobs.** If you want signals, correlations, evaluation, or
  confidence-decay to run on a cadence, install the sample schedules in
  `scripts/launchd/` (cron equivalents work too). These run out-of-process by design.

## Where to go next

- **Day-to-day usage:** `../GrACE-User-Manual.md`
- **Operator procedures:** `runbooks/`
- **What GrACE is, conceptually:** `GrACE-Product.md`
- **The Claude skills that drive the workflow:** `../grace-claude-skills/README.md`
