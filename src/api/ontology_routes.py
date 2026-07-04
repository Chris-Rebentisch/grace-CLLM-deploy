"""FastAPI API endpoints for Ontology Management."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.support.refused_routes import no_support_session

from src.ontology.database import (
    get_version_by_number,
)
from src.ontology.diff_engine import compute_entity_level_diff, compute_rfc6902_patch
from src.ontology.models import VersionSource
from src.ontology.schema_store import (
    get_schema_for_module,
    get_version_history,
    ratify_version,
    verify_hash_chain,
)
from src.shared.database import get_db

log = structlog.get_logger()

router = APIRouter(prefix="/api/ontology", tags=["ontology"])


# --- Request Models ---


class RatifyRequest(BaseModel):
    """Request body for ratifying a new ontology version."""

    schema_json: dict
    schema_modules: dict
    source: VersionSource
    reviewer: str | None = None
    changelog: str | None = None
    kgcl_commands: list[str] | None = None
    proposal_id: UUID | None = None
    cq_coverage_snapshot: dict | None = None
    promotion_gate_passed: bool | None = None
    promotion_gate_details: dict | None = None
    # F-0010 / ISS-0046 (additive): optional deployment-level module-name
    # override. Module names are otherwise an emergent property of
    # free-text authoring `domain` strings; when set, the server
    # normalizes every element's domain to this name and recomputes
    # schema_modules before partitioning.
    module_name: str | None = None


# --- Endpoints ---


@router.get("/active")
def get_active_schema(db: Session = Depends(get_db)):
    """Get the currently active ontology version."""
    from src.ontology.database import get_active_version

    version = get_active_version(db)
    if version is None:
        raise HTTPException(status_code=404, detail="No active ontology version exists.")
    return version.model_dump(mode="json")


@router.get("/versions/{version_number}")
def get_version(version_number: int, db: Session = Depends(get_db)):
    """Get a specific ontology version by version number."""
    version = get_version_by_number(db, version_number)
    if version is None:
        raise HTTPException(status_code=404, detail=f"Version {version_number} not found.")
    return version.model_dump(mode="json")


@router.get("/versions")
def list_version_history(
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List version history summaries."""
    return get_version_history(db, limit=limit)


@router.get("/modules/{module_name}")
def get_module_schema(
    module_name: str,
    version_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Get the schema for a specific module."""
    result = get_schema_for_module(db, module_name, version_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Module '{module_name}' not found or no active version.",
        )
    return result


@router.get("/verify-chain")
def verify_chain(db: Session = Depends(get_db)):
    """Verify the integrity of the version hash chain."""
    return verify_hash_chain(db)


def _normalize_schema_domains(schema_json: dict, module_name: str) -> dict:
    """Return a copy of schema_json with every element's domain set to module_name.

    F-0010 / ISS-0046: module names were an emergent property of free-text
    authoring ``domain`` strings with no deployment-level control. This
    normalizes both entity types and relationships (copy-on-write — the
    caller's request payload is not mutated).
    """
    normalized = dict(schema_json)
    for section in ("entity_types", "relationships"):
        elements = schema_json.get(section, {}) or {}
        normalized[section] = {
            name: {**data, "domain": module_name}
            for name, data in elements.items()
        }
    return normalized


@router.post("/ratify")
@no_support_session("POST", "/api/ontology/ratify")
def ratify(request: RatifyRequest, db: Session = Depends(get_db)):
    """Ratify a new ontology version.

    Optional ``module_name`` (F-0010 / ISS-0046, additive): when present,
    the server normalizes the ``domain`` of every entity type and
    relationship in ``schema_json`` to that name and recomputes
    ``schema_modules`` server-side via ``partition_schema_by_module``
    (the client-supplied ``schema_modules`` is ignored in that case).
    This gives deployments explicit control over module naming instead
    of inheriting whatever free-text ``domain`` strings authoring
    produced.
    """
    schema_json = request.schema_json
    schema_modules = request.schema_modules
    if request.module_name:
        # F-0010 / ISS-0046: server-side domain normalization + repartition.
        from src.ontology.review_ops import partition_schema_by_module

        schema_json = _normalize_schema_domains(
            schema_json, request.module_name
        )
        schema_modules = partition_schema_by_module(schema_json)

    version = ratify_version(
        db=db,
        schema_json=schema_json,
        schema_modules=schema_modules,
        source=request.source,
        reviewer=request.reviewer,
        changelog=request.changelog,
        kgcl_commands=request.kgcl_commands,
        proposal_id=request.proposal_id,
        cq_coverage_snapshot=request.cq_coverage_snapshot,
        promotion_gate_passed=request.promotion_gate_passed,
        promotion_gate_details=request.promotion_gate_details,
    )

    # F-014 / ISS-0012: server-side audit trail for direct REST ratification.
    # Previously only clients emitted events, so curl/script-driven ratify left
    # elicitation_events empty. ``mcp_review_decided`` is reused (existing
    # EventType — no new enum members invented) with decision="ratified" and
    # the ratified version as the reviewed element; the version id anchors
    # session_id. Best-effort: never break the route on an audit failure.
    # Server-emitted rows are distinguishable via actor_type="system" (bridge).
    try:
        from src.elicitation.bridge import enqueue_event

        reviewer = request.reviewer or "unknown"
        enqueue_event(
            event_type="mcp_review_decided",
            payload={
                "session_id": str(version.id),
                "element_name": f"ontology_schema_v{version.version_number}",
                "decision": "ratified",
                "rationale": request.changelog,
                "agent_id": reviewer,
            },
            session_id_override=version.id,
            agent_id=reviewer,
            delegation_source="user_direct",
        )
    except Exception as exc:  # noqa: BLE001 — log-and-continue by design
        log.warning(
            "ontology.ratify.audit_event_failed",
            version_id=str(version.id),
            error=str(exc),
        )

    return version.model_dump(mode="json")


@router.get("/diff/{old_version_number}/{new_version_number}")
def compare_versions(
    old_version_number: int,
    new_version_number: int,
    db: Session = Depends(get_db),
):
    """Compare two ontology versions and return diffs."""
    old_version = get_version_by_number(db, old_version_number)
    if old_version is None:
        raise HTTPException(status_code=404, detail=f"Version {old_version_number} not found.")
    new_version = get_version_by_number(db, new_version_number)
    if new_version is None:
        raise HTTPException(status_code=404, detail=f"Version {new_version_number} not found.")

    rfc6902 = compute_rfc6902_patch(old_version.schema_json, new_version.schema_json)
    entity_diff = compute_entity_level_diff(old_version.schema_json, new_version.schema_json)

    return {
        "old_version": old_version_number,
        "new_version": new_version_number,
        "rfc6902_patch": rfc6902,
        "entity_level_diff": entity_diff,
    }


# Legacy /proposals and /proposals/summary routes removed — superseded by
# src.api.proposal_routes (Chunk 47, D389) with cursor pagination.
