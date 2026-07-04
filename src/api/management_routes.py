"""Graph management API routes — health, orphans, temporal, namespaces, dedup."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.config import ArcadeConfig
from src.graph.dedup_detection import detect_duplicates
from src.graph.health_metrics import get_health_report
from src.graph.management_models import (
    DuplicateReport,
    GraphHealthReport,
    GraphNamespace,
    OrphanReport,
    TemporalWindowRequest,
    TemporalWindowResponse,
)
from src.graph.namespace_manager import (
    get_namespace,
    list_namespaces,
    register_namespace,
    remove_namespace,
)
from src.graph.orphan_detection import detect_orphans
from src.graph.temporal_window import get_temporal_window
from src.shared.config import get_settings
from src.shared.database import get_db

router = APIRouter(prefix="/api/graph/management", tags=["graph-management"])


def _get_client() -> ArcadeClient:
    """Build an ArcadeClient from current settings."""
    settings = get_settings()
    config = ArcadeConfig.from_settings(settings)
    return ArcadeClient(config=config)


@router.get("/health", response_model=GraphHealthReport)
async def management_health():
    """Full graph health statistics."""
    client = _get_client()
    try:
        return await get_health_report(client)
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/orphans", response_model=OrphanReport)
async def management_orphans():
    """Detect orphan entities (vertices with zero edges)."""
    client = _get_client()
    try:
        return await detect_orphans(client)
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.post("/temporal-window", response_model=TemporalWindowResponse)
async def management_temporal_window(request: TemporalWindowRequest):
    """Return entities and relationships within a temporal window."""
    client = _get_client()
    try:
        return await get_temporal_window(client, request)
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.get("/namespaces", response_model=list[GraphNamespace])
async def management_list_namespaces(db: Session = Depends(get_db)):
    """List all registered graph namespaces."""
    return await list_namespaces(db)


@router.post("/namespaces", response_model=GraphNamespace)
async def management_register_namespace(
    namespace: GraphNamespace,
    db: Session = Depends(get_db),
):
    """Register a new child graph namespace."""
    client = _get_client()
    try:
        return await register_namespace(db, client, namespace)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")


@router.delete("/namespaces/{name}")
async def management_delete_namespace(
    name: str,
    db: Session = Depends(get_db),
):
    """Remove a namespace from the registry (does not drop ArcadeDB database)."""
    deleted = await remove_namespace(db, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {name}")
    return {"deleted": True}


@router.get("/duplicates", response_model=DuplicateReport)
async def management_duplicates(type: str | None = None):
    """Detect potential duplicate entities by exact name match."""
    client = _get_client()
    try:
        return await detect_duplicates(client, entity_type=type)
    except (ConnectionError, ArcadeDBError):
        raise HTTPException(status_code=503, detail="ArcadeDB unavailable")
