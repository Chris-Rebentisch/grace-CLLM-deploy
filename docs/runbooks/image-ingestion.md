# Image ingestion runbook (Chunk 77b)

Operator guide for photo visual-understanding jobs (`job_kind='image'`).

## Prerequisites

- FastAPI on `localhost:8000`, Postgres `grace`, ArcadeDB on `localhost:2480`
- For local vision: `ollama pull qwen2.5-vl:7b` (dev) or `qwen2.5-vl:32b` (Mac Studio)
- `config/discovery.yaml` → `llm.vision.enabled: true` and `image_ingestion.document_chunk_mode`

## Airgapped (default)

1. Set `airgap_mode: true` in `config/discovery.yaml`
2. Submit job: `POST /api/extraction/jobs` with `{"job_kind":"image","source_path":"<path under allowlist>"}`
3. Pipeline CLI: `python -m src.extraction.image_pipeline --job-id <UUID> --source-path <path>`
4. Verify `Image_Asset` in ArcadeDB Studio; idempotent re-run on same bytes is a no-op (`content_sha256`)

## Cloud vision opt-in

1. Set `airgap_mode: false` and configure cloud provider in `llm` section
2. Cost-budget gate applies for cloud batch paths (see extraction routes)
3. PII-dense images still route to local vision per D503 sensitivity strategy

## Observability

- Metrics: `grace_image_assets_ingested_total`, `grace_vision_calls_total`, `grace_vision_call_duration_seconds`
- Job status: `GET /api/extraction/jobs/{job_id}`

## Troubleshooting

- **422 airgap conflict:** cloud provider selected while `airgap_mode=true`
- **Vision skipped in CI:** tests marked `requires_vision` auto-skip unless `GRACE_REQUIRE_SERVICES=1`
