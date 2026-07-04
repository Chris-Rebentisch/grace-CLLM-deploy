"""Federation namespace registration and cleanup.

Creates and removes label-prefixed type DDL in the existing ``grace``
ArcadeDB database (D383 — single instance, no separate databases).
Coordinates with ``src/graph/namespace_database`` for Postgres CRUD.
"""

from __future__ import annotations

import re

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

_PASCAL_CASE_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")


def _validate_label_prefix(prefix: str, db: Session) -> None:
    """Validate label prefix format and uniqueness."""
    if not _PASCAL_CASE_RE.match(prefix):
        raise ValueError(
            f"label_prefix must be PascalCase: {prefix!r}"
        )
    existing = db_list(db)
    for ns in existing:
        if ns.label_prefix and ns.label_prefix == prefix:
            raise ValueError(
                f"Duplicate label_prefix: {prefix!r} already registered"
            )


async def register_federation_namespace(
    db: Session,
    client: ArcadeClient,
    namespace: GraphNamespace,
) -> GraphNamespace:
    """Register a federation namespace with label-prefixed DDL.

    D383: creates prefixed type DDL in the existing ``grace`` ArcadeDB
    database. Does NOT create a separate database. Uses label prefixes
    for namespace isolation.

    Args:
        db: SQLAlchemy session for Postgres CRUD.
        client: ArcadeDB client for DDL execution.
        namespace: Namespace model with federation fields populated.

    Returns:
        The persisted GraphNamespace model.

    Raises:
        ValueError: On duplicate label_prefix or invalid format.
    """
    if namespace.label_prefix:
        _validate_label_prefix(namespace.label_prefix, db)

    existing = get_namespace_by_name(db, namespace.database_name)
    if existing:
        raise ValueError(
            f"Namespace already registered: {namespace.database_name}"
        )

    # F-49 readiness gate: child namespaces must NOT enter query routing at
    # registration — their retrieval indexes/data don't exist yet, and routing
    # through them produced a global 200-but-empty retrieval outage. The
    # operator enables the namespace explicitly once it is populated. Mother
    # namespaces are ready by definition (they ARE the default graph).
    namespace.is_ready = namespace.namespace_type == "mother"

    # D383: Create prefixed vertex/edge types in the existing grace DB.
    if namespace.label_prefix:
        prefix = namespace.label_prefix
        # Create a vertex type for the namespace root marker.
        try:
            # F-42 (validation run, 2026-07-01): the prefixed edge type
            # `EXTENDS E`, but ArcadeDB has no OrientDB-style `V`/`E` base types
            # on a fresh database, so registration 500'd everywhere with
            # "Supertype 'E' not found" until an operator hand-created them.
            # Create the base types (idempotent) BEFORE the EXTENDS.
            await client.execute_sql("CREATE VERTEX TYPE V IF NOT EXISTS")
            await client.execute_sql("CREATE EDGE TYPE E IF NOT EXISTS")
            await client.execute_sql(
                f"CREATE VERTEX TYPE {prefix}_Entity IF NOT EXISTS"
            )
            await client.execute_sql(
                f"CREATE EDGE TYPE {prefix}_Relationship IF NOT EXISTS EXTENDS E"
            )
            logger.info(
                "federation.ddl_created",
                label_prefix=prefix,
                database="grace",
            )
        except Exception:
            logger.exception(
                "federation.ddl_creation_failed",
                label_prefix=prefix,
            )
            raise

    saved = db_create(db, namespace)
    logger.info(
        "federation.namespace_registered",
        database_name=namespace.database_name,
        namespace_id=saved.id,
        namespace_type=namespace.namespace_type,
        label_prefix=namespace.label_prefix,
    )
    return saved


async def unregister_federation_namespace(
    db: Session,
    client: ArcadeClient,
    namespace_name: str,
) -> bool:
    """Remove a federation namespace and clean up prefixed DDL.

    Args:
        db: SQLAlchemy session.
        client: ArcadeDB client.
        namespace_name: The ``database_name`` of the namespace to remove.

    Returns:
        True if found and deleted, False if not found.
    """
    ns = get_namespace_by_name(db, namespace_name)
    if not ns:
        return False

    # Clean up prefixed DDL in grace DB.
    if ns.label_prefix:
        prefix = ns.label_prefix
        try:
            await client.execute_sql(
                f"DROP TYPE {prefix}_Relationship IF EXISTS"
            )
            await client.execute_sql(
                f"DROP TYPE {prefix}_Entity IF EXISTS"
            )
            logger.info(
                "federation.ddl_dropped",
                label_prefix=prefix,
            )
        except Exception:
            logger.warning(
                "federation.ddl_drop_failed",
                label_prefix=prefix,
            )

    deleted = db_delete(db, namespace_name)
    if deleted:
        logger.info(
            "federation.namespace_unregistered",
            database_name=namespace_name,
        )
    return deleted
