"""Extraction_Event provenance tracking in ArcadeDB + PostgreSQL reconciliation.

Creates Extraction_Event vertices and produced_by edges in the provenance
layer. Manages event status transitions and dual-write reconciliation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from sqlalchemy.orm import Session

from src.extraction.claim_database import (
    get_extraction_event,
    update_extraction_event_status,
)
from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import build_property_map, escape_cypher_string

log = structlog.get_logger()

VALID_TRANSITIONS: dict[str, set[str]] = {
    "running": {"verified"},
    "verified": {"graph_written", "partial_failed", "graph_failed"},
    "partial_failed": {"verified"},
    "graph_failed": {"verified"},
}


def validate_status_transition(current: str, target: str) -> None:
    """Check that a status transition is valid. Raises ValueError if not."""
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Illegal status transition: '{current}' -> '{target}'. "
            f"Allowed from '{current}': {allowed}"
        )


async def create_extraction_event_vertex(
    client: ArcadeClient,
    event_data: dict,
) -> str:
    """Create an Extraction_Event vertex in ArcadeDB.

    Uses OpenCypher CREATE. Returns the grace_id of the created vertex.
    The extraction_event_id property is the PostgreSQL event_id — the
    cross-store join key.
    """
    grace_id = str(uuid4())
    props = {"grace_id": grace_id}
    props.update(event_data)
    prop_map = build_property_map(props)
    query = f"CREATE (n:Extraction_Event {prop_map}) RETURN n"
    await client.execute_cypher(query)
    log.info(
        "provenance.extraction_event_created",
        grace_id=grace_id,
        extraction_event_id=event_data.get("extraction_event_id"),
    )
    return grace_id


async def create_produced_by_edges(
    client: ArcadeClient,
    entity_grace_ids: list[str],
    event_grace_id: str,
    event_extraction_id: str,
) -> int:
    """Create produced_by edges from entities to their Extraction_Event.

    Checks for existing edges before creating (idempotent on replay).
    Returns count of edges created.
    """
    created = 0
    for entity_gid in entity_grace_ids:
        escaped_entity = escape_cypher_string(entity_gid)
        escaped_event_id = escape_cypher_string(event_extraction_id)

        # Check if edge already exists
        check_query = (
            f"MATCH (n {{grace_id: '{escaped_entity}'}})"
            f"-[:produced_by]->"
            f"(e {{extraction_event_id: '{escaped_event_id}'}}) "
            f"RETURN count(*) as cnt"
        )
        result = await client.execute_cypher(check_query)
        rows = result.get("result", [])
        cnt = 0
        if rows:
            row = rows[0]
            cnt = row.get("cnt", 0) if isinstance(row, dict) else 0

        if cnt > 0:
            continue

        # Create edge
        escaped_event_gid = escape_cypher_string(event_grace_id)
        edge_grace_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        create_query = (
            f"MATCH (a {{grace_id: '{escaped_entity}'}}), "
            f"(b {{grace_id: '{escaped_event_gid}'}}) "
            f"CREATE (a)-[:produced_by {{grace_id: '{escape_cypher_string(edge_grace_id)}', "
            f"created_at: '{escape_cypher_string(now)}'}}]->(b) "
            f"RETURN a.grace_id"
        )
        await client.execute_cypher(create_query)
        created += 1

    log.info(
        "provenance.produced_by_edges_created",
        count=created,
        total_entities=len(entity_grace_ids),
    )
    return created


def update_event_status_after_write(
    session: Session,
    event_id: str,
    write_result,
) -> str:
    """Determine and apply terminal status after graph write.

    D107 thresholds:
    - graph_failed: zero successes + at least one failure
    - partial_failed: some successes + some failures
    - graph_written: all writes succeeded
    """
    total_success = (
        write_result.entities_created
        + write_result.entities_matched
        + write_result.relationships_created
    )
    total_failed = write_result.entities_failed + write_result.relationships_failed

    if total_success == 0 and total_failed > 0:
        target = "graph_failed"
    elif total_failed > 0:
        target = "partial_failed"
    else:
        target = "graph_written"

    validate_status_transition("verified", target)
    update_extraction_event_status(session, event_id, target, {
        "completed_at": datetime.now(UTC),
    })
    log.info(
        "provenance.event_status_updated",
        event_id=event_id,
        target_status=target,
    )
    return target


async def reconciliation_check(
    client: ArcadeClient,
    session: Session,
) -> dict:
    """Idempotent dual-write reconciliation.

    Queries PostgreSQL for events with status='verified'. For each,
    checks if the corresponding Extraction_Event vertex exists in ArcadeDB.
    Promotes to 'graph_written' if found, logs warning if not.
    """
    from sqlalchemy import select
    from src.extraction.claim_database import extraction_events_pg

    stmt = select(extraction_events_pg).where(
        extraction_events_pg.c.status == "verified"
    )
    rows = session.execute(stmt).all()

    promoted = 0
    warnings = 0
    checked = len(rows)

    for row in rows:
        event_id = str(row.event_id)
        escaped = escape_cypher_string(event_id)
        query = (
            f"MATCH (e:Extraction_Event {{extraction_event_id: '{escaped}'}}) "
            f"RETURN e.grace_id LIMIT 1"
        )
        result = await client.execute_cypher(query)
        arcade_rows = result.get("result", [])

        if arcade_rows:
            update_extraction_event_status(session, event_id, "graph_written", {
                "completed_at": datetime.now(UTC),
            })
            promoted += 1
            log.info("reconciliation.promoted", event_id=event_id)
        else:
            warnings += 1
            log.warning(
                "reconciliation.no_vertex",
                event_id=event_id,
                msg="PostgreSQL event verified but no ArcadeDB vertex found",
            )

    return {"promoted": promoted, "warnings": warnings, "checked": checked}
