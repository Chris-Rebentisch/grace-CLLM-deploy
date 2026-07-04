"""Diff-based incremental schema migration between ontology versions.

When a new ontology version is ratified, applies only the changes
(not a full re-sync). Uses graceful deprecation — never DROP TYPE
or DROP PROPERTY.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import structlog
from sqlalchemy.orm import Session

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.kgcl_generator import generate_kgcl_commands
from src.graph.schema_sync_database import create_sync_record
from src.graph.schema_sync_models import DDLStatement, GraphSchemaSyncRecord
from src.graph.system_properties import VERTEX_SYSTEM_PROPERTIES
from src.graph.type_mapping import map_data_type
from src.ontology.database import get_version_by_number
from src.ontology.diff_engine import compute_entity_level_diff

logger = structlog.get_logger()


async def migrate_schema(
    db: Session,
    client: ArcadeClient,
    from_version: int,
    to_version: int,
) -> GraphSchemaSyncRecord:
    """Apply incremental schema changes between two ontology versions.

    Steps:
    1. Load both versions from PostgreSQL
    2. Compute OM4OV diff via diff_engine.compute_entity_level_diff
    3. Generate KGCL commands from diff
    4. Translate diff to ArcadeDB DDL (add types, deprecate removed, add properties)
    5. Execute DDL via client.execute_sql
    6. Create Migration_Event vertex in ArcadeDB
    7. Record sync in PostgreSQL
    8. Return sync record with KGCL commands
    """
    # Step 1: Load versions
    old_version = get_version_by_number(db, from_version)
    new_version = get_version_by_number(db, to_version)
    if old_version is None:
        raise ValueError(f"Ontology version {from_version} not found")
    if new_version is None:
        raise ValueError(f"Ontology version {to_version} not found")

    # Step 2: Compute diff
    diff = compute_entity_level_diff(old_version.schema_json, new_version.schema_json)
    entity_diff = diff.get("entity_types", {})
    prop_diff = diff.get("properties", {})
    rel_diff = diff.get("relationships", {})

    # Same-version or no-diff shortcut
    added_types = entity_diff.get("added", [])
    removed_types = entity_diff.get("removed", [])
    added_rels = rel_diff.get("added", [])
    removed_rels = rel_diff.get("removed", [])
    added_props = prop_diff.get("added", [])
    removed_props = prop_diff.get("removed", [])
    modified_types = entity_diff.get("modified", [])

    has_changes = (
        added_types or removed_types or added_rels or removed_rels
        or added_props or removed_props or modified_types
    )

    if not has_changes:
        logger.info(
            "schema_migration.no_changes",
            from_version=from_version,
            to_version=to_version,
        )
        return GraphSchemaSyncRecord(
            ontology_version_id=str(new_version.id),
            ontology_version_number=to_version,
            total_statements=0,
            succeeded=0,
            failed=0,
            status="success",
            completed_at=datetime.now(UTC),
        )

    # Step 3: Generate KGCL
    kgcl_commands = generate_kgcl_commands(diff)

    # Step 4: Generate DDL from diff
    ddl_strings = _generate_migration_ddl(
        new_schema=new_version.schema_json,
        added_types=added_types,
        removed_types=removed_types,
        added_rels=added_rels,
        removed_rels=removed_rels,
        added_props=added_props,
        removed_props=removed_props,
    )

    # Step 5: Execute DDL
    await client.ensure_database(client.config.database)
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
            logger.error("schema_migration.ddl_failed", statement=sql, error=str(exc))
        ddl_statements.append(stmt)

    total = len(ddl_statements)
    if failed == 0:
        status = "success"
    elif succeeded == 0:
        status = "failed"
    else:
        status = "partial"

    # Step 6: Create Migration_Event vertex
    migration_id = str(uuid4())
    await _create_migration_event(
        client=client,
        migration_id=migration_id,
        from_version=from_version,
        to_version=to_version,
        ddl_executed_count=succeeded,
        ddl_failed_count=failed,
        types_added=added_types,
        types_deprecated=removed_types,
        properties_added=[f"{p['entity']}.{p['property']}" for p in added_props],
        kgcl_commands=kgcl_commands,
        status=status,
    )

    # Step 7: Record in PostgreSQL
    record = GraphSchemaSyncRecord(
        ontology_version_id=str(new_version.id),
        ontology_version_number=to_version,
        ddl_statements=ddl_statements,
        total_statements=total,
        succeeded=succeeded,
        failed=failed,
        status=status,
        completed_at=datetime.now(UTC),
    )
    saved = create_sync_record(db, record)

    logger.info(
        "schema_migration.completed",
        from_version=from_version,
        to_version=to_version,
        status=status,
        kgcl_count=len(kgcl_commands),
        ddl_count=total,
    )
    return saved


def _generate_migration_ddl(
    new_schema: dict,
    added_types: list[str],
    removed_types: list[str],
    added_rels: list[str],
    removed_rels: list[str],
    added_props: list[dict],
    removed_props: list[dict],
) -> list[str]:
    """Generate DDL for incremental changes only.

    - Added types → CREATE VERTEX/EDGE TYPE + properties + system properties
    - Added properties → CREATE PROPERTY
    - Removed types → set _deprecated=true (graceful, no DROP)
    - Removed properties → mark with _deprecated_at (no DROP)
    """
    statements: list[str] = []
    entity_types = new_schema.get("entity_types", {})
    relationships = new_schema.get("relationships", {})

    # Added vertex types
    for type_name in added_types:
        statements.append(f"CREATE VERTEX TYPE {type_name} IF NOT EXISTS")
        type_def = entity_types.get(type_name, {})
        for prop_name, prop_def in type_def.get("properties", {}).items():
            arcade_type = map_data_type(prop_def.get("data_type", "string"))
            statements.append(
                f"CREATE PROPERTY {type_name}.{prop_name} IF NOT EXISTS {arcade_type}"
            )
        # System properties for new types
        for sp in VERTEX_SYSTEM_PROPERTIES:
            statements.append(
                f"CREATE PROPERTY {type_name}.{sp['name']} IF NOT EXISTS {sp['type']}"
            )

    # Added relationships
    for rel_name in added_rels:
        statements.append(f"CREATE EDGE TYPE {rel_name} IF NOT EXISTS")
        rel_def = relationships.get(rel_name, {})
        for prop_name, prop_def in rel_def.get("properties", {}).items():
            arcade_type = map_data_type(prop_def.get("data_type", "string"))
            statements.append(
                f"CREATE PROPERTY {rel_name}.{prop_name} IF NOT EXISTS {arcade_type}"
            )

    # Deprecated types — graceful, never DROP
    for type_name in removed_types:
        statements.append(
            f"CREATE PROPERTY {type_name}._deprecated IF NOT EXISTS BOOLEAN"
        )
        statements.append(
            f"CREATE PROPERTY {type_name}._deprecated_at IF NOT EXISTS DATETIME"
        )
        statements.append(
            f"UPDATE {type_name} SET _deprecated = true, "
            f"_deprecated_at = sysdate() WHERE _deprecated IS NULL"
        )

    # Deprecated relationships — graceful, never DROP
    for rel_name in removed_rels:
        statements.append(
            f"CREATE PROPERTY {rel_name}._deprecated IF NOT EXISTS BOOLEAN"
        )
        statements.append(
            f"CREATE PROPERTY {rel_name}._deprecated_at IF NOT EXISTS DATETIME"
        )
        statements.append(
            f"UPDATE {rel_name} SET _deprecated = true, "
            f"_deprecated_at = sysdate() WHERE _deprecated IS NULL"
        )

    # Added properties on existing types
    for prop in added_props:
        entity = prop["entity"]
        prop_name = prop["property"]
        # Look up data type from new schema
        type_def = entity_types.get(entity, {})
        prop_def = type_def.get("properties", {}).get(prop_name, {})
        arcade_type = map_data_type(prop_def.get("data_type", "string"))
        statements.append(
            f"CREATE PROPERTY {entity}.{prop_name} IF NOT EXISTS {arcade_type}"
        )

    # Removed properties — mark deprecated, never DROP
    for prop in removed_props:
        entity = prop["entity"]
        prop_name = prop["property"]
        statements.append(
            f"CREATE PROPERTY {entity}.{prop_name}_deprecated_at IF NOT EXISTS DATETIME"
        )

    return statements


async def _create_migration_event(
    client: ArcadeClient,
    migration_id: str,
    from_version: int,
    to_version: int,
    ddl_executed_count: int,
    ddl_failed_count: int,
    types_added: list[str],
    types_deprecated: list[str],
    properties_added: list[str],
    kgcl_commands: list[str],
    status: str,
) -> None:
    """Insert a Migration_Event vertex into ArcadeDB."""
    try:
        # F-026 / ISS-0011: parameterized statement. The previous f-string
        # interpolation embedded json.dumps(kgcl_commands) inside single
        # quotes — every KGCL command contains single quotes (e.g.
        # "add property 'closing_date' ..."), which broke the statement and
        # silently dropped the ArcadeDB-side audit record on every daemon-
        # applied migration. Named :params are sent out-of-band, so quoted
        # payloads can never break the statement.
        query = (
            "INSERT INTO Migration_Event SET "
            "migration_id = :migration_id, "
            "from_version = :from_version, "
            "to_version = :to_version, "
            "ddl_executed_count = :ddl_executed_count, "
            "ddl_failed_count = :ddl_failed_count, "
            "types_added = :types_added, "
            "types_deprecated = :types_deprecated, "
            "properties_added = :properties_added, "
            "kgcl_commands = :kgcl_commands, "
            "migrated_at = sysdate(), "
            "migrated_by = 'system', "
            "status = :status"
        )
        params = {
            "migration_id": migration_id,
            "from_version": from_version,
            "to_version": to_version,
            "ddl_executed_count": ddl_executed_count,
            "ddl_failed_count": ddl_failed_count,
            "types_added": json.dumps(types_added),
            "types_deprecated": json.dumps(types_deprecated),
            "properties_added": json.dumps(properties_added),
            "kgcl_commands": json.dumps(kgcl_commands),
            "status": status,
        }
        await client.execute_query("sql", query, params=params)
        logger.info("migration_event.created", migration_id=migration_id)
    except (ArcadeDBError, ConnectionError, TimeoutError) as exc:
        logger.error("migration_event.failed", migration_id=migration_id, error=str(exc))
