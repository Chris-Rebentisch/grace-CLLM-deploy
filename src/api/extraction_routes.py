"""Extraction module API surface (Chunk 34; Chunk 72a D470 extensions).

Seven routes:

* ``POST /api/extraction/mine-sample`` — wraps ``MINESampler.sample_document``
  with a 120s synchronous-await timeout (D255). Idempotent on
  ``document_id`` via the existing dedup at
  ``src.extraction.mine_sampler.MINESampler.sample_document`` lines 404–422.

* ``POST /api/extraction/reconciliation`` — wraps
  ``src.extraction.provenance.reconciliation_check`` directly (D256, B1
  resolution: no filter parameters).

* ``POST /api/extraction/jobs``  — spawn extraction CLI subprocess (D470).
* ``GET  /api/extraction/jobs/{job_id}``  — poll job status (D470).
* ``GET  /api/extraction/jobs``  — list jobs paged (D470).
* ``GET  /api/extraction/events``  — list extraction events paged (D470).
* ``GET  /api/extraction/events/{event_id}``  — single event (D470).

D246 mirror: this module MUST NOT import ``src.discovery.batch_runner``
or ``src.extraction.eval_checkpoint``. CLI spawn via ``subprocess.Popen``
only. CI guard: ``tests/extraction/test_route_invocation_surface.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fastapi.responses import JSONResponse

from src.extraction import provenance
from src.extraction.claim_database import extraction_events_pg, get_extraction_event
from src.extraction.mine_emitter import set_mine_retention_observation
from src.extraction.mine_sampler import MINESampler, MineSampleRow
from src.extraction.router import RouterStrategy, estimate_input_tokens, validate_strategy_implemented
from src.graph.arcade_client import ArcadeClient
from src.graph.config import ArcadeConfig
from src.ontology.database import get_active_version
from src.shared.database import get_db

logger = structlog.get_logger()


router = APIRouter(prefix="/api/extraction", tags=["extraction"])


_DEFAULT_MINE_TIMEOUT_SECONDS = 120
_EVAL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "eval_config.yaml"


def _load_mine_timeout_seconds() -> int:
    """Read ``mine_api.timeout_seconds`` from ``config/eval_config.yaml``.

    Falls back to ``120`` (D255) when the file or key is absent.
    """
    try:
        if not _EVAL_CONFIG_PATH.exists():
            return _DEFAULT_MINE_TIMEOUT_SECONDS
        data = yaml.safe_load(_EVAL_CONFIG_PATH.read_text()) or {}
        block = data.get("mine_api") or {}
        value = block.get("timeout_seconds")
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    except Exception:  # noqa: BLE001 — degrade gracefully to default
        logger.warning("extraction.mine_timeout_load_failed", exc_info=True)
    return _DEFAULT_MINE_TIMEOUT_SECONDS


# --- MINE sample (D255) ----------------------------------------------------


class MineSampleRequest(BaseModel):
    """Request body for ``POST /api/extraction/mine-sample`` (D255)."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID = Field(
        description="processed_documents.id (UUID4) for the document to sample."
    )
    ontology_module: str | None = Field(
        default=None,
        description="Optional active-module hint (does not change the dedup key).",
    )


class MineSampleResponse(BaseModel):
    """Response for the MINE sample route (D255)."""

    retention_score: float
    total_facts: int
    recovered_facts: int
    judgments: list[dict]
    mine_sample_id: UUID


def _build_extraction_client():
    """Construct an ``ExtractionLLMClient`` using the active extraction config.

    Imported lazily so unit tests can monkeypatch the constructor without
    paying ``instructor`` import cost at module load.
    """
    from src.extraction.extraction_config import ExtractionSettings
    from src.extraction.instructor_client import ExtractionLLMClient

    return ExtractionLLMClient(ExtractionSettings())


def _build_arcade_client() -> ArcadeClient:
    # Phase-9 fix: pull from settings so ARCADE_TIMEOUT, ARCADE_HOST
    # etc. are honored (default ArcadeConfig() carries hardcoded 30s).
    from src.shared.config import get_settings

    return ArcadeClient(config=ArcadeConfig.from_settings(get_settings()))


