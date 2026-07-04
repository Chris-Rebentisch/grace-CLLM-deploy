# Voice Card Export — Operator Runbook

**Chunk 78, D505/D506.** CLI-only export of portable Voice Cards in 4 formats.

## Export CLI Usage

```bash
# Markdown (default)
python -m src.ingestion.communications.voice_tone export --person alice@corp.com

# Claude Skill format
python -m src.ingestion.communications.voice_tone export --person alice@corp.com --format claude-skill

# Claude Style (paste-ready block)
python -m src.ingestion.communications.voice_tone export --person alice@corp.com --format claude-style

# JSON canonical record
python -m src.ingestion.communications.voice_tone export --person alice@corp.com --format json

# Segment (aggregate) export
python -m src.ingestion.communications.voice_tone export --segment legal-team --format markdown

# Custom output directory
python -m src.ingestion.communications.voice_tone export --person alice@corp.com --out /tmp/export
```

## DPIA Requirements

| Mode | DPIA Required | Notes |
|------|---------------|-------|
| Individual (`--person`) | Yes | Active DPIA attestation in `data/dpia-attestations/` required |
| Aggregate (`--segment`) | No | Segment-level analytics allowed unconditionally |

Individual exports without a valid DPIA attestation are refused with a structured log warning.

## PII Redaction

All exemplar text is PII-redacted before export emission (D506):

- **Layer 1 (always-on):** Regex patterns for emails → `[EMAIL]`, phone numbers → `[PHONE]`, postal addresses → `[ADDRESS]`, policy/claim IDs → `[CLAIM_ID]`.
- **Layer 2 (best-effort):** Local-only NER via Ollama for person names → `[PERSON]` and organization names → `[ORG]`. Falls back silently to Layer 1 only if Ollama is unavailable.

Cloud NER providers are blocked — `redaction_ner_provider` is always `local_only` (D506/D138).

## Asset Directory Layout

```
data/voice-profiles/
  alice@corp.com/
    voice-card.md          # markdown format
    voice-card.txt         # claude-style format
    voice-card.json        # json format
```

Export audit rows are persisted in the `voice_card_exports` table (append-only, c78a migration).

## Cloud Synthesis Gating

NL tone synthesis uses `get_provider()` which respects `airgap_mode` in `config/discovery.yaml`. When `airgap_mode=true`, only local Ollama is used for synthesis. The `synthesis_provider_override` config key can select a specific provider for synthesis only.

## Configuration

Config keys in `config/voice_tone_config.yaml`:

| Key | Default | Description |
|-----|---------|-------------|
| `synthesis_provider_override` | `null` | Override global LLM for V&T synthesis |
| `baseline_corpus_source` | `org_corpus` | Baseline source for contrastive markers |
| `export_default_dir` | `data/voice-profiles` | Default export root directory |
| `redaction_enabled` | `true` | Mandatory PII redaction for exports |
| `redaction_ner_provider` | `local_only` | NER provider (always local) |
| `voice_card_core_word_limit` | `400` | Core card word limit |
