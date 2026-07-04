"""Federation API routes (Chunk 51, D402–D405).

Seven routes under ``/api/federation/``.

Route isolation (D246 mirror, spec §7): this module imports
``src.federation.service`` for all stateful operations. It MAY import
``src.federation.models`` (data-only) and ``src.federation.rules_engine``
(pure-function). It MUST NOT import ``src.federation.registry`` or
``src.federation.namespace_federation`` directly. CI guard:
``tests/federation/test_federation_route_isolation.py``.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.analytics import metrics as grace_metrics
from src.federation.models import (
    CanonicalEntity,
    FederationConfig,
    NamespaceRegistration,
)
from src.federation.service import FederationService
from src.graph.arcade_client import get_arcade_client
from src.graph.management_models import GraphNamespace
from src.graph.namespace_database import get_namespace_by_name, list_namespaces
from src.shared.database import get_session_factory

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/federation", tags=["federation"])

# Server-side federation telemetry (Chunk 51) — distinct from proposal/session UUIDs.
_FEDERATION_TELEMETRY_SESSION_ID = UUID("00000000-0000-0000-0000-000000000051")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config() -> FederationConfig:
    """Load federation config from disk."""
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "config", "federation.yaml"
    )
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return FederationConfig(**data)
    except Exception:
        logger.warning("federation.config_load_failed_using_defaults")
        return FederationConfig()


def _get_service() -> FederationService:
    return FederationService(config=_load_config())


def _get_db():
    factory = get_session_factory()
    return factory()


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _require_admin_key(request: Request) -> None:
    """Mutating-route admin-key enforcement."""
    admin_key = os.environ.get("GRACE_ADMIN_KEY", "")
    if not admin_key:
        client_host = request.client.host if request.client else None
        if client_host in {"127.0.0.1", "::1", "testclient"}:
            return
        raise HTTPException(status_code=401, detail="admin key required")
    submitted = request.headers.get("X-Admin-Key", "")
    if not submitted or not secrets.compare_digest(admin_key, submitted):
        raise HTTPException(status_code=401, detail="admin key required")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ResolveRequest(BaseModel):
    """Request body for entity resolution."""

    name: str = Field(description="Entity name to resolve")
    entity_type: str = Field(description="Entity type")
    namespace: str | None = Field(default=None, description="Namespace scope")


class ValidateChildRequest(BaseModel):
    """Request body for child schema validation."""

    child_schema: dict = Field(description="Child ontology schema")
    mother_version_id: str | None = Field(
        default=None, description="Mother version ID (uses active if null)"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/namespaces", status_code=201)
async def register_namespace(
    request: Request,
    body: NamespaceRegistration,
):
    """Register a federation namespace. Mutating; admin-key gated."""
    _require_admin_key(request)

    db = _get_db()
    try:
        client = get_arcade_client()
        service = _get_service()
        result = await service.register_namespace(db, client, body)

        # OTel + telemetry.
        grace_metrics.record_federation_namespace_registered(
            namespace_type=body.namespace_type,
        )

        try:
            from src.elicitation.event_writer import write_event
            from src.elicitation.models import (
                ElicitationEventEnvelope,
                validate_payload_for_event_type,
            )

            validated = validate_payload_for_event_type(
                "federation_namespace_registered",
                {
                    "namespace_id": str(result.id),
                    "namespace_type": result.namespace_type,
                    "label_prefix": result.label_prefix,
                    "database_name": result.database_name,
                },
            )
            envelope = ElicitationEventEnvelope(
                event_id=uuid4(),
                event_type="federation_namespace_registered",
                session_id=_FEDERATION_TELEMETRY_SESSION_ID,
                actor_type="system",
                phase_name="none",
                emitted_at=datetime.now(UTC),
                schema_version=1,
                grace_version="0.1.0",
                payload=validated.model_dump(mode="json"),
                payload_schema_version=1,
            )
            write_event(db, envelope)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "federation.namespace_registered.telemetry_failed",
                error=str(exc),
            )

        # F-49: routing state changed — reset the retrieval-side activation
        # cache so the new (not-ready) namespace is reflected immediately.
        _invalidate_retrieval_federation_cache()

        return result.model_dump()
    except ValueError as exc:
        if "Duplicate" in str(exc) or "already registered" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc))
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        db.close()


@router.get("/namespaces")
async def list_federation_namespaces():
    """List all federation namespaces. Read path."""
    db = _get_db()
    try:
        namespaces = list_namespaces(db)
        return [ns.model_dump() for ns in namespaces]
    finally:
        db.close()


@router.get("/namespaces/{namespace_id}")
async def get_namespace(namespace_id: str):
    """Get a single namespace by ID. Read path."""
    db = _get_db()
    try:
        namespaces = list_namespaces(db)
        for ns in namespaces:
            if ns.id == namespace_id:
                return ns.model_dump()
        raise HTTPException(status_code=404, detail="Namespace not found")
    finally:
        db.close()


class NamespacePatchBody(BaseModel):
    """PATCH body for namespace readiness (F-49)."""

    is_ready: bool = Field(
        description="Enable (true) or disable (false) federated query routing "
        "through this namespace"
    )


@router.patch("/namespaces/{namespace_id}")
async def patch_namespace(
    request: Request,
    namespace_id: str,
    body: NamespacePatchBody,
):
    """Enable/disable a namespace for query routing (F-49). Admin-key gated.

    This is also the sanctioned 'removal' path when hard DELETE is blocked by
    audit rows: a disabled namespace is inert for retrieval.
    """
    _require_admin_key(request)

    db = _get_db()
    try:
        from src.graph.namespace_database import GraphNamespaceRow

        row = (
            db.query(GraphNamespaceRow)
            .filter(GraphNamespaceRow.id == namespace_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Namespace not found")

        row.is_ready = body.is_ready
        db.commit()
        logger.info(
            "federation.namespace_readiness_changed",
            namespace_id=namespace_id,
            is_ready=body.is_ready,
        )
        _invalidate_retrieval_federation_cache()
        return {"namespace_id": namespace_id, "is_ready": body.is_ready}
    finally:
        db.close()


@router.delete("/namespaces/{namespace_id}")
async def delete_namespace(
    request: Request,
    namespace_id: str,
):
    """Unregister a namespace. Mutating; admin-key gated."""
    _require_admin_key(request)

    db = _get_db()
    try:
        # Find the namespace by ID to get database_name.
        namespaces = list_namespaces(db)
        target = None
        for ns in namespaces:
            if ns.id == namespace_id:
                target = ns
                break

        if not target:
            raise HTTPException(status_code=404, detail="Namespace not found")

        # F-49 FK policy: entity_resolution_review_queue rows are append-only
        # AUDIT data (c53a trigger) and FK-reference this namespace. Hard
        # deletion previously surfaced as an opaque 500 and required trigger-
        # bypass DB surgery. Policy: audit rows are never cascaded — pre-flight
        # to a clean 409 and point the operator at the disable path instead.
        from sqlalchemy import text as _sql_text

        dependent = db.execute(
            _sql_text(
                "SELECT count(*) FROM entity_resolution_review_queue "
                "WHERE namespace_id = :nsid"
            ),
            {"nsid": namespace_id},
        ).scalar()
        if dependent and dependent > 0:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Namespace has {dependent} entity-resolution review-queue "
                    "rows (append-only audit data). Hard delete is not "
                    "permitted; disable it instead: PATCH "
                    f"/api/federation/namespaces/{namespace_id} "
                    '{"is_ready": false}'
                ),
            )

        client = get_arcade_client()
        service = _get_service()
        await service.unregister_namespace(db, client, target.database_name)

        _invalidate_retrieval_federation_cache()
        return {"deleted": True, "namespace_id": namespace_id}
    finally:
        db.close()


def _invalidate_retrieval_federation_cache() -> None:
    """Best-effort reset of the retrieval-side federation cache (F-49)."""
    try:
        from src.api.retrieval_routes import invalidate_federation_cache

        invalidate_federation_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning("federation.cache_invalidation_failed", error=str(exc))


@router.post("/registry/resolve")
async def resolve_entity(body: ResolveRequest) -> CanonicalEntity | None:
    """Resolve entity name to canonical entity.

    Read-only POST (added to READONLY_ROUTES). Response body is the entity
    or JSON ``null`` when unresolved (spec §7.5).
    """
    db = _get_db()
    try:
        service = _get_service()
        entity, method = await service.resolve_entity(
            db, body.name, body.entity_type, body.namespace
        )

        grace_metrics.record_federation_entity_resolved(
            resolution_method=method,
        )

        try:
            from src.elicitation.event_writer import write_event
            from src.elicitation.models import (
                ElicitationEventEnvelope,
                validate_payload_for_event_type,
            )

            cgid = (
                str(entity.canonical_grace_id)
                if entity is not None
                else None
            )
            validated = validate_payload_for_event_type(
                "federation_entity_resolved",
                {
                    "canonical_grace_id": cgid,
                    "name": body.name,
                    "entity_type": body.entity_type,
                    "resolution_method": method,
                    "namespace": body.namespace,
                },
            )
            envelope = ElicitationEventEnvelope(
                event_id=uuid4(),
                event_type="federation_entity_resolved",
                session_id=_FEDERATION_TELEMETRY_SESSION_ID,
                actor_type="system",
                phase_name="none",
                emitted_at=datetime.now(UTC),
                schema_version=1,
                grace_version="0.1.0",
                payload=validated.model_dump(mode="json"),
                payload_schema_version=1,
            )
            write_event(db, envelope)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "federation.entity_resolved.telemetry_failed",
                error=str(exc),
            )

        if entity is None:
            return None
        return entity
    finally:
        db.close()


@router.get("/registry")
async def list_canonical_entities(
    type_filter: str | None = Query(default=None, description="Filter by entity type"),
):
    """List canonical entities. Read path."""
    db = _get_db()
    try:
        service = _get_service()
        entities = await service.list_canonical_entities(db, type_filter)
        return [e.model_dump() for e in entities]
    finally:
        db.close()


@router.post("/validate-child-schema")
async def validate_child_schema_route(body: ValidateChildRequest):
    """Validate child schema against mother schema (D405).

    Read-only POST (added to READONLY_ROUTES).
    """
    from src.ontology.database import get_active_version, get_version_by_id

    db = _get_db()
    try:
        if body.mother_version_id:
            mother = get_version_by_id(db, UUID(body.mother_version_id))
        else:
            mother = get_active_version(db)

        if not mother:
            raise HTTPException(
                status_code=404,
                detail="No active mother schema found",
            )

        service = _get_service()
        result = service.validate_child(body.child_schema, mother.schema_json)
        return result.model_dump(mode="json", by_alias=True)
    finally:
        db.close()