def _build_mine_sampler() -> MINESampler:
    return MINESampler()


@router.post("/mine-sample", response_model=MineSampleResponse)
async def mine_sample(
    request: MineSampleRequest,
    db: Session = Depends(get_db),
) -> MineSampleResponse:
    """Synchronously run MINE retention sampling for a document (D255).

    Returns 504 ``{"detail": "MINE sampling timed out"}`` when the
    ``mine_api.timeout_seconds`` budget elapses.
    """
    timeout = _load_mine_timeout_seconds()

    sampler = _build_mine_sampler()
    arcade_client = _build_arcade_client()
    llm_client = _build_extraction_client()

    try:
        result: dict[str, Any] = await asyncio.wait_for(
            sampler.sample_document(
                request.document_id,
                db,
                llm_client,
                arcade_client,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "extraction.mine_sample.timeout",
            document_id=str(request.document_id),
            timeout=timeout,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="MINE sampling timed out",
        )

    sample_id = result["id"]
    row = db.get(MineSampleRow, sample_id)
    schema_sid = "unknown"
    if row is not None and row.schema_version_id is not None:
        schema_sid = str(row.schema_version_id)
    elif sampler._schema_version_id is not None:
        schema_sid = str(sampler._schema_version_id)

    ontology = (request.ontology_module or "").strip() or "unknown"
    try:
        set_mine_retention_observation(
            ontology_module=ontology,
            schema_version_id=schema_sid,
            retention_ratio=float(result.get("retention_score", 0.0)),
        )
    except Exception:  # noqa: BLE001 — metric path must never fail the route
        logger.warning("extraction.mine_sample.emitter_failed", exc_info=True)

    return MineSampleResponse(
        retention_score=float(result.get("retention_score", 0.0)),
        total_facts=int(result.get("total_facts", 0)),
        recovered_facts=int(result.get("recovered_facts", 0)),
        judgments=list(result.get("judgments") or []),
        mine_sample_id=sample_id,
    )


# --- Reconciliation (D256, B1) --------------------------------------------


class ReconciliationResponse(BaseModel):
    """Response for the reconciliation route (D256)."""

    promoted: int
    warnings: int
    checked: int


@router.post("/reconciliation", response_model=ReconciliationResponse)
async def reconciliation(
    db: Session = Depends(get_db),
) -> ReconciliationResponse:
    """Wrap ``provenance.reconciliation_check(client, session)`` directly (D256).

    Body is empty ``{}`` (B1: no filter parameters).
    """
    arcade_client = _build_arcade_client()
    result = await provenance.reconciliation_check(arcade_client, db)
    return ReconciliationResponse(
        promoted=int(result.get("promoted", 0)),
        warnings=int(result.get("warnings", 0)),
        checked=int(result.get("checked", 0)),
    )


# ---------------------------------------------------------------------------
# Chunk 72a — Extraction Job routes (D470)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Source-path allowlist defaults (CWE-22 defense).
# Operator extends via GRACE_EXTRACTION_ALLOWED_ROOTS env var (colon-separated).
_DEFAULT_ALLOWED_ROOTS = [
    _REPO_ROOT / "data" / "discovery-sample",
    _REPO_ROOT / "data" / "corpus",
]


def _get_allowed_roots() -> list[Path]:
    """Return the resolved source-path allowlist roots."""
    roots = list(_DEFAULT_ALLOWED_ROOTS)
    extra = os.environ.get("GRACE_EXTRACTION_ALLOWED_ROOTS", "")
    if extra:
        for part in extra.split(":"):
            part = part.strip()
            if part:
                roots.append(Path(part).resolve())
    return roots


def _validate_source_path(source_path: str) -> Path:
    """Resolve and validate source_path against the allowlist.

    Rejects traversal attempts, symlinks resolving outside allowed roots,
    and non-existent paths. Returns the resolved Path on success.
    """
    try:
        resolved = Path(source_path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Source path does not exist or is not accessible: {exc}",
        )

    allowed = _get_allowed_roots()
    for root in allowed:
        try:
            if resolved.is_relative_to(root):
                return resolved
        except (TypeError, ValueError):
            continue

    raise HTTPException(
        status_code=422,
        detail=(
            f"Source path {str(resolved)!r} is outside the allowlisted roots. "
            f"Allowed: {[str(r) for r in allowed]}"
        ),
    )


def _estimate_cost(total_bytes: int, provider: str, model: str) -> float:
    """Text-only token estimator with 1.3x safety multiplier (spec R1).

    Returns estimated cost in USD (very rough).
    """
    estimated_tokens = total_bytes / 4 * 1.3
    # Rough per-token cost estimate: $0.001 per 1000 tokens as a conservative default
    cost_per_1k = 0.001
    return estimated_tokens / 1000 * cost_per_1k


# Cloud provider names that require a cost budget for batch jobs.
_CLOUD_PROVIDERS = frozenset({"anthropic", "openai", "deepseek", "groq", "xai", "together"})

# File-size hard cap for single-document jobs (5MB).
_MAX_DOCUMENT_SIZE_BYTES = 5 * 1024 * 1024

# F-0008 / ISS-0041: suffixes eval_checkpoint can read DIRECTLY as plain
# text (no Docling path in that CLI). Binary-format follow-up (ISS-0041
# addendum): other suffixes (.pdf/.docx/.xlsx/.pptx) are now ACCEPTED when a
# processed_documents row with extracted text exists for the resolved path —
# the CLI is spawned with --from-processed-doc and sources the text from
# processed_documents.extracted_text (Docling batch output). Without such a
# row the route still 422s, fast, with batch-runner guidance — better than a
# dead subprocess. Mirrors SUPPORTED_DOC_SUFFIXES in
# src/extraction/eval_checkpoint.py — duplicated, not imported, because the
# D246 route-isolation guard forbids importing eval_checkpoint here.
_SUPPORTED_DOCUMENT_SUFFIXES = (".txt", ".md")


def _processed_document_text_available(db: Session, resolved_path: Path) -> bool:
    """Best-effort pre-check: does a processed_documents row with extracted
    text exist for this resolved path?

    F-0008 / ISS-0041 (binary-format follow-up): the route already holds a DB
    session (it writes extraction_jobs rows), so failing fast here at request
    time beats spawning a subprocess doomed to exit 1. Raw SQL (route style)
    rather than the discovery ORM keeps the import surface minimal. The
    lookup key matches ``process_document``'s ``Path(file_path).resolve()``
    storage convention (src/discovery/document_processor.py) — the route's
    ``_validate_source_path`` output is the same resolved form.
    """
    row = db.execute(
        text(
            "SELECT extracted_text FROM processed_documents "
            "WHERE file_path = :fp"
        ),
        {"fp": str(resolved_path)},
    ).first()
    return bool(row is not None and (row.extracted_text or "").strip())

# D502 (Chunk 77b): File-size hard cap for image jobs (20MB).
_MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024

# Stale heartbeat threshold (30 minutes).
_STALLED_THRESHOLD_SECONDS = 30 * 60

# In-flight job tracking for 409 concurrent-trigger protection.
# Key: canonical resolved source path string.
_IN_FLIGHT_JOBS: dict[str, UUID] = {}


def _job_heartbeat_age_seconds(row) -> float | None:
    """Return the age (in seconds) of a job's last heartbeat, or None if unknown.

    Reuses the D470 stalled concept: heartbeat is the progress_json
    ``last_tick_at`` when present, else ``started_at``, else ``created_at``.
    """
    last_tick = None
    progress = getattr(row, "progress_json", None)
    if progress and isinstance(progress, dict):
        last_tick_str = progress.get("last_tick_at")
        if last_tick_str:
            try:
                last_tick = datetime.fromisoformat(last_tick_str)
            except (ValueError, TypeError):
                last_tick = None
    if last_tick is None:
        last_tick = getattr(row, "started_at", None) or getattr(row, "created_at", None)
    if last_tick is None:
        return None
    if hasattr(last_tick, "tzinfo") and last_tick.tzinfo is None:
        from datetime import timezone

        last_tick = last_tick.replace(tzinfo=timezone.utc)
    return (datetime.now(UTC) - last_tick).total_seconds()


class ExtractionJobCreateRequest(BaseModel):
    """Request body for POST /api/extraction/jobs (D470; D471 adds router_strategy)."""

    model_config = ConfigDict(extra="forbid")

    job_kind: str = Field(description="'document' or 'batch'")
    source_path: str = Field(description="Absolute or relative path to file/directory")
    provider: str | None = Field(default=None, description="LLM provider override")
    model: str | None = Field(default=None, description="LLM model override")
    cost_budget_usd: float | None = Field(default=None, description="Maximum cost in USD for cloud batch")
    router_strategy: str | None = Field(default=None, description="Routing strategy: sensitivity | size_tier")


class ExtractionJobResponse(BaseModel):
    """Response shape for extraction job endpoints (D470)."""

    job_id: str
    job_kind: str | None = None
    source_path: str | None = None
    status: str | None = None
    pid: int | None = None
    progress_json: dict | None = None
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    created_by: str | None = None
    provider: str | None = None
    model: str | None = None
    stalled: bool | None = None


class ExtractionJobListResponse(BaseModel):
    """Paginated list of extraction jobs."""

    items: list[ExtractionJobResponse]
    next_cursor: str | None = None


def _wait_and_clear_inflight(source_key: str, proc: subprocess.Popen) -> None:
    """Wait for spawned CLI to exit, then release per-source lock."""
    try:
        proc.wait()
    finally:
        _IN_FLIGHT_JOBS.pop(source_key, None)


# D473: resolve active ontology as materialized JSON Schema file for --schema.
_ONTOLOGY_CACHE_DIR = Path.home() / ".cache" / "grace" / "ontology"


def _resolve_active_ontology_json(session: Session) -> Path:
    """Materialize the active ratified ontology as a JSON Schema file.

    D473: ``eval_checkpoint.py --schema`` requires a JSON Schema file, not
    ``config/discovery.yaml``. This helper reads the active ontology from
    ``get_active_version(session)`` and writes it to
    ``~/.cache/grace/ontology/{version.id}.json`` via atomic ``os.replace``.
    Cache-hit path returns immediately (ratified schemas are immutable).

    Raises ``HTTPException(422)`` when no active ontology version exists.
    """
    version = get_active_version(session)
    if version is None:
        raise HTTPException(
            status_code=422,
            detail="No active ontology version — cannot spawn extraction job",
        )

    _ONTOLOGY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _ONTOLOGY_CACHE_DIR / f"{version.id}.json"
    if cache_path.exists():
        return cache_path

    # Atomic write: tmp file then rename
    tmp_path = cache_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(version.schema_json, indent=2))
    os.replace(str(tmp_path), str(cache_path))
    return cache_path


