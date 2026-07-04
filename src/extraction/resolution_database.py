"""PostgreSQL CRUD operations for entity resolution logs.

Uses SQLAlchemy 2.0 (sync) with psycopg2-binary, matching the existing
pattern in claim_database.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

log = structlog.get_logger()

metadata = MetaData()

entity_resolution_log = Table(
    "entity_resolution_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("extracted_name", String(500), nullable=False),
    Column("extracted_type", String(100), nullable=False),
    Column("matched_grace_id", PG_UUID(as_uuid=True), nullable=True),
    Column("matched_name", String(500), nullable=True),
    Column("resolution_tier", String(20), nullable=False),
    Column("similarity_score", Float, nullable=True),
    Column("blocking_key", String(200), nullable=False),
    Column("candidate_count", Integer, nullable=True),
    Column("candidates_json", JSONB, nullable=True),
    Column("resolution_note", String(200), nullable=True),
    Column("extraction_event_id", PG_UUID(as_uuid=True), nullable=True),
    Column("batch_id", String(64), nullable=True),
    Column("resolved_at", DateTime(timezone=True), nullable=False),
)


def _result_to_row(result, extraction_event_id: str | None, batch_id: str | None) -> dict:
    """Convert an EntityResolutionResult to a dict for insertion."""
    return {
        "extracted_name": result.extracted_name,
        "extracted_type": result.extracted_type,
        "matched_grace_id": UUID(result.resolved_grace_id) if result.resolved_grace_id else None,
        "matched_name": result.matched_name,
        "resolution_tier": result.resolution_tier,
        "similarity_score": result.similarity_score,
        "blocking_key": result.blocking_key,
        "candidate_count": result.candidate_count,
        "candidates_json": result.candidates_json,
        "resolution_note": result.resolution_note,
        "extraction_event_id": UUID(extraction_event_id) if extraction_event_id else None,
        "batch_id": batch_id,
        "resolved_at": datetime.now(UTC),
    }


def insert_resolution_log(
    session: Session,
    result,
    extraction_event_id: str | None = None,
    batch_id: str | None = None,
) -> int:
    """Insert single resolution log entry. Returns row ID."""
    row_data = _result_to_row(result, extraction_event_id, batch_id)
    stmt = entity_resolution_log.insert().values(**row_data).returning(entity_resolution_log.c.id)
    row = session.execute(stmt).scalar()
    session.flush()
    log.info(
        "resolution_log_inserted",
        extracted_name=result.extracted_name,
        tier=result.resolution_tier,
    )
    return row


def insert_resolution_logs_batch(
    session: Session,
    results: list,
    extraction_event_id: str | None = None,
    batch_id: str | None = None,
) -> int:
    """Batch insert resolution logs. Returns count inserted."""
    if not results:
        return 0
    rows = [_result_to_row(r, extraction_event_id, batch_id) for r in results]
    session.execute(entity_resolution_log.insert(), rows)
    session.flush()
    log.info("resolution_logs_batch_inserted", count=len(results))
    return len(results)


def get_resolution_stats(
    session: Session,
    extraction_event_id: str | None = None,
) -> dict:
    """Returns counts by tier, avg similarity per tier, new vs matched ratio.

    Filters resolution_note IS NULL to exclude failure-induced decisions
    from calibration metrics.
    """
    base_filter = entity_resolution_log.c.resolution_note.is_(None)
    if extraction_event_id:
        base_filter = base_filter & (
            entity_resolution_log.c.extraction_event_id == UUID(extraction_event_id)
        )

    # Tier counts
    stmt = (
        select(
            entity_resolution_log.c.resolution_tier,
            func.count().label("count"),
            func.avg(entity_resolution_log.c.similarity_score).label("avg_similarity"),
        )
        .where(base_filter)
        .group_by(entity_resolution_log.c.resolution_tier)
    )
    rows = session.execute(stmt).all()

    tier_counts: dict[str, int] = {}
    avg_similarity_by_tier: dict[str, float | None] = {}
    total = 0
    new_count = 0
    matched_count = 0

    for row in rows:
        tier = row.resolution_tier
        count = row.count
        tier_counts[tier] = count
        avg_similarity_by_tier[tier] = float(row.avg_similarity) if row.avg_similarity is not None else None
        total += count
        if tier == "new":
            new_count += count
        else:
            matched_count += count

    return {
        "tier_counts": tier_counts,
        "avg_similarity_by_tier": avg_similarity_by_tier,
        "total": total,
        "new_count": new_count,
        "matched_count": matched_count,
        "new_ratio": new_count / total if total > 0 else 0.0,
        "matched_ratio": matched_count / total if total > 0 else 0.0,
    }


def get_resolution_history(
    session: Session,
    entity_name: str | None = None,
    entity_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Query resolution log with optional filters. For debugging."""
    stmt = select(entity_resolution_log)
    if entity_name is not None:
        stmt = stmt.where(entity_resolution_log.c.extracted_name == entity_name)
    if entity_type is not None:
        stmt = stmt.where(entity_resolution_log.c.extracted_type == entity_type)
    stmt = stmt.order_by(entity_resolution_log.c.resolved_at.desc()).offset(offset).limit(limit)
    rows = session.execute(stmt).all()
    return [
        {
            "id": row.id,
            "extracted_name": row.extracted_name,
            "extracted_type": row.extracted_type,
            "matched_grace_id": str(row.matched_grace_id) if row.matched_grace_id else None,
            "matched_name": row.matched_name,
            "resolution_tier": row.resolution_tier,
            "similarity_score": row.similarity_score,
            "blocking_key": row.blocking_key,
            "candidate_count": row.candidate_count,
            "candidates_json": row.candidates_json,
            "resolution_note": row.resolution_note,
            "extraction_event_id": str(row.extraction_event_id) if row.extraction_event_id else None,
            "batch_id": row.batch_id,
            "resolved_at": row.resolved_at,
        }
        for row in rows
    ]
