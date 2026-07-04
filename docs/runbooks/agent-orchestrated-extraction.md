# Agent-Orchestrated Extraction — Operator Runbook

## 1. Overview

GrACE extraction is accessible through three layers:

- **MCP tools (72a):** 10 tools (5 read-only + 5 writable) in `src/mcp_server/tools_extraction.py`. Agents call `grace_extract_document`, `grace_batch_extract`, `grace_extraction_job_status`, `grace_list_extraction_jobs`, `grace_list_quarantined_claims`, etc.
- **API routes (72a):** `POST /api/extraction/jobs` (202 spawn), `GET /api/extraction/jobs/{job_id}` (poll), `GET /api/extraction/jobs` (list), plus event and claim detail routes.
- **CLI (pre-72a + 72b):** `python -m src.discovery.batch_runner` with `--job-id` for progress tracking and `--router-strategy` for multi-provider routing.

## 2. Single-Document Workflow

1. Agent calls `grace_extract_document(source_path="/path/to/file.pdf")` (or `POST /api/extraction/jobs` with `job_kind: "document"`).
2. Server spawns `eval_checkpoint` subprocess, returns 202 with `job_id`.
3. Agent polls `grace_extraction_job_status(job_id)` until `status: "completed"` or `"failed"`.
4. Agent calls `grace_list_quarantined_claims()` to review extracted claims.
5. Operator reviews claims at `http://localhost:3000/claims`.

**File-size cap:** 5MB per document. Larger files return 422.

## 3. Batch Workflow with Router

1. Agent calls `grace_batch_extract(source_path="/path/to/corpus/", router_strategy="sensitivity")`.
2. Server spawns `batch_runner` with `--router-strategy sensitivity --job-id <uuid>`.
3. Batch runner loads `config/extraction_router.yaml`, routes documents into shards:
   - **Sensitivity strategy:** Privileged documents (matching `config/sensitivity_rules.yaml` patterns) hard-pinned to airgap-eligible provider (Ollama). Non-privileged routed to default cloud provider.
   - **Size-tier strategy:** Small (<50KB) and large (>5MB) to Ollama, medium (50KB-5MB) to cloud.
4. Each shard gets a staging directory (symlinks) under a temp parent.
5. One `eval_checkpoint` subprocess spawned per shard with `start_new_session=True`.
6. Shard PIDs written to `extraction_jobs.shard_pids` JSONB.
7. Aggregate progress in `extraction_jobs.progress_json`.
8. All shards complete: parent job status flips to `completed`.

**Example CLI:**

```bash
python -m src.discovery.batch_runner \
  --source-dir data/corpus/ \
  --job-id $(python3 -c "import uuid; print(uuid.uuid4())") \
  --router-strategy sensitivity
```

## 4. Cost-Budget Gate

Cloud-provider batch jobs require `cost_budget_usd` in the request body.

**Pre-flight estimation:** The tiered estimator computes token counts with confidence levels:
- Text/Markdown/CSV: `max(byte_size/4, char_count/4) * 1.3` — confidence `"high"`
- PDF/DOCX/XLSX/PPTX: `page_estimate * 2500 * 1.3` — confidence `"medium"` or `"low"`

**Structured 422 response** when budget is exceeded:
```json
{
  "detail": "Estimated cost exceeds budget",
  "estimated_input_tokens": 125000,
  "confidence": "medium",
  "estimated_cost_usd": 0.1625,
  "budget_usd": 0.10
}
```

**Dry-run mode:** Use `dry_run: true` in the MCP tool or `--dry-run` on the CLI to see estimates without executing.

## 5. Ctrl-C / SIGTERM Behavior

When a multi-shard batch receives SIGINT or SIGTERM:

1. Handler iterates `shard_pids`, calls `os.killpg(pgid, SIGTERM)` for each.
2. Waits 5 seconds for graceful shutdown.
3. Escalates to `os.killpg(pgid, SIGKILL)` for any surviving processes.
4. Cleans up temp shard staging directory via `shutil.rmtree`.
5. Flips `extraction_jobs.status` to `cancelled`.
6. Exits with code 128 + signal number.

Single-provider (non-router) batch runs do not register the process-group handler.

## 6. Troubleshooting

### Stalled jobs
Jobs are flagged `stalled: true` after 30 minutes without a `progress_json.last_tick_at` update. This is informational only — the subprocess may still be running. Check the PID:

```bash
ps -p <pid> -o pid,state,etime,command
```

### Orphaned shards
If the parent batch_runner process crashed without cleanup, shard subprocesses may still be running. Find them:

```bash
ps aux | grep eval_checkpoint | grep -v grep
```

Kill by process group if needed:

```bash
kill -TERM -<pgid>
```

### Orphaned temp directories
Shard staging directories are created under `/tmp/grace_shard_*`. After a crash, clean up manually:

```bash
rm -rf /tmp/grace_shard_*
```

### Source-path allowlist errors
If the API returns 422 with "outside the allowlisted roots", verify the source path is under one of:
- `data/discovery-sample/`
- `data/corpus/`
- Any path in `GRACE_EXTRACTION_ALLOWED_ROOTS` env var (colon-separated)
- Any path in `config/extraction_router.yaml` `source_path_allowlist`

### Unimplemented strategy 422
Strategies `parallel_split`, `cost_budget`, and `operator_tag` return 422 with message "not yet implemented (deferred to 72c)". These will be available in Chunk 72c.
