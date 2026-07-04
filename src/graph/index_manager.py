"""Two-layer index system: static auto-indexes and dynamic analytics requests.

Static auto-indexes are created during schema sync for every vertex type.
Dynamic index requests come from the analytics module and are stored as
pending until explicitly applied.
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.schema_sync_database import (
    create_index_request,
    get_pending_index_requests,
    update_index_request_status,
)
from src.graph.schema_sync_models import GraphIndexRequest

logger = structlog.get_logger()

DEFAULT_VERTEX_INDEXES: list[dict] = [
    {"property": "grace_id", "unique": True, "reason": "Primary entity lookup, must be unique"},
    {"property": "name", "unique": False, "reason": "Most common entity lookup"},
    {"property": "valid_from", "unique": False, "reason": "Temporal query filtering"},
    {"property": "schema_version", "unique": False, "reason": "Version-scoped queries"},
]
"""Static auto-indexes created for every vertex type during schema sync."""


def generate_vector_index_ddl(
    type_name: str,
    property_name: str = "_embedding",
    dimensions: int = 768,
) -> str:
    """Generate a CREATE INDEX statement for an LSM_VECTOR index.

    # D445.2 / D356 — LSMVectorIndex DDL; ArcadeDB 26.5.1 accepts
    # LSM_VECTOR with METADATA dimensions + metric. Index is named
    # '{type_name}[{property_name}]' by ArcadeDB automatically.
    # Authorization: D445.2.

    Args:
        type_name: Vertex type name.
        property_name: Property to index (default: _embedding).
        dimensions: Vector dimensionality (default: 768 for nomic-embed-text).

    Returns:
        CREATE INDEX SQL string for LSM_VECTOR.
    """
    return (
        f'CREATE INDEX ON {type_name} ({property_name}) LSM_VECTOR '
        f'METADATA {{"dimensions": {dimensions}, "metric": "COSINE"}}'
    )


def generate_index_ddl(type_name: str, property_name: str, unique: bool = False) -> str:
    """Generate a single CREATE INDEX statement.

    Args:
        type_name: Vertex or edge type name.
        property_name: Property to index.
        unique: If True, create a unique index.

    Returns:
        CREATE INDEX SQL string.
    """
    if unique:
        return f"CREATE INDEX ON {type_name} ({property_name}) UNIQUE"
    return f"CREATE INDEX ON {type_name} ({property_name})"


async def create_static_indexes(client: ArcadeClient, schema_json: dict) -> list[str]:
    """Generate and execute static auto-indexes for all vertex types.

    Creates indexes on name, valid_from, and schema_version for every
    vertex type in the schema.

    Returns:
        List of executed index DDL statements.
    """
    entity_types = schema_json.get("entity_types", {})
    executed: list[str] = []

    for type_name in entity_types:
        for idx_def in DEFAULT_VERTEX_INDEXES:
            ddl = generate_index_ddl(type_name, idx_def["property"], idx_def["unique"])
            try:
                await client.execute_sql(ddl)
                executed.append(ddl)
                logger.info(
                    "static_index.created",
                    type_name=type_name,
                    property=idx_def["property"],
                )
            except (ArcadeDBError, ConnectionError, TimeoutError) as exc:
                logger.warning(
                    "static_index.failed",
                    type_name=type_name,
                    property=idx_def["property"],
                    error=str(exc),
                )

    return executed


async def create_vector_indexes(client: ArcadeClient, schema_json: dict) -> list[str]:
    """Generate and execute LSM_VECTOR indexes for all domain entity types.

    Creates a vector index on _embedding for every vertex type in the
    schema, enabling server-side ANN queries via vectorNeighbors().

    # D445.2 / D356 — wiring vector indexes into schema_sync alongside
    # static indexes. Authorization: D445.2.

    Returns:
        List of executed vector index DDL statements.
    """
    entity_types = schema_json.get("entity_types", {})
    executed: list[str] = []

    for type_name in entity_types:
        ddl = generate_vector_index_ddl(type_name)
        try:
            await client.execute_sql(ddl)
            executed.append(ddl)
            logger.info(
                "vector_index.created",
                type_name=type_name,
                property="_embedding",
            )
        except (ArcadeDBError, ConnectionError, TimeoutError) as exc:
            # Index already exists is expected on re-sync — log as info not error
            logger.warning(
                "vector_index.failed",
                type_name=type_name,
                property="_embedding",
                error=str(exc),
            )

    # D463 (Chunk 71 CP1) — meta-entity types with _embedding need vector indexes
    # for vectorNeighbors() queries. The domain-type loop above iterates
    # schema_json only; META_ENTITY_TYPES are covered here.
    # Invariant: create_vector_indexes governs ANN index creation.
    # Carve-out: meta-entity types carrying _embedding.
    # Authorization: D463.
    from src.graph.migration_types import META_ENTITY_TYPES

    for meta_type_name, props in META_ENTITY_TYPES.items():
        if any(p.get("name") == "_embedding" for p in props):
            ddl = generate_vector_index_ddl(meta_type_name)
            try:
                await client.execute_sql(ddl)
                executed.append(ddl)
                logger.info(
                    "vector_index.meta_entity_created",
                    type_name=meta_type_name,
                    property="_embedding",
                )
            except (ArcadeDBError, ConnectionError, TimeoutError) as exc:
                logger.warning(
                    "vector_index.meta_entity_failed",
                    type_name=meta_type_name,
                    property="_embedding",
                    error=str(exc),
                )

    return executed


async def apply_pending_indexes(
    db: Session, client: ArcadeClient,
) -> list[GraphIndexRequest]:
    """Apply all pending index requests from the analytics module.

    Reads pending requests from PostgreSQL, executes CREATE INDEX
    against ArcadeDB, and updates status to 'applied' or 'failed'.

    Returns:
        List of updated index requests.
    """
    pending = get_pending_index_requests(db)
    results: list[GraphIndexRequest] = []

    for request in pending:
        unique = request.index_type == "unique"
        ddl = generate_index_ddl(request.type_name, request.property_name, unique=unique)
        try:
            await client.execute_sql(ddl)
            request.status = "applied"
            request.applied_at = datetime.now(UTC)
            update_index_request_status(db, request.id, "applied")
            logger.info(
                "dynamic_index.applied",
                type_name=request.type_name,
                property=request.property_name,
            )
        except (ArcadeDBError, ConnectionError, TimeoutError) as exc:
            request.status = "failed"
            update_index_request_status(db, request.id, "failed")
            logger.error(
                "dynamic_index.failed",
                type_name=request.type_name,
                property=request.property_name,
                error=str(exc),
            )
        results.append(request)

    return results


def store_index_request(db: Session, request: GraphIndexRequest) -> GraphIndexRequest:
    """Store a new index request as pending."""
    return create_index_request(db, request)