def _build_extraction_argv(
    job_kind: str,
    job_id: UUID,
    schema_path: Path,
    source_path: Path,
    *,
    provider: str | None = None,
    model: str | None = None,
    router_strategy: str | None = None,
    from_processed_doc: bool = False,
) -> list[str]:
    """Build the CLI argv list for extraction job spawn.

    D476: pure function extracted for CLI argv contract testing.
    D473: document-job uses materialized JSON Schema path (not YAML).
    D474: --provider/--model only appended for document jobs.
    F-0008 / ISS-0041 (binary-format follow-up): ``from_processed_doc=True``
    appends ``--from-processed-doc`` so eval_checkpoint sources text from
    ``processed_documents.extracted_text`` instead of reading the (binary)
    file as plain text. Document jobs only.
    """
    if job_kind == "image":
        # D502 (Chunk 77b): image pipeline CLI spawn.
        # D246: route-isolation — MUST NOT import image_pipeline.
        # Authorization: D502.
        cmd = [
            sys.executable, "-m", "src.extraction.image_pipeline",
            "--job-id", str(job_id),
            "--source-path", str(source_path),
        ]
        return cmd
    elif job_kind == "document":
        # F-0008 / ISS-0041: pass the requested file itself via --doc-file.
        # The old argv (--doc-dir <parent> --sample-count 1) discarded the
        # requested filename — eval_checkpoint extracted sorted(dir)[:1],
        # i.e. the alphabetically-first .txt/.md in the directory.
        # F-0009 / ISS-0041: --persist runs the pipeline with a real DB
        # session so claims/events persist — this makes the route an honest
        # document-extraction API instead of a metrics-only eval run
        # (session=None). Directory fallback kept for defensive callers of
        # this pure function; the route enforces is_file() for document jobs.
        if source_path.is_file():
            doc_args = ["--doc-file", str(source_path)]
        else:
            doc_args = ["--doc-dir", str(source_path), "--sample-count", "1"]
        if from_processed_doc:
            # F-0008 / ISS-0041 (binary-format follow-up): binary suffix with
            # a processed_documents row — text is served from
            # extracted_text, not Path.read_text().
            doc_args.append("--from-processed-doc")
        cmd = [
            sys.executable, "-m", "src.extraction.eval_checkpoint",
            "--schema", str(schema_path),
            *doc_args,
            "--persist",
            "--job-id", str(job_id),
        ]
        # D474: --provider/--model are eval_checkpoint flags, not batch_runner flags
        if provider:
            cmd.extend(["--provider", provider])
        if model:
            cmd.extend(["--model", model])
    else:
        cmd = [
            sys.executable, "-m", "src.discovery.batch_runner",
            "--source-dir", str(source_path),
            "--job-id", str(job_id),
        ]
        # D474: batch_runner does NOT accept --provider or --model
        if router_strategy:
            cmd.extend(["--router-strategy", router_strategy])
    return cmd


