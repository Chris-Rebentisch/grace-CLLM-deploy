"""CLI-only sync pipeline orchestrator (D246 mirror, D411).

Orchestrates the full connector lifecycle:
discover → map → DDL → validate → ratify → load/sync → resolve.

Entry point: ``python -m src.connectors.sync_pipeline run ...``

MUST NOT be imported from ``src/api/connectors_routes.py`` (D246 mirror).
Route-isolation CI guard enforces this in ``test_route_invocation_surface.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.connectors.models import ConnectorConfig, SyncResult, SyncStatus
from src.connectors.registry import get_connector
from src.connectors.schema_mapper import load_mapping_config, map_source_schema

# F-43 (validation run, 2026-07-01): the CLI process never populated the
# connector registry — @register_connector side-effects only ran via the API
# route module's force-load, so `sync_pipeline run` always died with
# "Unknown connector type ... Registered: []". Mirror the route module's
# force-load here (registration import, NOT a route import — D246 intact).
import src.connectors.synthetic_connector  # noqa: E402,F401

logger = structlog.get_logger()

MAPPING_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "connectors"


async def run_sync(
    connector_type: str,
    namespace_id: UUID,
    *,
    mode: str | None = None,
    dry_run: bool = False,
    batch_size: int = 100,
) -> SyncResult:
    """Execute the full sync pipeline.

    Args:
        connector_type: Registered connector type string.
        namespace_id: Target federation namespace UUID.
        mode: "initial" | "incremental" | None (auto-detect).
        dry_run: If True, produce zero graph/DB writes.
        batch_size: Records per batch (informational; streaming in v1).
    """
    from src.graph.arcade_client import get_arcade_client
    from src.graph.namespace_database import update_sync_status
    from src.ontology.schema_store import ratify_version, validate_child_ontology_submission
    from src.shared.config import get_settings

    settings = get_settings()
    start_time = datetime.now(UTC)

    # --- Setup DB session ---
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(str(settings.database_url))
    SessionLocal = sessionmaker(bind=engine)
    db: Session = SessionLocal()

    config = ConnectorConfig(
        connector_type=connector_type,
        namespace_id=namespace_id,
    )
    connector = get_connector(connector_type, config)

    result = SyncResult(
        connector_type=connector_type,
        namespace_id=namespace_id,
        status=SyncStatus.RUNNING,
        started_at=start_time,
    )

    try:
        # 1. Pre-flight connectivity check
        if not connector.check_connectivity():
            if not dry_run:
                update_sync_status(db, _get_database_name(db, namespace_id), "error", start_time)
            result.status = SyncStatus.FAILED
            result.error_detail = "Connectivity check failed"
            return result

        # 2. Discover schema
        source_schema = connector.discover_schema()

        # 3. Map schema
        namespace = _get_namespace(db, namespace_id)
        mapping_path = MAPPING_CONFIG_DIR / f"{connector_type}_mapping.yaml"
        mapping_config = load_mapping_config(mapping_path)
        mother_ontology = _get_mother_ontology(db)
        mapping_result = map_source_schema(source_schema, namespace, mother_ontology, mapping_config)

        if dry_run:
            logger.info(
                "dry_run_plan",
                ddl_count=len(mapping_result.vertex_type_ddl),
                child_types=list(mapping_result.child_ontology_schema.keys()),
            )
            result.status = SyncStatus.COMPLETED
            result.completed_at = datetime.now(UTC)
            return result

        # 4. Execute DDL
        arcade_client = get_arcade_client()

        # F-032a / ISS-0023: the child `database_name` recorded at namespace
        # registration is never created as a separate ArcadeDB database —
        # label-prefixed types land in the parent graph by design, but the
        # field name misleads operators into looking for a child database.
        # Log the clarification at sync start. No persistence behavior change.
        actual_db = arcade_client.config.database
        if namespace.database_name != actual_db:
            logger.info(
                "connector_sync_label_prefix_namespacing",
                configured_database_name=namespace.database_name,
                actual_database=actual_db,
                label_prefix=namespace.label_prefix,
                detail=(
                    f"label-prefix namespacing: types are created in the parent "
                    f"database '{actual_db}' with prefix "
                    f"'{namespace.label_prefix}'; no separate database named "
                    f"'{namespace.database_name}' is created"
                ),
            )

        for ddl in mapping_result.vertex_type_ddl:
            await arcade_client.execute_sql(ddl)

        # 5. Validate child ontology
        validation = validate_child_ontology_submission(
            mapping_result.child_ontology_schema, mother_ontology
        )

        # 6. Ratify version
        ratify_version(
            db,
            schema_json=mapping_result.child_ontology_schema,
            schema_modules={connector_type: mapping_result.child_ontology_schema},
            # F-44 (validation run, 2026-07-01): the OntologyVersion source
            # enum now carries 'connector_sync' (VersionSource.CONNECTOR_SYNC),
            # so connector-originated versions record accurate provenance
            # instead of the earlier 'manual' workaround.
            source="connector_sync",
            reviewer=None,
            changelog=f"Connector sync: {connector_type}",
            ontology_scope="child",
            # F-0045 / ISS-0025 (validation run 2026-07-03, 3rd
            # occurrence): this ratification previously flipped the
            # deployment-active flag, replacing the mother ontology with a
            # 0-entity-type child schema and breaking every module-scoped
            # consumer. Invariant: a CHILD-namespace connector sync must
            # NEVER become or replace the deployment's active ontology
            # version. activate=False persists the child version for
            # provenance only; ratify_version carries a belt-and-braces
            # guard that refuses activation for connector_sync/child scope
            # even if this flag regresses.
            activate=False,
        )

        # 7–8. Watermark read + mode pick BEFORE "syncing" stamp (D411 auto-detect).
        db_name = _get_database_name(db, namespace_id)
        last_sync_at = _get_last_sync_at(db, namespace_id)
        effective_mode = mode
        if effective_mode is None:
            effective_mode = "initial" if last_sync_at is None else "incremental"

        update_sync_status(db, db_name, "syncing", start_time)

        # 9. Load / sync records
        from src.connectors.entity_resolver import resolve_or_create
        from src.federation.registry import CanonicalEntityRegistry

        registry = CanonicalEntityRegistry(
            session=db,
            ollama_base_url=str(settings.ollama_base_url),
        )

        record_count = 0
        if effective_mode == "initial":
            async for record in connector.initial_load():
                resolved = await resolve_or_create(
                    record, namespace, registry,
                    arcade_client=arcade_client,
                    db=db,
                    ollama_base_url=str(settings.ollama_base_url),
                )
                record_count += 1
                if resolved.outcome == "bridged":
                    result.records_bridged += 1
                elif resolved.outcome == "created":
                    result.records_created += 1
                elif resolved.outcome == "queued":
                    result.records_queued += 1
                elif resolved.outcome == "updated":
                    result.records_updated += 1
        else:
            async for record in connector.incremental_sync(since=last_sync_at):
                resolved = await resolve_or_create(
                    record, namespace, registry,
                    arcade_client=arcade_client,
                    db=db,
                    ollama_base_url=str(settings.ollama_base_url),
                )
                record_count += 1
                if resolved.outcome == "bridged":
                    result.records_bridged += 1
                elif resolved.outcome == "created":
                    result.records_created += 1
                elif resolved.outcome == "queued":
                    result.records_queued += 1
                elif resolved.outcome == "updated":
                    result.records_updated += 1

        result.records_processed = record_count

        # F-032a / ISS-0023: a completed sync reporting records_queued > 0 gave
        # no pointer to WHERE those records went (the append-only Postgres
        # entity_resolution_review_queue) — they appeared "lost". Name the
        # destination and the next step in the result itself.
        if result.records_queued:
            result.records_queued_to = "entity_resolution_review_queue"
            result.records_queued_hint = (
                "Queued records await manual entity-resolution review in the "
                "append-only Postgres table entity_resolution_review_queue "
                "(status='pending'); they are NOT yet graph vertices. Review via: "
                "SELECT * FROM entity_resolution_review_queue WHERE namespace_id = "
                f"'{namespace_id}' ORDER BY created_at DESC"
            )
            logger.info(
                "connector_sync_records_queued_destination",
                records_queued=result.records_queued,
                records_queued_to=result.records_queued_to,
                hint=result.records_queued_hint,
            )

        # 10. Update sync status → synced (watermark = completion time)
        update_sync_status(db, db_name, "synced", datetime.now(UTC))

        # 11. Upsert connector_sync_state
        schema_hash = hashlib.sha256(
            json.dumps(mapping_result.child_ontology_schema, sort_keys=True).encode()
        ).hexdigest()[:32]
        _upsert_sync_state(db, namespace_id, connector_type, schema_hash, record_count)

        result.status = SyncStatus.COMPLETED
        result.completed_at = datetime.now(UTC)

        # Record duration metric
        _record_duration(connector_type, effective_mode, time.monotonic())

    except (httpx.ConnectError, httpx.TimeoutException, ConnectionError) as exc:
        # Transient network-class failures (backend down, timeout) —
        # logged distinctly from permanent errors for triage clarity.
        # Control flow is identical to the broad catch below.
        logger.error("sync_pipeline_error", error=str(exc), error_class="transient")
        result.status = SyncStatus.FAILED
        result.error_detail = str(exc)
        try:
            db_name = _get_database_name(db, namespace_id)
            update_sync_status(db, db_name, "error", start_time)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        logger.error("sync_pipeline_error", error=str(exc), error_class="permanent")
        result.status = SyncStatus.FAILED
        result.error_detail = str(exc)
        try:
            db_name = _get_database_name(db, namespace_id)
            update_sync_status(db, db_name, "error", start_time)
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
        engine.dispose()

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_namespace(db: Session, namespace_id: UUID):
    """Load namespace from DB."""
    from src.graph.management_models import GraphNamespace
    from src.graph.namespace_database import GraphNamespaceRow

    row = db.query(GraphNamespaceRow).filter(
        GraphNamespaceRow.id == namespace_id
    ).first()
    if not row:
        raise ValueError(f"Namespace {namespace_id} not found")

    from src.graph.namespace_database import _row_to_model
    return _row_to_model(row)


def _get_database_name(db: Session, namespace_id: UUID) -> str:
    """Get database_name for a namespace_id."""
    from src.graph.namespace_database import GraphNamespaceRow
    row = db.query(GraphNamespaceRow).filter(
        GraphNamespaceRow.id == namespace_id
    ).first()
    if not row:
        raise ValueError(f"Namespace {namespace_id} not found")
    return row.database_name


def _get_last_sync_at(db: Session, namespace_id: UUID) -> datetime | None:
    """Get last_sync_at for mode auto-detection."""
    from src.graph.namespace_database import GraphNamespaceRow
    row = db.query(GraphNamespaceRow).filter(
        GraphNamespaceRow.id == namespace_id
    ).first()
    return row.last_sync_at if row else None


def _get_mother_ontology(db: Session) -> dict:
    """Get the active mother ontology schema."""
    try:
        from src.ontology.database import get_active_version
        version = get_active_version(db)
        if version and version.schema_json:
            return version.schema_json
    except Exception:  # noqa: BLE001
        pass
    return {}


def _upsert_sync_state(
    db: Session,
    namespace_id: UUID,
    connector_type: str,
    schema_hash: str,
    record_count: int,
) -> None:
    """Upsert connector_sync_state row."""
    db.execute(
        text("""
            INSERT INTO connector_sync_state
                (id, namespace_id, connector_type, schema_hash,
                 record_count, updated_at)
            VALUES
                (gen_random_uuid(), :ns_id, :ct, :hash, :count, NOW())
            ON CONFLICT (namespace_id) DO UPDATE SET
                connector_type = EXCLUDED.connector_type,
                schema_hash = EXCLUDED.schema_hash,
                record_count = EXCLUDED.record_count,
                updated_at = NOW()
        """),
        {
            "ns_id": str(namespace_id),
            "ct": connector_type,
            "hash": schema_hash,
            "count": record_count,
        },
    )
    db.commit()


def _record_duration(connector_type: str, mode: str, elapsed: float) -> None:
    """Record grace_connector_sync_duration_seconds. Best-effort."""
    try:
        from src.analytics.metrics import record_connector_sync_duration
        record_connector_sync_duration(
            connector_type=connector_type, mode=mode, duration=elapsed
        )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for the connector sync pipeline."""
    parser = argparse.ArgumentParser(
        description="GrACE Connector Sync Pipeline (D246 mirror — CLI-only)",
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Execute a connector sync")
    run_parser.add_argument("--connector-type", required=True, help="Registered connector type")
    run_parser.add_argument("--namespace-id", required=True, help="Target namespace UUID")
    run_parser.add_argument("--mode", choices=["initial", "incremental"], default=None, help="Sync mode (auto-detect if omitted)")
    run_parser.add_argument("--dry-run", action="store_true", help="Validate only — zero writes")
    run_parser.add_argument("--batch-size", type=int, default=100, help="Records per batch")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "run":
        result = asyncio.run(run_sync(
            connector_type=args.connector_type,
            namespace_id=UUID(args.namespace_id),
            mode=args.mode,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        ))
        print(result.model_dump_json(indent=2))
        sys.exit(0 if result.status == SyncStatus.COMPLETED else 1)


if __name__ == "__main__":
    main()
