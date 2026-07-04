# GrACE Frontend

The web interface for **GrACE — Graph as Auditable Context Engine**. A Next.js 15
(App Router, TypeScript strict, Tailwind) application that talks to the GrACE
FastAPI backend over JSON.

Key surfaces:

- **Chat** — ask questions grounded in the knowledge graph, with certainty bands
  and evidence links.
- **Graph viewer** (`/graph`) — browse entities, relationships, and neighborhoods.
- **Guided review** (`/review`) — approve or reject the proposed ontology.
- **Claims** (`/claims`) — review flagged extractions before they are trusted.
- **Sources / Settings** (`/sources`, `/settings`) — pick documents, configure
  the LLM provider.

## Prerequisites

- Node.js 20+
- The GrACE backend API running (default `http://127.0.0.1:8000`) — see
  [../INSTALL.md](../INSTALL.md)

## Setup

```bash
npm install
cp .env.local.example .env.local
```

Edit `.env.local` if your backend is not on the default host/port — the frontend
resolves the API from `NEXT_PUBLIC_GRACE_API_BASE_URL`.

## Commands

| Command | What it does |
|---------|--------------|
| `npm run dev` | Dev server at http://localhost:3000 |
| `npm run build` | Production build (strict TypeScript) |
| `npm test` | Test suite (vitest) |
| `npm run typecheck` | Type check only (`tsc --noEmit`) |

## Notes

- All dependencies in `package.json` are pinned to exact versions (no carets).
  Upgrades are deliberate and reviewed — do not bump versions casually.
- Full setup and first-run walkthrough:
  [../docs/GrACE-Onboarding-Setup-Manual.md](../docs/GrACE-Onboarding-Setup-Manual.md).
