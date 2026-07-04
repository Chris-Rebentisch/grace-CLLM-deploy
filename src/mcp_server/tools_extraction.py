"""Extraction MCP tools (Chunk 72a, CP3, D468).

Ten tools for the extraction workflow:
- Five GET-wrapping read tools (``@readonly_tool``).
- Five POST-wrapping write tools (``@writable_review_tool``).

All include ``@permission_gated_tool``. Static string literal
descriptions (D182). Spawn-and-poll pattern for ``grace_extract_document``
and ``grace_batch_extract`` mirrors the async subprocess lifecycle
in ``extraction_routes.py``.
"""

from __future__ import annotations

import asyncio
import os

from src.mcp_server import http_client
from src.mcp_server.server import (
    mcp,
    permission_gated_tool,
    readonly_tool,
    writable_review_tool,
)


# --------------- Read tools (5 × @readonly_tool) ---------------


@readonly_tool("GET", "/api/extraction/events")
@permission_gated_tool("extraction_event", "global", "view")
async def grace_list_extraction_events(
    cursor: str | None = None,
    limit: int = 25,
) -> dict:
    """List extraction events with cursor pagination. Returns
    paginated items from the extraction_events_pg table. Use cursor
    from the response to fetch subsequent pages."""
    result = await http_client.call(
        "GET",
        "/api/extraction/events",
        tool="grace_list_extraction_events",
        query_params={"cursor": cursor, "limit": limit},
    )
    return result


@readonly_tool("GET", "/api/extraction/events/{event_id}")
@permission_gated_tool("extraction_event", "global", "view")
async def grace_get_extraction_event(
    event_id: str,
) -> dict:
    """Get a single extraction event by its event_id. Returns full
    event details including batch_id, source_document_id, status,
    and timestamps."""
    result = await http_client.call(
        "GET",
        "/api/extraction/events/{event_id}",
        tool="grace_get_extraction_event",
        path_params={"event_id": event_id},
    )
    return result


@readonly_tool("GET", "/api/claims")
@permission_gated_tool("extraction_claim", "global", "view")
async def grace_list_quarantined_claims(
    status: str = "quarantined",
    cursor: str | None = None,
    limit: int = 25,
) -> dict:
    """List quarantined extraction claims with cursor pagination.
    Defaults to status=quarantined. Returns paginated claim items
    with filter chips for verdict, ontology_module, and
    source_document_id."""
    result = await http_client.call(
        "GET",
        "/api/claims",
        tool="grace_list_quarantined_claims",
        query_params={"status": status, "cursor": cursor, "limit": limit},
    )
    return result


@readonly_tool("GET", "/api/claims/{claim_id}")
@permission_gated_tool("extraction_claim", "global", "view")
async def grace_get_claim(
    claim_id: str,
) -> dict:
    """Get a single extraction claim by its claim_id. Returns full
    claim details including subject_name, predicate, object info,
    confidence band, and decision metadata."""
    result = await http_client.call(
        "GET",
        "/api/claims/{claim_id}",
        tool="grace_get_claim",
        path_params={"claim_id": claim_id},
    )
    return result


@readonly_tool("GET", "/api/extraction/jobs/{job_id}")
@permission_gated_tool("extraction_job", "global", "view")
async def grace_extraction_job_status(
    job_id: str,
) -> dict:
    """Get the current status of an extraction job by its job_id.
    Returns job state including status, progress_json, stalled flag,
    and timing information."""
    result = await http_client.call(
        "GET",
        "/api/extraction/jobs/{job_id}",
        tool="grace_extraction_job_status",
        path_params={"job_id": job_id},
    )
    return result


# --------------- Writable review tools (5 × @writable_review_tool) ---------------

_DEFAULT_POLL_INTERVAL = 5  # seconds
_DEFAULT_POLL_TIMEOUT = 600  # seconds


@writable_review_tool("POST", "/api/extraction/jobs")
@permission_gated_tool("extraction_job", "global", "edit")
async def grace_extract_document(
    source_path: str,
    provider: str | None = None,
    cost_budget_usd: float | None = None,
    timeout_seconds: int = _DEFAULT_POLL_TIMEOUT,
) -> dict:
    """Spawn a single-document extraction job and poll until
    completion. Submits the job to POST /api/extraction/jobs with
    job_kind='document', then polls GET /api/extraction/jobs/{job_id}
    at 5-second intervals until the job reaches a terminal status
    (completed or failed). Returns the final job state. The
    timeout_seconds parameter caps total poll time (default 600s)."""
    body: dict = {
        "job_kind": "document",
        "source_path": source_path,
    }
    if provider is not None:
        body["provider"] = provider
    if cost_budget_usd is not None:
        body["cost_budget_usd"] = cost_budget_usd

    result = await http_client.call(
        "POST",
        "/api/extraction/jobs",
        tool="grace_extract_document",
        json_body=body,
    )
    if "code" in result:
        return result

    job_id = result.get("job_id")
    if not job_id:
        return result

    return await _poll_job(job_id, "grace_extract_document", timeout_seconds)