# D475: per-job logfile directory
_LOG_DIR = Path.home() / ".grace" / "logs"


def _row_to_job_response(row) -> ExtractionJobResponse:
    """Convert a DB row to ExtractionJobResponse."""
    # Compute stalled: 30-min heartbeat loss (informational only, spec R6)
    stalled = False
    if row.status == "running":
        last_tick = None
        if row.progress_json and isinstance(row.progress_json, dict):
            last_tick_str = row.progress_json.get("last_tick_at")
            if last_tick_str:
                try:
                    last_tick = datetime.fromisoformat(last_tick_str)
                except (ValueError, TypeError):
                    pass
        if last_tick is None:
            last_tick = row.started_at or row.created_at
        if last_tick:
            if hasattr(last_tick, "tzinfo") and last_tick.tzinfo is None:
                from datetime import timezone
                last_tick = last_tick.replace(tzinfo=timezone.utc)
            age = (datetime.now(UTC) - last_tick).total_seconds()
            stalled = age > _STALLED_THRESHOLD_SECONDS

    return ExtractionJobResponse(
        job_id=str(row.job_id),
        job_kind=row.job_kind,
        source_path=row.source_path,
        status=row.status,
        pid=row.pid,
        progress_json=row.progress_json if row.progress_json else {},
        error_message=row.error_message,
        started_at=row.started_at.isoformat() if row.started_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
        created_at=row.created_at.isoformat() if row.created_at else None,
        created_by=row.created_by,
        provider=row.provider,
        model=row.model,
        stalled=stalled,
    )


