# GrACE — User Manual

**Graph as Auditable Context Engine**

*A client guide to using GrACE day to day.*

---

## Contents

1. [Welcome](#1-welcome)
2. [How GrACE Thinks: The Core Ideas](#2-how-grace-thinks-the-core-ideas)
3. [Getting Started](#3-getting-started)
4. [Everyday Use](#4-everyday-use)
5. [Teaching and Governing GrACE](#5-teaching-and-governing-grace)
6. [Compliance and Control](#6-compliance-and-control)
7. [Bringing Email Into GrACE](#7-bringing-email-into-grace)
8. [Reading Trust Signals](#8-reading-trust-signals)
9. [Connecting AI Assistants](#9-connecting-ai-assistants)
10. [Frequently Asked Questions](#10-frequently-asked-questions)
11. [Glossary](#11-glossary)

---

## 1. Welcome

GrACE is an **audit-grade memory layer** for document-heavy, regulated work — the kind of work where exceptions, precedents, and approvals matter and where every answer may need to be defended to an auditor or regulator.

In plain terms: GrACE reads your documents, decision records, and (optionally) filtered communications, and turns them into a connected **knowledge graph** — a web of facts and the relationships between them. You then ask questions in plain language, and GrACE answers using only what it has actually read, with a **clickable link back to the source** behind every claim.

Two promises define the product:

- **Nothing is stored without evidence.** Every fact in GrACE traces back to the document, span, and moment it came from.
- **Nothing is recalled without a source.** Every answer can be opened up to show exactly where it came from and how confident the system is.

GrACE is built for the senior reviewers, partners, adjudicators, underwriters, and trust officers who spend hours reconstructing rationale rather than deciding. It runs **on your own network by default** — your data does not leave your environment unless you explicitly configure it to.

> **What GrACE is not.** It is not a general workplace chatbot, an enterprise web-search tool, or an unsupervised AI that rewrites its own rules. It is a provenance-first, human-governed memory of your domain.

---

## 2. How GrACE Thinks: The Core Ideas

You will encounter these ideas throughout the product. Understanding them makes everything else intuitive.

### The knowledge graph

Instead of keeping a giant pile of documents and searching the text each time, GrACE **extracts the facts** out of your documents and stores them as a graph of **entities** (people, policies, claims, trusts, matters) and **relationships** between them. Each document typically yields a handful of entities and relationships. The original wording is summarized away; the facts — names, dates, dollar amounts, signatories, decisions — are preserved as structure.

### The ontology — what GrACE is allowed to know

The **ontology** is the schema that defines which kinds of things GrACE can represent in your domain (e.g., for insurance: *Claim, Exception, Precedent, Policy Clause, Reviewer, Approval Event*). It is the boundary of the system's knowledge. **You approve the ontology** — GrACE proposes, a human decides. The same GrACE software produces a different ontology for each domain; the software is the tool, the ontology is your work product.

### Provenance — how GrACE knows

Every fact carries a **provenance record**: the source document, the exact span of text, which model extracted it, when, and any human review that followed. This is what makes an answer defensible. You are always one click away from "show me where this came from."

### Certainty bands — not scary numbers

GrACE never shows you a raw confidence percentage. Instead, each claim is labeled with a **certainty band**: **High, Medium, Low,** or **Insufficient Evidence**. The bands are derived consistently from how strongly the underlying evidence supports the claim. (See [Reading Trust Signals](#7-reading-trust-signals).)

### Time validity — what was true *when*

Facts carry validity dates (*valid from / valid to*). GrACE can answer "what did we believe on this date" and supports backdated corrections without erasing history.

### Airgapped by default

Out of the box, GrACE runs all of its AI locally (on a local model server) and is set to **airgap mode on**. No content is sent to any outside service. You can connect a cloud AI provider later, but only by deliberately turning airgap mode off and configuring it — it never happens silently.

### The audit trail

Schema approvals, extractions, queries, and human decisions are written to **append-only, tamper-evident records** (cryptographically hash-chained where it matters). The history cannot be quietly edited. This is the backbone of "every decision is recorded."

---

## 3. Getting Started

### Where GrACE lives

GrACE is a **web application** you open in your browser. In a standard local deployment it runs at:

- **GrACE web app:** `http://localhost:3000`

(Your administrator may give you a different address if GrACE is hosted on a shared machine on your network.)

When you open GrACE, it takes you straight to the **Chat** screen.

### The navigation bar

A tab bar across the top is your map to everything:

| Tab | What it's for |
|---|---|
| **Chat** | Ask questions in plain language |
| **Graph** | Explore the knowledge graph visually |
| **Inspector** | See exactly how an answer was assembled |
| **Review** | Approve and refine the ontology (Guided Review) |
| **Claims** | Review facts GrACE flagged as uncertain |
| **Sources** | Choose which documents/folders GrACE reads |
| **Settings** | AI provider, airgap mode, ingestion options |
| **Directives** | Track intentional organizational changes |
| **Permissions** | See who is allowed to access what |
| **Sensitivity** | Manage sensitivity classification + reports |

Additional surfaces (Reconciliation reports, Schema Proposals, Communication Ingestion, Autonomy/Trust) are reached from links within these areas; they are covered below.

### A note on roles

Some actions are **read-only** (anyone can ask questions and browse). Other actions **change the system** (approving schema, accepting a flagged claim, ratifying permissions) and may require an administrator key when GrACE is deployed beyond a single local machine. If a button is unavailable to you, your deployment has reserved that action for an administrator.

---

## 4. Everyday Use

### 4.1 Asking questions — Chat

The Chat screen is the main way most people use GrACE.

- Type your question in plain language and submit.
- GrACE finds the most relevant slice of the knowledge graph, writes it into a grounded answer, and replies.
- **Each substantive claim in the answer is marked with a certainty band.** A legend explains the bands.
- Claims link back to their **source**, so you can verify any statement against the original document.

Because answers are built only from what GrACE has read, you will sometimes see **"Insufficient Evidence"** — that is the system telling you honestly that the documents do not support a confident answer, rather than guessing.

### 4.2 Exploring the graph — Graph

The Graph screen is a full-screen, interactive map of what GrACE knows.

- **Nodes** are entities (e.g., a specific claim, policy, or person); **edges** are the relationships between them.
- Click any node to see its details and provenance.
- Use the **type filter / legend** to focus on certain kinds of entities or relationships, and the **neighborhood expander** to walk outward from one entity to its connections.
- Filtering by **ontology module** (the domain area) lets you narrow to just claims, just policies, etc.

This view is useful for understanding context — "what is connected to this matter?" — that no single document shows.

### 4.3 Inspecting an answer — Inspector

The Inspector is your audit and troubleshooting tool. For any query it shows:

- **Which search strategies contributed** (GrACE searches several complementary ways at once and merges the results — see the FAQ).
- The **ranked evidence** that was retrieved.
- A **source trace** — the audit trail of which documents fed the answer.
- A timing breakdown, and a **replay** button to re-run a query with edits.

If you ever need to demonstrate *how* GrACE reached an answer, the Inspector is where you do it.

---

## 5. Teaching and Governing GrACE

GrACE is deliberately a **human-governed** system. These surfaces are how people shape and approve what it knows.

### 5.1 Choosing what GrACE reads — Sources

The Sources screen controls which folders and documents GrACE ingests.

- Enter or scan a directory; GrACE lists what it found, with **suggested inclusions** pre-checked.
- Review the selection, then confirm. A summary shows how many files will be processed.

When documents are processed, GrACE chunks them, extracts entities and relationships against the ontology, resolves them to canonical identities, and writes them into the graph with full provenance.

### 5.2 Approving the ontology — Review (Guided Review)

Guided Review is where a domain expert validates the **schema** — the types GrACE is allowed to use.

- A panel lists the proposed schema elements.
- A working canvas helps you make decisions about each element.
- You can flag an element as an **intentional change** (a deliberate shift in how the organization operates) rather than a discrepancy.

The principle is fixed: **the LLM proposes, the human decides.** No schema change goes live without human approval (with a narrow, opt-in exception described in [Autonomy](#56-trust-calibration-and-the-kill-switch--autonomy)).

### 5.3 Reviewing flagged claims — Claims

Not every extracted fact is trustworthy enough to enter the graph unsupervised. GrACE runs a **verification pass** on each fact (classifying it as *supported*, *refuted*, or *insufficient*) and a **constraint check** against the ontology. Facts that fail are **quarantined** for human review on the Claims screen.

For each quarantined claim you see:

- The claim and **why it was quarantined**.
- The **highlighted evidence** from the source document.
- A **Teach-Back** step that confirms you have understood the evidence before you decide.
- Any constraint violations and verifier notes.

You then choose a **disposition**:

- **Accept** — promote the claim into the graph.
- **Reject** — discard it (no graph change).
- **Edit-and-Accept** — correct it first; the original is marked superseded and your corrected version is promoted, preserving the full history.

### 5.4 Schema proposals — Proposals

As your document corpus grows, GrACE's adaptive-evolution agent notices patterns the current schema cannot represent and queues **schema-change proposals**. Each proposal shows:

- The proposed change in a readable change-command form.
- Its **tier** (1, 2, or 3 — higher tiers are more consequential), **priority**, and a **confidence band**.
- The ontology area affected.

You filter by tier and status (pending / approved / rejected / etc.) and disposition each one. **Tier 3 changes — restructuring hierarchies, deprecating types, changing what relationships are allowed — are always reviewed by a human, no exceptions.**

### 5.5 Change Directives

A **Change Directive** records that a leader is *intentionally* changing how the organization works — so GrACE can tell deliberate change apart from accidental drift. Directives have:

- A **tier** — *Operational Adjustment* (lightweight) or *Strategic Initiative* (with evidence criteria and a realization horizon).
- A **lifecycle**: draft → active → realized / abandoned / superseded.
- A **velocity** indication and a **stalled** flag, so you can see whether an initiative is actually progressing in the evidence.

You author directives inline from Guided Review and track them on the Directives screen.

### 5.6 Trust calibration and the kill switch — Autonomy

Over time, GrACE measures how reliable its own low-risk proposals have been. The Autonomy screen lets authorized users **calibrate how much the system may do on its own**, per tier:

- A **trust indicator** (red / amber / green) and progress toward earned autonomy for each tier.
- A **regression banner** if reliability has slipped.
- **Cooling proposals** — automatically-applied low-risk changes sit in a cooling-off window before they finalize, so they can be reverted.
- A **kill switch** that immediately halts all autonomous activity. (Engaging it is one click; disengaging requires confirmation — friction is intentional.)

This is the "earned autonomy" model: GrACE only earns the right to act unsupervised on the safest categories, only after a measured track record, and **Tier 3 is never autonomous.**

---

## 6. Compliance and Control

These surfaces exist because GrACE is built for regulated buyers. They are where counsel and compliance live.

### 6.1 Permissions

GrACE enforces access through a **single permission policy engine** operating on a **default-deny** basis: access must be explicitly granted, and an explicit deny always wins. Permissions are expressed as a **Permission Matrix** — a versioned, hash-chained, append-only record of which role clusters may see what.

The Permissions screen shows the **active matrix**, its version label and integrity hash, and the **history** of prior versions. A **drift queue** flags identities or access patterns that don't fit the current matrix and routes them for review.

### 6.2 The Sensitivity Gate

The Sensitivity Gate is GrACE's compliance control for sensitive content (privileged material, dense PII, externally-bounded communications).

**How it actually works in this release — described precisely:**

- Content is **classified and tagged** as it is processed, using **customer-authored rules** (for example, attorney-client/work-product language and PII density). These tags travel with the content in the graph.
- Tagged-sensitive content is **withheld from query results, retrieval surfaces, and Reconciliation reports** according to the Permission Matrix and sensitivity policy — so it does not surface to people who should not see it.
- The Sensitivity screen produces a **Sensitivity Classification Report** (with a coverage band), maintains a **separately filterable audit-evidence trail**, and lets authorized users review and ratify reports.
- The classification rules themselves live in the governed, versioned, audited layer and are intended for **General Counsel sign-off** on a regular cadence.

> **An honest note on scope.** In this release the Sensitivity Gate operates as a **classification, tagging, access-enforcement, and compliance-reporting** layer — sensitive content is tagged and then withheld from retrieval and reporting by policy. It is the control surface counsel points at, backed by the permission engine and the audit trail. If your deployment requires that certain content be **physically excluded from ingestion** rather than tagged-and-withheld, treat that as a deployment-time configuration to confirm with your GrACE administrator, not an automatic default.

### 6.3 Reconciliation reports

Real organizations hold several competing views of "the truth." GrACE keeps one **evidence-grounded base layer** and surfaces interpretive views on top — without taking sides. Three artifacts:

- **Gap Reports (Perception–Evidence):** per reviewer and per session, which approved concepts have strong document evidence, which are weak or unsupported, and which evidence was not prioritized.
- **Divergence Maps (Cross-Executive):** where two reviewers disagree about the same area, with the evidence shown for each side. GrACE does **not** adjudicate.
- **Documented Reality Reports:** a neutral, on-demand description of what the documents actually say, untouched by interpretation.

The framing is deliberately **observational, not evaluative**, and **participatory** — each person sees their own evidence-grounded view first. Reconciliation content is also subject to the Sensitivity Gate.

### 6.4 Settings and airgap

The Settings screen controls the AI engine and data posture:

- **AI provider and model** — defaults to a **local model** so everything runs on your network.
- **Airgap mode** — on by default. Turning it off (to use a cloud AI provider) requires passing an explicit confirmation dialog; GrACE will refuse a cloud-while-airgapped configuration.
- **Ingestion options** — where ingested data is stored, your organization's email/communication domains, and noise-filter thresholds.

---

## 7. Bringing Email Into GrACE

Email is GrACE's "noisy" input stream, so it is optional and handled deliberately. There are **two ways to bring email in**, and GrACE supports both:

- **Connect to a live email server.** GrACE logs into a mailbox with **read-only** access and pulls messages directly. Supported servers: **IMAP**, **Microsoft Exchange / Microsoft 365**, and **Gmail**. Best for ongoing, scheduled sync.
- **Import from exported files (download).** GrACE reads email you have already exported from your mail system as files: a single **.mbox** file, a folder of **.eml** files, a folder of Outlook **.msg** files, or an Outlook **.pst** archive. Best for one-time historical imports and for fully offline / air-gapped sites.

Either way, **GrACE only ever reads** — it never sends, replies to, deletes, or modifies your email — and everything stays on your network.

### Before you start: warm up the graph first

Email triage relies on GrACE already knowing your people and organizations, so it can tell a relevant message from noise. **Ingest your documents first** (the Sources screen), then bring in email. GrACE enforces this with a readiness check during setup, so don't be surprised if email setup asks you to load more documents first.

### Step-by-step: the setup wizard

Open the ingestion setup wizard at `http://localhost:3000/ingestion/setup`. It walks top to bottom.

**Step 1 — Choose a deployment path.** This tells GrACE how warmed-up the graph is:

- **Path A — Direct ingestion.** The graph is already populated from your documents; bring in email directly. This is the common case.
- **Path B — Bootstrapped ingestion.** The graph isn't warm yet; you first hand-pick a small, representative sample of emails to seed it, then ingest the rest.
- **Path C — Curated ingestion.** You deliberately work from a hand-picked selection of emails.

**Step 2 — Choose a source type.** This is where you choose *connect* vs *download*:

- Connect to a server: **IMAP**, **Exchange**, **Gmail**
- Import from files: **Mbox file**, **EML directory**, **Outlook MSG**, **PST archive**

**Step 3 — (Live servers only) Set a schedule.** Optionally turn on scheduled ingestion and choose **Recurring interval** (e.g. every 6 hours) or **One-time run**, so GrACE keeps the mailbox in sync on its own.

**Step 4 — Enter the configuration.** The fields change with the source type:

| Source | What you provide |
|---|---|
| Mbox file | Path to the `.mbox` file |
| EML directory | Folder containing `.eml` files |
| Outlook MSG | Folder containing `.msg` files |
| PST archive | Path to the `.pst` file |
| IMAP | Server host, username/mailbox, and a password (or the name of an environment variable holding an app password) |
| Exchange | Microsoft Graph URL, username/mailbox, and your Azure AD tenant ID (sign-in happens in Step 7 via OAuth) |
| Gmail | Sign-in happens in Step 7 via OAuth |

You also assign the email to an **ontology module / segment** (the domain area it belongs to). Click **Save source**.

**Step 5 — Test the connection.** GrACE samples up to ten messages and reports success plus the date range it found — so you know it can reach the mailbox (or read the files) before you commit to a full run.

**Step 6 — Check the readiness gate.** It shows **Ready**, **Not ready**, or **Bootstrap pending** (Path B). "Not ready" usually means the graph needs more documents in that segment first.

**Step 7 — (Exchange and Gmail only) Authorize access.** These use OAuth, so **GrACE never sees your password**:

- Click **Start OAuth flow**. GrACE shows a secure authorization link.
- Open the link, sign in to Microsoft or Google, and grant **read-only mail** access.
- Copy the URL you land on afterward and paste it into the **callback URL** box, then click **Submit authorization**.
- On success the source flips to **ready**. The access token is stored locally on your machine and is never shown in the interface.

**Step 8 — Start the ingestion run.** Click **Start ingestion run**. For live servers this both pulls new mail and runs triage in one pass; for file imports it loads the messages (triage can be run as a separate step).

### What triage does (automatically)

Every message runs through GrACE's **four-tier triage** before anything reaches the graph:

1. Cheap noise rejection (auto-replies, bulk mail, footers).
2. A check for mentions of people and organizations GrACE already knows.
3. A relevance check against your ontology.
4. An AI semantic filter on whatever survives the cheap layers.

Only the small fraction carrying genuinely new rationale is kept — the rest is filtered out, not stored.

### Watching progress

The **Ingestion dashboard** (`/ingestion`) shows:

- **Source health** for each source (ready / error / disabled, with a "re-consent needed" flag if a token expired).
- A **triage funnel** — how many messages dropped at each tier, shown as bands rather than raw percentages.
- A list of **runs** with status (running / completed / failed / paused).

Click a source to open its **detail page**, where you can browse the individual messages and their triage outcome, or **Re-authorize** if an OAuth token has expired.

### Curating a sample (Path B / C)

If you chose Path B or C, use the curation step (`/ingestion/setup/curate`): load the available emails, select a representative set, and click **Curate selection**. GrACE previews the **diversity** of your selection — sender spread, thread depth, and date range, shown as bands — so you can pick a balanced sample to seed the graph, and warns you if the sample looks too small or unnecessarily large.

### For operators: the command line

The same actions are available as commands, which is how scheduled jobs run them:

- `python -m src.ingestion run --source-id <id>` — pull messages only
- `python -m src.ingestion triage --source-id <id>` — run triage only
- `python -m src.ingestion cycle --source-id <id>` — pull and triage in one pass (used for live sources)

### Privacy and safety notes

- **Read-only by design.** Live connections request read-only mail access only (Microsoft `Mail.Read`, Gmail `gmail.readonly`); GrACE cannot send or delete mail.
- **Credentials stay local.** Passwords and OAuth tokens live in your local environment file and are redacted everywhere in the interface.
- **The Sensitivity Gate still applies.** Ingested email is classified and tagged, and sensitive content is withheld from results and reports per your policy.
- **Stop anytime.** A running ingestion can be paused, and disabling a source halts future scheduled runs.

---

## 8. Reading Trust Signals

GrACE's confidence vocabulary is intentionally simple and consistent. You will **never** see a raw confidence number on screen — bands are the contract.

| Band | What it means for you |
|---|---|
| **High** | Strongly supported by the retrieved evidence. Safe to rely on, still one click from its source. |
| **Medium** | Reasonably supported. Worth a glance at the source for consequential decisions. |
| **Low** | Weakly supported. Treat as a lead, not a conclusion; verify before relying on it. |
| **Insufficient Evidence** | The documents do not support a confident answer. GrACE is declining to guess. |

Behind the scenes, each fact also carries an internal confidence that **decays over time** if it is never re-verified — so stale facts naturally lose standing until reconfirmed. You experience this only through the bands and through what GrACE chooses to surface.

In Reconciliation surfaces, the equivalent signal is called the **evidence grounding score** and is likewise shown as bands, never as a raw number or as the word "drift" to end users.

---

## 9. Connecting AI Assistants

GrACE can be connected to an AI assistant host (such as Claude Desktop) so an agent can query your graph on your behalf. This connection:

- Runs **locally over a secure channel** with airgap checks enforced before every request.
- Exposes a **curated catalog of tools**. The everyday querying and inspection tools are **read-only by contract** — they cannot change your data. A separate, controlled set of review/authoring tools exists for guided-review workflows and is governed by the same permissions and audit trail as the web app.

The AI-assistant channel is a convenience for agent workflows; it is not a replacement for the human web interface, and it inherits all the same access controls.

---

## 10. Frequently Asked Questions

**Does my data leave my network?**
No, not by default. GrACE ships airgapped with a local AI model. Using an external/cloud provider is possible but requires you to deliberately turn airgap mode off and configure it.

**Can GrACE change my ontology or data on its own?**
Only within tightly bounded, opt-in limits. By default, humans approve schema changes and disposition flagged claims. The earned-autonomy feature can let GrACE auto-apply only the **lowest-risk** change categories, only after a measured reliability record, with a cooling-off window and a kill switch — and **Tier 3 changes are always human-reviewed.**

**Why did GrACE say "Insufficient Evidence" instead of answering?**
Because the documents it has read don't support a confident answer. That is by design — GrACE would rather decline than fabricate. Consider whether the relevant source has been ingested (see Sources).

**How does GrACE search — is it just keyword search?**
No. For each query it runs several complementary strategies at once (graph relationships, meaning-based similarity, keyword match, time filtering, and chunk-level matching), merges the results, and re-ranks them. If the first pass is thin, it can do a second enrichment pass automatically.

**How is an answer auditable?**
Every claim links to its source span and provenance record; the Inspector reconstructs exactly which evidence produced an answer; and queries, approvals, and decisions are written to append-only, hash-chained records.

**What happens to a fact I reject in Claims?**
It is discarded and does not enter the graph; the decision is recorded. If you Edit-and-Accept instead, the original is marked superseded and your corrected version is promoted, preserving history.

**Can I bring in email without connecting GrACE to our mail server?**
Yes. GrACE supports two paths: connect to a live mailbox (IMAP, Exchange/Microsoft 365, or Gmail) with read-only access, or import email you have already exported as files (.mbox, .eml, .msg, or Outlook .pst). The file-import path is ideal for one-time historical loads and fully offline sites. See "Bringing Email Into GrACE."

**Can GrACE send, reply to, or delete email?**
No. Email access is read-only by design — live connections request read-only scopes only, and file imports obviously can't touch your server. GrACE pulls a copy, filters it, and stores only what is relevant.

**Who can see sensitive content?**
Only those allowed by the Permission Matrix. Sensitive content is classified, tagged, and withheld from query results and reports for everyone else, and a compliance report and audit trail document the controls.

---

## 11. Glossary

- **Airgap mode** — Setting (on by default) that keeps all AI processing local; no data leaves your network.
- **Certainty band** — The High / Medium / Low / Insufficient-Evidence label on a claim. GrACE never shows raw confidence numbers.
- **Change Directive** — A record of an intentional organizational change, so deliberate change is distinguished from accidental drift.
- **Claim** — A single extracted fact (a triple) with its evidence, confidence, and time validity.
- **Deployment path (A / B / C)** — How email ingestion warms up: A (direct, graph already populated), B (bootstrap with a curated sample first), C (work from a curated selection).
- **Disposition** — Your decision on a quarantined claim: Accept, Reject, or Edit-and-Accept.
- **Drift queue** — Identities or access patterns that don't fit the current Permission Matrix, queued for review.
- **Entity** — A node in the graph: a person, claim, policy, trust, matter, etc.
- **Evidence grounding score** — The Reconciliation-layer equivalent of a certainty signal, shown as bands.
- **Knowledge graph** — The connected web of entities and relationships that replaces raw document storage.
- **Ontology** — The schema defining what kinds of things GrACE is allowed to represent; the boundary of its knowledge. Human-approved.
- **Permission Matrix** — The versioned, hash-chained record of who may access what; enforced by a single default-deny policy engine.
- **Provenance** — The record of where, when, and how each fact was extracted, plus any human review.
- **Quarantine** — Holding an extracted fact aside for human review when it fails verification or constraints.
- **Reconciliation** — Surfacing the gap between organizational belief and document evidence, observationally and without adjudicating.
- **Sensitivity Gate** — The compliance control that classifies, tags, withholds, and reports on sensitive content.
- **Source (ingestion source)** — A configured email origin: either a live server connection (IMAP / Exchange / Gmail) or an exported-file location (.mbox / .eml / .msg / .pst).
- **Teach-Back** — The step in claim review that confirms you've understood the evidence before deciding.
- **Tier (1 / 2 / 3)** — How consequential a schema change or autonomous action is. Tier 3 is always human-reviewed.

---

*GrACE keeps the human in charge of the boundaries and keeps every answer tied to its evidence. When in doubt, click through to the source.*