@writable_review_tool("POST", "/api/extraction/jobs")
@permission_gated_tool("extraction_job", "global", "edit")
async def grace_batch_extract(
    source_path: str,
    provider: str | None = None,
    cost_budget_usd: float | None = None,
    timeout_seconds: int = _DEFAULT_POLL_TIMEOUT,
) -> dict:
    """Spawn a batch extraction job over a directory and poll until
    completion. Submits the job to POST /api/extraction/jobs with
    job_kind='batch', then polls GET /api/extraction/jobs/{job_id}
    at 5-second intervals until the job reaches a terminal status
    (completed or failed). Returns the final job state. Cloud
    providers require cost_budget_usd."""
    body: dict = {
        "job_kind": "batch",
        "source_path": source_path,
    }
    if provider is not None:
        body["provider"] = provider
    if cost_budget_usd is not None:
        body["cost_budget_usd"] = cost_budget_usd

    result = await http_client.call(
        "POST",
        "/api/extraction/jobs",
        tool="grace_batch_extract",
        json_body=body,
    )
    if "code" in result:
        return result

    job_id = result.get("job_id")
    if not job_id:
        return result

    return await _poll_job(job_id, "grace_batch_extract", timeout_seconds)


@writable_review_tool("POST", "/api/claims/{claim_id}/accept")
@permission_gated_tool("extraction_claim", "global", "edit")
async def grace_accept_claim(
    claim_id: str,
    reviewer: str | None = None,
    notes: str | None = None,
) -> dict:
    """Accept a quarantined extraction claim and promote it to the
    graph. Optionally provide reviewer name and notes. The claim
    status flips to accepted with decision_source='human' and the
    entity/relationship is written to ArcadeDB."""
    body: dict = {
        "reviewer": reviewer or os.environ.get("GRACE_AGENT_ID", "mcp-user"),
    }
    if notes is not None:
        body["notes"] = notes

    result = await http_client.call(
        "POST",
        "/api/claims/{claim_id}/accept",
        tool="grace_accept_claim",
        path_params={"claim_id": claim_id},
        json_body=body,
    )
    return result


@writable_review_tool("POST", "/api/claims/{claim_id}/reject")
@permission_gated_tool("extraction_claim", "global", "edit")
async def grace_reject_claim(
    claim_id: str,
    reviewer: str | None = None,
    notes: str | None = None,
) -> dict:
    """Reject a quarantined extraction claim. The claim status flips
    to rejected with decision_source='human'. No graph write occurs.
    Optionally provide reviewer name and rejection rationale."""
    body: dict = {
        "reviewer": reviewer or os.environ.get("GRACE_AGENT_ID", "mcp-user"),
    }
    if notes is not None:
        body["notes"] = notes

    result = await http_client.call(
        "POST",
        "/api/claims/{claim_id}/reject",
        tool="grace_reject_claim",
        path_params={"claim_id": claim_id},
        json_body=body,
    )
    return result


@writable_review_tool("POST", "/api/retrieval/build-indexes")
@permission_gated_tool("retrieval_index", "global", "edit")
async def grace_build_retrieval_indexes() -> dict:
    """Trigger a full rebuild of the retrieval indexes (BM25 keyword
    index and semantic embedding index). Returns confirmation when
    the rebuild completes. This is a potentially long-running
    operation."""
    result = await http_client.call(
        "POST",
        "/api/retrieval/build-indexes",
        tool="grace_build_retrieval_indexes",
        json_body={},
    )
    return result


# --------------- Internal helpers ---------------


async def _poll_job(
    job_id: str, tool_name: str, timeout_seconds: int
) -> dict:
    """Poll GET /api/extraction/jobs/{job_id} at 5s intervals until
    the job reaches a terminal status or the timeout expires."""
    elapsed = 0
    while elapsed < timeout_seconds:
        await asyncio.sleep(_DEFAULT_POLL_INTERVAL)
        elapsed += _DEFAULT_POLL_INTERVAL

        result = await http_client.call(
            "GET",
            "/api/extraction/jobs/{job_id}",
            tool=tool_name,
            path_params={"job_id": job_id},
        )
        if "code" in result:
            return result

        status = result.get("status", "")
        if status in {"completed", "failed"}:
            return result

    # Timeout — return last known state with a note
    result = await http_client.call(
        "GET",
        "/api/extraction/jobs/{job_id}",
        tool=tool_name,
        path_params={"job_id": job_id},
    )
    result["_poll_timeout"] = True
    result["_poll_elapsed_seconds"] = elapsed
    return result
