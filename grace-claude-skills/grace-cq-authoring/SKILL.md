---
name: grace-cq-authoring
description: >
  STEP 2 of the Claude-as-LLM onboarding flow. Claude reads an exported GrACE corpus
  bundle and authors Competency Questions (CQs) directly — replacing the local
  gpt-oss CQ-generation pass. Produces a cqs.json that import_cqs.py loads into the
  live pipeline so the native CQ merge + ontology proposal + review screen all work.
  Use when the operator wants Claude, not Ollama, to generate CQs.
---

# grace-cq-authoring

## Role
You (Claude) are the CQ generator. You replace GrACE's native `cq_generator` (which
calls gpt-oss). You read the corpus, think across perspectives, and emit a JSON list
of CQs. A helper script then validates and persists them.

## Inputs
- Corpus bundle(s) from **grace-corpus-export** (`~/grace/workspace/corpus/<domain>.md`).
- Method reference: `references/cq-authoring-method.md` (read it — it encodes the
  "combined / A3" multi-perspective method GrACE itself uses).

## What a CQ is (and is NOT)
- A CQ is a question the **ontology schema** must be able to answer. It shapes types,
  relationships, and properties. It is **not** a graph node/edge and not a fact lookup.
- Good CQs force structure: relationship CQs imply edges; metaproperty CQs imply
  properties; validating CQs imply integrity constraints / required links.

## Method (summary — full detail in references/cq-authoring-method.md)
Author CQs in ONE combined pass per domain, covering four perspectives:
1. **Top-down** — the big entity classes and their obvious relationships.
2. **Bottom-up** — specifics visible in the documents (named fields, dates, amounts).
3. **Middle-out** — cross-document / cross-domain links (e.g. policy → insured entity).
4. **Negative evidence** — integrity checks: "does every X reference a Y?".

Aim for a **compact canonical set**, not exhaustive variants. Target roughly
**20–30 high-value CQs per domain** — the native merge will still collapse near-dupes,
but you should not hand it 200 redundant questions. Prefer one well-phrased CQ over
five rephrasings of the same need.

## Output format
Emit a JSON list matching `templates/cqs.example.json`. Per item:
- `canonical_text` (REQUIRED) — the question.
- `cq_type` — one of SCOPING | VALIDATING | FOUNDATIONAL | RELATIONSHIP | METAPROPERTY | UNCLASSIFIED.
- `domain` — the corpus domain.
- `priority` — HIGH | MEDIUM | LOW | UNSET.
- `rationale` — one line on what schema element it forces (stored in metadata, aids review).
- `evidence_files` — filenames from the corpus header that ground the CQ (optional;
  resolved to `linked_document_ids` on import).
- `confidence` — your 0–1 confidence (optional).

Write it to `~/grace/workspace/cqs.json`.

## Persist (no LLM)
```bash
cd ~/grace
.venv/bin/python ~/grace-claude-skills/scripts/import_cqs.py --in ./workspace/cqs.json --dry-run   # validate
.venv/bin/python ~/grace-claude-skills/scripts/import_cqs.py --in ./workspace/cqs.json             # write
```
Each row is tagged `source=HUMAN_AUTHORED` (operator-curated — you review before
import) with `metadata_extra.authoring_method="combined-a3"` for audit.

## Do NOT run the CQ merge on the Claude path
The merge (`/api/discovery/merge-cqs`) exists to dedup/compress the redundant output of
weak local models (4 blind passes → 200+ near-duplicate CQs). Claude authors one
combined, globally-deduped, type-classified pass, so there is **nothing to merge** — on
this run the merge collapsed almost nothing (28 CQs → mostly singletons). Skipping it
removes a stage, the Tier-3 gpt-oss heat, and the domain-scoping/cluster-FK friction.

Go straight to **grace-ontology-proposal**. Coverage (which CQ each type answers) is
computed there, heat-free, by `map_coverage.py` — that was the only useful thing the
merge produced, and it belongs at propose time.

> The native merge stays valid for the local-LLM onboarding path; it is just not used
> when Claude is the LLM. (Optional: run an embedding-only dedup ad hoc if you ever bulk
> import CQs from a weaker source.)

## Heat / safety
- The authoring is Claude's own inference — no local model. Keep gpt-oss unloaded.
- import_cqs.py writes the live `grace` DB (intended). Never run it against grace_test.
