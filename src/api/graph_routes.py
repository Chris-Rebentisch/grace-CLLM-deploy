"""Graph module API routes — ArcadeDB health, info, schema sync, and index management."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.config import ArcadeConfig
from src.graph.entity_models import (
    BulkInsertRequest,
    EntityCreate,
    EntityUpdate,
    RelationshipCreate,
)
from src.graph.entity_ops import (
    bulk_insert,
    canonical_lookup,
    get_entity,
    insert_entity,
    update_entity,
)
from src.graph.graph_read_models import (
    NeighborhoodResponse,
    PagedEntitiesResponse,
    PagedRelationshipsResponse,
)
from src.graph.graph_read_ops import (
    FilterMismatchError,
    list_entities_paged,
    list_relationships_paged,
)
from src.graph.health_metrics import (
    get_edge_aggregation,
    get_graph_counts,
    get_relationship_coverage,
)
from src.graph.index_manager import apply_pending_indexes, store_index_request
from src.graph.neighborhood import fetch_entity_neighborhood
from src.graph.relationship_ops import get_relationship, insert_relationship
from src.graph.schema_sync import (
    get_sync_status,
    preview_sync,
    sync_schema_to_graph,
)
from src.graph.schema_sync_database import list_index_requests
from src.graph.schema_sync_models import GraphIndexRequest
from src.shared.config import get_settings
from src.shared.database import get_db

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _get_client() -> ArcadeClient:
    """Build an ArcadeClient from current settings."""
    settings = get_settings()
    config = ArcadeConfig.from_settings(settings)
    return ArcadeClient(config=config)


@router.get("/health")
async def graph_health():
    """Check ArcadeDB connectivity. Returns server info or 503."""
    client = _get_client()
    try:
        info = await client.health_check()
        return {"status": "ok", "server": info}
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/info")
async def graph_info():
    """Return ArcadeDB server info plus the database this API is bound to.

    F-0001 / ISS-0042: the raw server blob alone never named the database
    the client is bound to, so "confirm the API is bound to grace_test"
    style sandbox/live verification was unsatisfiable. ``database`` is the
    ArcadeConfig-resolved name (honors ARCADE_DATABASE via its
    default_factory — see F-022).
    """
    client = _get_client()
    try:
        info = await client.health_check()
        return {"database": client.config.database, "server": info}
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/counts")
async def graph_counts():
    """Exact graph-wide per-type entity and relationship counts.

    Unlike retrieval (top-k capped), this returns authoritative
    ``count(*)`` figures per type — the count to trust for "how many X
    are in the graph" questions.
    """
    client = _get_client()
    try:
        return await get_graph_counts(client)
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/aggregate")
async def graph_aggregate(edge_type: str, direction: str = "in"):
    """Ranked count of one edge type grouped by an endpoint node.

    `direction="in"` ranks edge targets (e.g. `governed_by` -> which
    jurisdiction governs the most); `direction="out"` ranks edge sources
    (e.g. `party_to` -> which entity is party to the most). One call
    answers "which X has the most Y" — domain-agnostic, parameterized by
    edge type.
    """
    client = _get_client()
    try:
        return await get_edge_aggregation(client, edge_type, direction)
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/relationship-coverage")
async def graph_relationship_coverage():
    """Per-relationship completeness across the whole graph.

    For each domain relationship type, what fraction of its source
    entities actually carry the edge — the one-call "where is extraction
    thin?" view. Domain-agnostic; sorted thinnest-coverage-first.
    """
    client = _get_client()
    try:
        return await get_relationship_coverage(client)
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.post("/sync-schema")
async def sync_schema(db: Session = Depends(get_db)):
    """Sync active ontology schema to ArcadeDB. Returns sync record with per-statement results."""
    client = _get_client()
    try:
        record = await sync_schema_to_graph(db, client)
        return record.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/schema-status")
async def schema_status(db: Session = Depends(get_db)):
    """Current sync status: which ontology version is in the graph."""
    status = await get_sync_status(db)
    return status


@router.post("/preview-sync")
async def preview_sync_endpoint(db: Session = Depends(get_db)):
    """Dry run: show DDL statements without executing."""
    try:
        result = await preview_sync(db)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/request-index")
async def request_index(request: GraphIndexRequest, db: Session = Depends(get_db)):
    """Analytics module requests an index on a property. Stored as pending."""
    saved = store_index_request(db, request)
    return saved.model_dump(mode="json")


@router.post("/apply-indexes")
async def apply_indexes(db: Session = Depends(get_db)):
    """Apply all pending index requests to ArcadeDB."""
    client = _get_client()
    try:
        results = await apply_pending_indexes(db, client)
        return {
            "applied": [r.model_dump(mode="json") for r in results if r.status == "applied"],
            "failed": [r.model_dump(mode="json") for r in results if r.status == "failed"],
            "total": len(results),
        }
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/indexes")
async def list_indexes(db: Session = Depends(get_db)):
    """List all index requests with their status."""
    requests = list_index_requests(db)
    return [r.model_dump(mode="json") for r in requests]


# ===========================================================================
# Chunk 29 D229 scope segments
# ===========================================================================


class SegmentRow(BaseModel):
    """A single scope segment with entity count."""

    module_name: str
    entity_count: int


@router.get("/scope/segments", response_model=list[SegmentRow])
async def list_scope_segments():
    """Return distinct ontology_module segments with entity counts (D229)."""
    client = _get_client()
    try:
        result = await client.execute_sql(
            (
                "SELECT ontology_module, count(*) AS entity_count "
                "FROM Entity GROUP BY ontology_module "
                "ORDER BY ontology_module ASC"
            ),
        )
        rows = result.get("result", []) if isinstance(result, dict) else []
        segments: list[SegmentRow] = []
        for row in rows:
            module = row.get("ontology_module")
            count = row.get("entity_count", 0)
            segments.append(
                SegmentRow(
                    module_name=module if module else "_unclassified",
                    entity_count=int(count),
                )
            )
        # Sort alphabetically by module_name
        segments.sort(key=lambda s: s.module_name)
        return segments
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=500, detail="Failed to query scope segments")


# ===========================================================================
# Entity / Relationship CRUD (Chunk 13)
# ===========================================================================


# ---------- Chunk 28 D212 read routes ----------
# Registered BEFORE the parametric /entities/{grace_id} handlers to avoid
# FastAPI routing ambiguity. Keep the order stable.


@router.get("/entities", response_model=PagedEntitiesResponse)
async def list_entities(
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    entity_type: str | None = None,
    ontology_module: str | None = None,
):
    """Cursor-paged entity listing (D212).

    Returns at most `limit` entities plus an opaque `next_cursor` when more
    results exist. Cursor carries an embedded filter fingerprint; mismatch
    with the active filters returns 400 (`filter_mismatch`).
    """
    client = _get_client()
    try:
        return await list_entities_paged(
            client,
            cursor=cursor,
            limit=limit,
            entity_type=entity_type,
            ontology_module=ontology_module,
        )
    except FilterMismatchError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "filter_mismatch",
                "message": "Filters changed mid-pagination; reset to page 1",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get(
    "/entities/{grace_id}/neighborhood",
    response_model=NeighborhoodResponse,
    description=(
        "Return the 1- or 2-hop neighborhood of an entity. "
        "Internal caps are fixed: 100 outgoing + 100 incoming at depth 1, "
        "50 at depth 2; they are not configurable via query params."
    ),
)
async def get_entity_neighborhood(
    grace_id: str,
    depth: int = Query(default=1, ge=1, le=2),
):
    """Wrap `fetch_entity_neighborhood` (D212)."""
    client = _get_client()
    try:
        entity = await get_entity(client, grace_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        data = await fetch_entity_neighborhood(client, grace_id, max_depth=depth)
        return NeighborhoodResponse(
            seed=data.get("seed", {}) or {},
            neighbors=data.get("neighbors", []) or [],
            edges=data.get("edges", []) or [],
        )
    except HTTPException:
        raise
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/relationships", response_model=PagedRelationshipsResponse)
async def list_relationships(
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    relationship_type: str | None = None,
):
    """Cursor-paged relationship listing (D212)."""
    client = _get_client()
    try:
        return await list_relationships_paged(
            client,
            cursor=cursor,
            limit=limit,
            relationship_type=relationship_type,
        )
    except FilterMismatchError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "filter_mismatch",
                "message": "Filters changed mid-pagination; reset to page 1",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


# ---------- End Chunk 28 D212 routes ----------


@router.post("/entities/")
async def create_entity(entity: EntityCreate):
    """Insert a single entity into ArcadeDB with canonical dedup."""
    # F-09: writes previously auto-created undefined vertex types in ArcadeDB,
    # bypassing the human-approved ontology boundary. Enforce at the API front
    # door (422 with the offending type named).
    from src.graph.type_enforcement import (
        UndefinedEntityTypeError,
        validate_entity_type,
    )

    try:
        validate_entity_type(entity.entity_type)
    except UndefinedEntityTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    client = _get_client()
    try:
        result = await insert_entity(client, entity)
        return result.model_dump(mode="json")
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/entities/bulk")
async def bulk_insert_entities(request: BulkInsertRequest):
    """Bulk insert entities and relationships with partial success."""
    # F-09: same write-time boundary as the single-entity route. Validated
    # up-front for the whole batch so a bad type is a clean 422 (with every
    # offending type named) rather than a partial write.
    from src.graph.type_enforcement import (
        UndefinedEntityTypeError,
        validate_entity_type,
    )

    bad_types: list[str] = []
    for item in request.entities or []:
        try:
            validate_entity_type(item.entity_type)
        except UndefinedEntityTypeError:
            if item.entity_type not in bad_types:
                bad_types.append(item.entity_type)
    if bad_types:
        raise HTTPException(
            status_code=422,
            detail=(
                "Undefined entity types (not in the active ontology or "
                f"system plane): {', '.join(bad_types)}. Ratify them first "
                "(F-09 write-time boundary)."
            ),
        )

    client = _get_client()
    try:
        result = await bulk_insert(client, request)
        return result.model_dump(mode="json")
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/entities/lookup")
async def lookup_entity(type: str, name: str):
    """Canonical lookup: find entity by type and name."""
    client = _get_client()
    try:
        grace_id = await canonical_lookup(client, type, name)
        if grace_id is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        entity = await get_entity(client, grace_id)
        return {"grace_id": grace_id, "entity": entity}
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/entities/{grace_id}")
async def get_entity_by_id(grace_id: str):
    """Get a single entity by grace_id."""
    client = _get_client()
    try:
        entity = await get_entity(client, grace_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        return entity
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.put("/entities/{grace_id}")
async def update_entity_by_id(grace_id: str, update: EntityUpdate):
    """Partial update of entity properties by grace_id."""
    client = _get_client()
    try:
        result = await update_entity(client, grace_id, update)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.post("/relationships/")
async def create_relationship(rel: RelationshipCreate):
    """Insert a single relationship (edge) between two entities."""
    client = _get_client()
    try:
        result = await insert_relationship(client, rel)
        return result.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/relationships/{grace_id}")
async def get_relationship_by_id(grace_id: str):
    """Get a single relationship by grace_id."""
    client = _get_client()
    try:
        rel = await get_relationship(client, grace_id)
        if rel is None:
            raise HTTPException(status_code=404, detail="Relationship not found")
        return rel
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")
