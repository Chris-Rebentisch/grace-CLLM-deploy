---
name: grace-corpus-export
description: >
  STEP 1 of the Claude-as-LLM onboarding flow. Export GrACE's processed documents
  into per-domain markdown corpus bundles that Claude can read, using balanced
  all-document coverage (not top-10). Use this before authoring CQs or proposing an
  ontology when you want Claude — not local gpt-oss — to do the reasoning. No Ollama,
  no heat.
---

# grace-corpus-export

## Why this exists
GrACE normally feeds documents to local gpt-oss:120b for CQ generation and schema
extraction. On a heat-sensitive machine that model overheats the host. This skill
pulls the already-processed document text out of Postgres so **Claude Desktop** can
be the reasoning engine instead. This step touches Postgres only — no model runs.

## Preconditions
- Documents already processed into `processed_documents` (status `COMPLETE`). If not,
  run document processing first (`/sources` → process, or `batch_runner`). Docling
  processing is CPU OCR/parse, not gpt-oss — acceptable.
- The grace repo at `~/grace` with its `.venv`.

## Do this
```bash
cd ~/grace
.venv/bin/python ~/grace-claude-skills/scripts/export_corpus.py
# scoped:
.venv/bin/python ~/grace-claude-skills/scripts/export_corpus.py --domain legal --domain insurance
# tighter budget if a domain is huge:
.venv/bin/python ~/grace-claude-skills/scripts/export_corpus.py --max-chars 120000
```
Output lands in `~/grace/workspace/corpus/`:
- `<domain>.md` — one bundle per domain (header lists doc count + filenames).
- `manifest.json` — domains, doc/char counts, file lists.

## Coverage guarantee
The script calls grace's own `build_balanced_document_text`, so **every** document in
a domain gets an equal slice of the char budget (head/middle/tail sampled for long
docs). This is the "docs, not top-10" grounding fix from the 2026-06-09 session — it
prevents short documents from being crowded out by a few long ones.

## Hand-off
Attach/paste the relevant `<domain>.md` files into Claude and invoke
**grace-cq-authoring** (next step). For ontology proposal you will reuse the same
bundles plus the canonical CQ set.

## Heat / safety
- Reads Postgres only. Does NOT load gpt-oss. Keep the model unloaded (`ollama stop`).
- Reads the live `grace` DB (correct — this is real onboarding, not a test).
