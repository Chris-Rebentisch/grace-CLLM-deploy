"""Schema sync orchestration: read ontology -> generate DDL -> execute -> record."""

from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.ddl_generator import generate_full_schema_ddl
from src.graph.index_manager import create_static_indexes, create_vector_indexes
from src.graph.schema_sync_database import create_sync_record, get_latest_sync, get_sync_by_version
from src.graph.schema_sync_models import DDLStatement, GraphSchemaSyncRecord
from src.ontology.database import get_active_version

logger = structlog.get_logger()


async def sync_schema_to_graph(
    db: Session,
    client: ArcadeClient,
) -> GraphSchemaSyncRecord:
    """Full schema sync: read active ontology -> generate DDL -> execute -> record.

    Steps:
    1. Get active ontology version from PostgreSQL
    2. Check if this version is already synced (idempotent)
    3. Ensure ArcadeDB database exists
    4. Generate full DDL
    5. Execute each DDL statement
    6. Record success/failure per statement
    7. Create GraphSchemaSyncRecord in PostgreSQL
    8. Return the sync record
    """
    # Step 1: Get active ontology
    active = get_active_version(db)
    if active is None:
        raise ValueError("No active ontology version found")

    version_id = str(active.id)
    version_number = active.version_number

    # Step 2: Check if already synced
    existing = get_sync_by_version(db, version_id)
    if existing and existing.status == "success":
        logger.info(
            "schema_sync.already_synced",
            version_number=version_number,
            sync_id=existing.id,
        )
        return existing

    # Step 3: Ensure database exists
    await client.ensure_database(client.config.database)

    # Step 4: Generate DDL
    ddl_strings = generate_full_schema_ddl(active.schema_json)

    # Step 5 & 6: Execute each statement, track results
    ddl_statements: list[DDLStatement] = []
    succeeded = 0
    failed = 0

    for sql in ddl_strings:
        stmt = DDLStatement(statement=sql)
        try:
            await client.execute_sql(sql)
            stmt.status = "executed"
            stmt.executed_at = datetime.now(UTC)
            succeeded += 1
        except (ArcadeDBError, ConnectionError, TimeoutError) as exc:
            stmt.status = "failed"
            stmt.error = str(exc)
            failed += 1
            logger.error(
                "schema_sync.ddl_failed",
                statement=sql,
                error=str(exc),
            )
        ddl_statements.append(stmt)

    # D445.2 / D356 — wiring static + vector indexes into schema_sync;
    # static indexes were previously test-only — first production execution site.
    # Authorization: D445.2.
    schema_json = active.schema_json
    await create_static_indexes(client, schema_json)
    await create_vector_indexes(client, schema_json)

    # Step 7: Determine overall status and create record
    total = len(ddl_statements)
    if failed == 0:
        status = "success"
    elif succeeded == 0:
        status = "failed"
    else:
        status = "partial"

    record = GraphSchemaSyncRecord(
        ontology_version_id=version_id,
        ontology_version_number=version_number,
        ddl_statements=ddl_statements,
        total_statements=total,
        succeeded=succeeded,
        failed=failed,
        status=status,
        completed_at=datetime.now(UTC),
        error_message=f"{failed} of {total} statements failed" if failed > 0 else None,
    )

    saved = create_sync_record(db, record)
    logger.info(
        "schema_sync.completed",
        version_number=version_number,
        status=status,
        total=total,
        succeeded=succeeded,
        failed=failed,
    )
    return saved


async def preview_sync(db: Session) -> dict:
    """Dry run: generate DDL without executing.

    Returns: {"version_number": N, "ddl_statements": [...], "statement_count": N}
    """
    active = get_active_version(db)
    if active is None:
        raise ValueError("No active ontology version found")

    ddl_strings = generate_full_schema_ddl(active.schema_json)
    return {
        "version_number": active.version_number,
        "ddl_statements": ddl_strings,
        "statement_count": len(ddl_strings),
    }


async def get_sync_status(db: Session) -> dict:
    """Return current sync state.

    Returns: {
        "ontology_version": N (active in PostgreSQL),
        "graph_version": N (last synced to ArcadeDB),
        "in_sync": bool,
        "last_sync_at": timestamp,
        "last_sync_status": "success" | "partial" | "failed" | null
    }
    """
    active = get_active_version(db)
    latest = get_latest_sync(db)

    ontology_version = active.version_number if active else None
    graph_version = latest.ontology_version_number if latest else None

    return {
        "ontology_version": ontology_version,
        "graph_version": graph_version,
        "in_sync": (
            ontology_version is not None
            and graph_version is not None
            and ontology_version == graph_version
            and latest is not None
            and latest.status == "success"
        ),
        "last_sync_at": latest.completed_at.isoformat() if latest and latest.completed_at else None,
        "last_sync_status": latest.status if latest else None,
    }