@router.post("/jobs", status_code=202)
def create_extraction_job(
    request: ExtractionJobCreateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Spawn an extraction CLI subprocess and return 202 with job_id (D470).

    D246 mirror: does NOT import batch_runner or eval_checkpoint.
    Subprocess spawn via subprocess.Popen only.
    """
    # D502 (Chunk 77b): widened to accept 'image' alongside 'document' and 'batch'.
    if request.job_kind not in ("document", "batch", "image"):
        raise HTTPException(status_code=422, detail="job_kind must be 'document', 'batch', or 'image'")

    resolved_path = _validate_source_path(request.source_path)

    # Router strategy pre-validation (D471): catch NotImplementedError → 422
    if request.router_strategy:
        try:
            strategy = RouterStrategy(request.router_strategy)
        except ValueError:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": f"Unknown router strategy '{request.router_strategy}'",
                    "strategy": request.router_strategy,
                },
            )
        try:
            validate_strategy_implemented(strategy)
        except NotImplementedError:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": f"Strategy '{strategy.value}' is not yet implemented (deferred to 72c)",
                    "strategy": strategy.value,
                },
            )

    # File-size cap for single-document jobs (5MB)
    from_processed_doc = False
    if request.job_kind == "document":
        if not resolved_path.is_file():
            raise HTTPException(status_code=422, detail="source_path must be a file for job_kind='document'")
        # F-0008 / ISS-0041 (binary-format follow-up): non-plain-text
        # suffixes are accepted when the Docling batch has already persisted
        # extracted text for this exact path (processed_documents, keyed on
        # UNIQUE(file_path)) — the spawn then carries --from-processed-doc.
        # Missing row → fail fast HERE with batch-runner guidance instead of
        # spawning a subprocess doomed to exit 1. .txt/.md behavior is
        # byte-identical to before (no DB lookup, direct plain-text read).
        if resolved_path.suffix.lower() not in _SUPPORTED_DOCUMENT_SUFFIXES:
            if _processed_document_text_available(db, resolved_path):
                from_processed_doc = True
            else:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Unsupported document suffix {resolved_path.suffix!r} for "
                        f"direct plain-text extraction (supported: "
                        f"{', '.join(_SUPPORTED_DOCUMENT_SUFFIXES)}) and no "
                        "processed_documents row with extracted text exists for "
                        "this path. Binary formats (.pdf/.docx/.xlsx/.pptx) are "
                        "served from processed_documents.extracted_text — run the "
                        "Docling batch first: python -m src.discovery.batch_runner "
                        f"--source-dir {resolved_path.parent}"
                    ),
                )
        # F-0008 / ISS-0041 (binary-format follow-up): the 5MB cap guards the
        # subprocess's Path.read_text() memory; in from-processed-doc mode
        # the file is never read (extracted_text is what Docling already
        # persisted), so the raw binary size — often >5MB for scanned PDFs —
        # is irrelevant and the cap does not apply.
        if not from_processed_doc and resolved_path.stat().st_size > _MAX_DOCUMENT_SIZE_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"File exceeds 5MB limit ({resolved_path.stat().st_size} bytes)",
            )

    # D502 (Chunk 77b): File-size cap for image jobs (20MB).
    # D502: widened job surface for image processing. D246: route-isolation
    # maintained — MUST NOT import image_pipeline. D470: reuses extraction-jobs
    # lifecycle. Authorization: D502.
    if request.job_kind == "image":
        if not resolved_path.is_file():
            raise HTTPException(status_code=422, detail="source_path must be a file for job_kind='image'")
        if resolved_path.stat().st_size > _MAX_IMAGE_SIZE_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"Image file exceeds maximum size of {_MAX_IMAGE_SIZE_BYTES} bytes",
            )

    # Cost-budget gate for cloud batch — uses tiered estimator (D471)
    provider = request.provider or "ollama"
    if request.job_kind == "batch" and provider.lower() in _CLOUD_PROVIDERS:
        # Compute tiered token estimate
        if resolved_path.is_file():
            token_count, confidence = estimate_input_tokens(resolved_path)
        elif resolved_path.is_dir():
            total_tokens = 0
            worst_confidence = "high"
            confidence_rank = {"high": 0, "medium": 1, "low": 2}
            for f in resolved_path.rglob("*"):
                if f.is_file():
                    t, c = estimate_input_tokens(f)
                    total_tokens += t
                    if confidence_rank.get(c, 2) > confidence_rank.get(worst_confidence, 0):
                        worst_confidence = c
            token_count = total_tokens
            confidence = worst_confidence
        else:
            token_count = 0
            confidence = "low"
        estimated_cost = _estimate_cost(token_count * 4, provider, request.model or "")

        if request.cost_budget_usd is None:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "cost_budget_usd required for cloud-provider batch jobs",
                    "estimated_input_tokens": token_count,
                    "confidence": confidence,
                    "estimated_cost_usd": round(estimated_cost, 4),
                    "budget_usd": None,
                },
            )
        if estimated_cost > request.cost_budget_usd:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "Estimated cost exceeds budget",
                    "estimated_input_tokens": token_count,
                    "confidence": confidence,
                    "estimated_cost_usd": round(estimated_cost, 4),
                    "budget_usd": request.cost_budget_usd,
                },
            )

    job_id = uuid4()
    source_key = str(resolved_path)

    # Concurrent-trigger race protection: SELECT FOR UPDATE on canonical source path
    existing = db.execute(
        text(
            "SELECT job_id, status, progress_json, started_at, created_at "
            "FROM extraction_jobs "
            "WHERE source_path = :sp AND status NOT IN ('completed', 'failed', 'cancelled') "
            "FOR UPDATE"
        ),
        {"sp": source_key},
    ).first()
    if existing:
        # F-13 (validation run): a stuck 'pending'/'running' job used to
        # deadlock re-submission forever. Reuse the D470 30-min stalled
        # threshold as an escape hatch — if the existing non-terminal job's
        # heartbeat is older than the threshold, treat it as re-spawnable
        # (mark it failed and let the new job proceed) instead of hard-409.
        age = _job_heartbeat_age_seconds(existing)
        if age is not None and age > _STALLED_THRESHOLD_SECONDS:
            db.execute(
                text(
                    "UPDATE extraction_jobs "
                    "SET status='failed', error_message=:em, completed_at=now() "
                    "WHERE job_id=:jid"
                ),
                {
                    "em": "superseded: stalled job re-spawned after heartbeat timeout",
                    "jid": str(existing.job_id),
                },
            )
            _IN_FLIGHT_JOBS.pop(source_key, None)
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Non-terminal job already exists for {source_key}: {existing.job_id}",
            )

    # Also check in-flight memory (skip when the DB row above was stalled-cleared)
    if source_key in _IN_FLIGHT_JOBS:
        raise HTTPException(
            status_code=409,
            detail=f"Job already in flight for {source_key}",
        )

    # Insert job row
    db.execute(
        text(
            "INSERT INTO extraction_jobs "
            "(job_id, job_kind, source_path, status, created_at, created_by, provider, model, cost_budget_usd) "
            "VALUES (:jid, :jk, :sp, 'pending', now(), :cb, :prov, :mod, :cbu)"
        ),
        {
            "jid": str(job_id),
            "jk": request.job_kind,
            "sp": source_key,
            "cb": os.environ.get("GRACE_AGENT_ID", "api"),
            "prov": provider,
            "mod": request.model,
            "cbu": request.cost_budget_usd,
        },
    )
    db.commit()

    # D473: resolve active ontology as JSON Schema for --schema
    # D502: image jobs don't need ontology schema — skip resolution
    schema_path = Path("/dev/null")  # placeholder for image jobs
    if request.job_kind != "image":
        schema_path = _resolve_active_ontology_json(db)

    # D476: build argv via pure function for contract-test coverage
    cmd = _build_extraction_argv(
        job_kind=request.job_kind,
        job_id=job_id,
        schema_path=schema_path,
        source_path=resolved_path,
        provider=request.provider,
        model=request.model,
        router_strategy=request.router_strategy if request.job_kind == "batch" else None,
        # F-0008 / ISS-0041 (binary-format follow-up): only ever True for
        # document jobs (set in the validation branch above).
        from_processed_doc=from_processed_doc,
    )

    # D475: per-job logfile — replace stderr=subprocess.DEVNULL with capture
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile_path = _LOG_DIR / f"extraction-job-{job_id}.log"

    try:
        logfile = open(logfile_path, "w")  # noqa: SIM115 — intentional long-lived fd
        proc = subprocess.Popen(  # noqa: S603 — known argv; not user-editable shell
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=logfile,
            start_new_session=True,
            cwd=str(_REPO_ROOT),
        )
    except Exception as exc:
        db.execute(
            text("UPDATE extraction_jobs SET status='failed', error_message=:em WHERE job_id=:jid"),
            {"em": str(exc), "jid": str(job_id)},
        )
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to spawn CLI: {exc}")

    # Update PID
    db.execute(
        text("UPDATE extraction_jobs SET pid=:pid WHERE job_id=:jid"),
        {"pid": proc.pid, "jid": str(job_id)},
    )
    db.commit()

    _IN_FLIGHT_JOBS[source_key] = job_id

    # Background thread to release lock when child exits
    t = threading.Thread(
        target=_wait_and_clear_inflight,
        args=(source_key, proc),
        daemon=True,
    )
    t.start()

    # Emit OTel counter (best-effort)
    try:
        from src.analytics.metrics import grace_extraction_jobs_started_total
        grace_extraction_jobs_started_total.add(1, {"job_kind": request.job_kind})
    except Exception:  # noqa: BLE001
        pass

    return {"job_id": str(job_id), "status": "pending", "pid": proc.pid}


@router.get("/jobs/{job_id}", response_model=ExtractionJobResponse)
def get_extraction_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> ExtractionJobResponse:
    """Get extraction job status with computed stalled field (D470)."""
    try:
        UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job_id format")

    row = db.execute(
        text("SELECT * FROM extraction_jobs WHERE job_id = :jid"),
        {"jid": job_id},
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _row_to_job_response(row)


@router.get("/jobs")
def list_extraction_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ExtractionJobListResponse:
    """List extraction jobs with optional status filter and cursor pagination (D470)."""
    offset = 0
    if cursor:
        try:
            import base64, json as _json
            decoded = base64.urlsafe_b64decode(cursor.encode("ascii"))
            data = _json.loads(decoded)
            offset = int(data.get("o", 0))
        except Exception:
            offset = 0

    query = "SELECT * FROM extraction_jobs"
    params: dict[str, Any] = {}
    if status_filter:
        query += " WHERE status = :sf"
        params["sf"] = status_filter
    query += " ORDER BY created_at DESC LIMIT :lim OFFSET :off"
    params["lim"] = limit + 1
    params["off"] = offset

    rows = db.execute(text(query), params).fetchall()
    has_more = len(rows) > limit
    items = [_row_to_job_response(r) for r in rows[:limit]]

    next_cursor = None
    if has_more:
        import base64, json as _json
        next_cursor = base64.urlsafe_b64encode(
            _json.dumps({"o": offset + limit}).encode()
        ).decode("ascii")

    return ExtractionJobListResponse(items=items, next_cursor=next_cursor)


# --- Extraction Events routes (D470) ---


@router.get("/events")
def list_extraction_events(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """List extraction events from extraction_events_pg with cursor pagination (D470)."""
    offset = 0
    if cursor:
        try:
            import base64, json as _json
            decoded = base64.urlsafe_b64decode(cursor.encode("ascii"))
            data = _json.loads(decoded)
            offset = int(data.get("o", 0))
        except Exception:
            offset = 0

    stmt = (
        select(extraction_events_pg)
        .order_by(extraction_events_pg.c.created_at.desc())
        .limit(limit + 1)
        .offset(offset)
    )
    rows = db.execute(stmt).fetchall()
    has_more = len(rows) > limit

    items = []
    for r in rows[:limit]:
        items.append({
            "event_id": str(r.event_id),
            "batch_id": str(r.batch_id),
            "source_document_id": r.source_document_id,
            "ontology_module": r.ontology_module,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    next_cursor = None
    if has_more:
        import base64, json as _json
        next_cursor = base64.urlsafe_b64encode(
            _json.dumps({"o": offset + limit}).encode()
        ).decode("ascii")

    return {"items": items, "next_cursor": next_cursor}


@router.get("/events/{event_id}")
def get_extraction_event_route(
    event_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Get a single extraction event by ID (D470)."""
    event = get_extraction_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Extraction event not found")
    # Serialize datetime fields
    result = {}
    for k, v in event.items():
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
