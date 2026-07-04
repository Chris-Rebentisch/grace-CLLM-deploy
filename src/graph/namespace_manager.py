"""Namespace registry operations for federated child graph databases.

CRUD operations that coordinate between ArcadeDB (database creation)
and PostgreSQL (namespace registry).
"""

from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from src.graph.arcade_client import ArcadeClient
from src.graph.management_models import GraphNamespace
from src.graph.namespace_database import (
    create_namespace as db_create,
    delete_namespace as db_delete,
    get_namespace_by_name,
    list_namespaces as db_list,
)

logger = structlog.get_logger()


async def register_namespace(
    db: Session,
    client: ArcadeClient,
    namespace: GraphNamespace,
) -> GraphNamespace:
    """Register a new child graph namespace.

    1. Validate database_name is not already registered
    2. Create database in ArcadeDB via ensure_database
    3. Save to PostgreSQL graph_namespaces table
    """
    existing = get_namespace_by_name(db, namespace.database_name)
    if existing:
        raise ValueError(
            f"Namespace already registered: {namespace.database_name}"
        )

    await client.ensure_database(namespace.database_name)

    saved = db_create(db, namespace)
    logger.info(
        "namespace.registered",
        database_name=namespace.database_name,
        namespace_id=saved.id,
    )
    return saved


async def list_namespaces(db: Session) -> list[GraphNamespace]:
    """List all registered namespaces."""
    return db_list(db)


async def remove_namespace(db: Session, database_name: str) -> bool:
    """Remove namespace from registry.

    Does NOT drop the ArcadeDB database — just removes from PostgreSQL registry.
    Dropping databases is a manual/admin operation.
    """
    deleted = db_delete(db, database_name)
    if deleted:
        logger.info("namespace.removed", database_name=database_name)
    return deleted


async def get_namespace(db: Session, database_name: str) -> GraphNamespace | None:
    """Get a single namespace by database name."""
    return get_namespace_by_name(db, database_name)
