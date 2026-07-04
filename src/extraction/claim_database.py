"""PostgreSQL CRUD operations for extraction claims and events.

Uses SQLAlchemy 2.0 (sync) with psycopg2-binary, matching the existing
pattern in src/ontology/database.py and src/graph/schema_sync_database.py.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table, select, update
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict, ConstraintViolation, EvidenceSpan

log = structlog.get_logger()

metadata = MetaData()

extraction_claims = Table(
    "extraction_claims",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", PG_UUID(as_uuid=True), nullable=False),
    Column("claim_fingerprint", String(64), nullable=True),
    Column("extraction_unit_id", String(64), nullable=False),
    Column("entity_type", String(100), nullable=True),
    Column("relationship_type", String(100), nullable=True),
    Column("subject_name", String(500), nullable=False),
    Column("predicate", String(200), nullable=False),
    Column("object_name", String(500), nullable=True),
    Column("subject_type", String(100), nullable=True),
    Column("object_type", String(100), nullable=True),
    Column("properties_json", JSONB, nullable=True),
    Column("evidence_spans", JSONB, nullable=True),
    Column("verdict", String(20), nullable=True),
    Column("confidence", Float, nullable=True),
    Column("status", String(20), nullable=False),
    Column("decision_source", String(20), nullable=False),
    Column("constraint_violations", JSONB, nullable=True),
    Column("supersedes_claim_id", PG_UUID(as_uuid=True), nullable=True),
    Column("source_document_id", String(200), nullable=False),
    Column("source_chunk_id", String(200), nullable=False),
    Column("ontology_module", String(100), nullable=True),
    Column("schema_version", Integer, nullable=True),
    Column("prompt_template_id", String(100), nullable=True),
    Column("model_name", String(100), nullable=True),
    Column("model_temperature", Float, nullable=True),
    Column("model_max_tokens", Integer, nullable=True),
    Column("extraction_event_id", PG_UUID(as_uuid=True), nullable=True),
    Column("verifier_model", String(100), nullable=True),
    Column("contradiction_reason", String(2000), nullable=True),
    Column("resolved_entity_grace_id", PG_UUID(as_uuid=True), nullable=True),
    Column("resolved_subject_grace_id", PG_UUID(as_uuid=True), nullable=True),
    Column("resolved_object_grace_id", PG_UUID(as_uuid=True), nullable=True),
    Column("resolution_note", String(200), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

extraction_events_pg = Table(
    "extraction_events_pg",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_id", PG_UUID(as_uuid=True), nullable=False),
    Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
    Column("source_document_id", String(200), nullable=False),
    Column("ontology_module", String(100), nullable=True),
    Column("schema_version", Integer, nullable=True),
    Column("provider_used", String(50), nullable=True),
    Column("model_used", String(100), nullable=True),
    Column("chunks_total", Integer, nullable=True),
    Column("chunks_succeeded", Integer, nullable=True),
    Column("chunks_failed", Integer, nullable=True),
    Column("entities_extracted", Integer, nullable=True),
    Column("relationships_extracted", Integer, nullable=True),
    Column("claims_accepted", Integer, nullable=True),
    Column("claims_quarantined", Integer, nullable=True),
    Column("avg_confidence", Float, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def _claim_to_row(claim: Claim) -> dict:
    """Convert a Claim Pydantic model to a dict for insertion."""
    return {
        "claim_id": UUID(claim.claim_id),
        "claim_fingerprint": claim.claim_fingerprint or None,
        "extraction_unit_id": claim.extraction_unit_id,
        "entity_type": claim.entity_type,
        "relationship_type": claim.relationship_type,
        "subject_name": claim.subject_name,
        "predicate": claim.predicate,
        "object_name": claim.object_name,
        "subject_type": claim.subject_type,
        "object_type": claim.object_type,
        "properties_json": claim.properties_json or None,
        "evidence_spans": [es.model_dump() for es in claim.evidence_spans] if claim.evidence_spans else None,
        "verdict": claim.verdict.value if claim.verdict else None,
        "confidence": claim.confidence,
        "status": claim.status.value,
        "decision_source": claim.decision_source,
        "constraint_violations": (
            [cv.model_dump() for cv in claim.constraint_violations]
            if claim.constraint_violations
            else None
        ),
        "supersedes_claim_id": UUID(claim.supersedes_claim_id) if claim.supersedes_claim_id else None,
        "source_document_id": claim.source_document_id,
        "source_chunk_id": claim.source_chunk_id,
        "ontology_module": claim.ontology_module,
        "schema_version": claim.schema_version,
        "prompt_template_id": claim.prompt_template_id or None,
        "model_name": claim.model_name or None,
        "model_temperature": claim.model_temperature,
        "model_max_tokens": claim.model_max_tokens,
        "extraction_event_id": UUID(claim.extraction_event_id) if claim.extraction_event_id else None,
        "verifier_model": claim.verifier_model,
        "contradiction_reason": claim.contradiction_reason or None,
        "resolved_entity_grace_id": UUID(claim.resolved_entity_grace_id) if claim.resolved_entity_grace_id else None,
        "resolved_subject_grace_id": UUID(claim.resolved_subject_grace_id) if claim.resolved_subject_grace_id else None,
        "resolved_object_grace_id": UUID(claim.resolved_object_grace_id) if claim.resolved_object_grace_id else None,
        "resolution_note": claim.resolution_note,
        "created_at": claim.created_at,
    }


def _row_to_claim(row) -> Claim:
    """Convert a database row to a Claim Pydantic model."""
    evidence_spans = []
    if row.evidence_spans:
        evidence_spans = [EvidenceSpan(**es) for es in row.evidence_spans]

    return Claim(
        claim_id=str(row.claim_id),
        claim_fingerprint=row.claim_fingerprint or "",
        extraction_unit_id=row.extraction_unit_id,
        entity_type=row.entity_type,
        relationship_type=row.relationship_type,
        subject_name=row.subject_name,
        predicate=row.predicate,
        object_name=row.object_name,
        subject_type=row.subject_type,
        object_type=row.object_type,
        properties_json=row.properties_json or {},
        evidence_spans=evidence_spans,
        verdict=ClaimVerdict(row.verdict) if row.verdict else ClaimVerdict.PENDING,
        confidence=row.confidence,
        status=ClaimStatus(row.status),
        decision_source=row.decision_source,
        constraint_violations=[
            ConstraintViolation.model_validate(v)
            for v in (row.constraint_violations or [])
        ],
        supersedes_claim_id=str(row.supersedes_claim_id) if row.supersedes_claim_id else None,
        source_document_id=row.source_document_id,
        source_chunk_id=row.source_chunk_id,
        ontology_module=row.ontology_module,
        schema_version=row.schema_version,
        prompt_template_id=row.prompt_template_id or "",
        model_name=row.model_name or "",
        model_temperature=row.model_temperature or 0.0,
        model_max_tokens=row.model_max_tokens or 0,
        extraction_event_id=str(row.extraction_event_id) if row.extraction_event_id else None,
        verifier_model=row.verifier_model,
        contradiction_reason=row.contradiction_reason or "",
        resolved_entity_grace_id=str(row.resolved_entity_grace_id) if row.resolved_entity_grace_id else None,
        resolved_subject_grace_id=str(row.resolved_subject_grace_id) if row.resolved_subject_grace_id else None,
        resolved_object_grace_id=str(row.resolved_object_grace_id) if row.resolved_object_grace_id else None,
        resolution_note=row.resolution_note,
        created_at=row.created_at,
    )


# --- Claims CRUD ---


def insert_claim(session: Session, claim: Claim) -> str:
    """Insert a single claim. Returns claim_id."""
    row_data = _claim_to_row(claim)
    session.execute(extraction_claims.insert().values(**row_data))
    session.flush()
    log.info("claim_inserted", claim_id=claim.claim_id)
    return claim.claim_id


def insert_claims_batch(session: Session, claims: list[Claim]) -> int:
    """Insert multiple claims in one transaction. Returns count inserted."""
    if not claims:
        return 0
    rows = [_claim_to_row(c) for c in claims]
    session.execute(extraction_claims.insert(), rows)
    session.flush()
    log.info("claims_batch_inserted", count=len(claims))
    return len(claims)


def get_claim(session: Session, claim_id: str) -> Claim | None:
    """Retrieve a claim by claim_id."""
    stmt = select(extraction_claims).where(
        extraction_claims.c.claim_id == UUID(claim_id)
    )
    row = session.execute(stmt).first()
    return _row_to_claim(row) if row else None


def list_claims(
    session: Session,
    status: ClaimStatus | None = None,
    verdict: ClaimVerdict | None = None,
    source_document_id: str | None = None,
    ontology_module: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Claim]:
    """List claims with optional filters and pagination."""
    stmt = select(extraction_claims)
    if status is not None:
        stmt = stmt.where(extraction_claims.c.status == status.value)
    if verdict is not None:
        stmt = stmt.where(extraction_claims.c.verdict == verdict.value)
    if source_document_id is not None:
        stmt = stmt.where(extraction_claims.c.source_document_id == source_document_id)
    if ontology_module is not None:
        stmt = stmt.where(extraction_claims.c.ontology_module == ontology_module)
    stmt = stmt.order_by(extraction_claims.c.created_at.desc()).offset(offset).limit(limit)
    rows = session.execute(stmt).all()
    return [_row_to_claim(row) for row in rows]


def update_claim_verdict(
    session: Session, claim_id: str, verdict: ClaimVerdict, confidence: float
) -> bool:
    """Update verdict and confidence for a claim. Returns True if updated."""
    stmt = (
        update(extraction_claims)
        .where(extraction_claims.c.claim_id == UUID(claim_id))
        .values(verdict=verdict.value, confidence=confidence)
    )
    result = session.execute(stmt)
    session.flush()
    return result.rowcount > 0


def update_claim_status(
    session: Session, claim_id: str, status: ClaimStatus, decision_source: str
) -> bool:
    """Update status and decision_source for a claim. Returns True if updated."""
    stmt = (
        update(extraction_claims)
        .where(extraction_claims.c.claim_id == UUID(claim_id))
        .values(status=status.value, decision_source=decision_source)
    )
    result = session.execute(stmt)
    session.flush()
    return result.rowcount > 0


def update_claim_violations(
    session: Session,
    claim_id: str,
    violations: list,
    decision_source: str = "validator",
) -> bool:
    """Update constraint_violations and decision_source on a claim. Returns True if updated."""
    violations_data = [v.model_dump() for v in violations] if violations else None
    stmt = (
        update(extraction_claims)
        .where(extraction_claims.c.claim_id == UUID(claim_id))
        .values(constraint_violations=violations_data, decision_source=decision_source)
    )
    result = session.execute(stmt)
    session.flush()
    return result.rowcount > 0


def update_claim_resolved_endpoints(
    session: Session,
    claim_id: str,
    resolved_subject_grace_id: str | None = None,
    resolved_object_grace_id: str | None = None,
    violations: list | None = None,
) -> bool:
    """Update resolved endpoint grace_ids and optionally violations. Returns True if updated."""
    values: dict = {}
    if resolved_subject_grace_id is not None:
        values["resolved_subject_grace_id"] = UUID(resolved_subject_grace_id)
    if resolved_object_grace_id is not None:
        values["resolved_object_grace_id"] = UUID(resolved_object_grace_id)
    if violations is not None:
        values["constraint_violations"] = [v.model_dump() for v in violations] if violations else None
    if not values:
        return False
    stmt = (
        update(extraction_claims)
        .where(extraction_claims.c.claim_id == UUID(claim_id))
        .values(**values)
    )
    result = session.execute(stmt)
    session.flush()
    return result.rowcount > 0


def check_extraction_unit_exists(session: Session, extraction_unit_id: str) -> bool:
    """Check if an extraction unit ID already exists in the claims table."""
    stmt = select(extraction_claims.c.id).where(
        extraction_claims.c.extraction_unit_id == extraction_unit_id
    ).limit(1)
    row = session.execute(stmt).first()
    return row is not None


# --- Extraction Events CRUD ---


def insert_extraction_event(session: Session, event: dict) -> str:
    """Insert an extraction event. Returns event_id."""
    event_id = event.get("event_id", str(uuid4()))
    row_data = {
        "event_id": UUID(event_id),
        "batch_id": UUID(event["batch_id"]),
        "source_document_id": event["source_document_id"],
        "ontology_module": event.get("ontology_module"),
        "schema_version": event.get("schema_version"),
        "provider_used": event.get("provider_used"),
        "model_used": event.get("model_used"),
        "chunks_total": event.get("chunks_total"),
        "chunks_succeeded": event.get("chunks_succeeded"),
        "chunks_failed": event.get("chunks_failed"),
        "entities_extracted": event.get("entities_extracted"),
        "relationships_extracted": event.get("relationships_extracted"),
        "claims_accepted": event.get("claims_accepted"),
        "claims_quarantined": event.get("claims_quarantined"),
        "avg_confidence": event.get("avg_confidence"),
        "started_at": event.get("started_at"),
        "completed_at": event.get("completed_at"),
        "status": event.get("status", "running"),
        "created_at": datetime.now(UTC),
    }
    session.execute(extraction_events_pg.insert().values(**row_data))
    session.flush()
    log.info("extraction_event_inserted", event_id=event_id)
    return event_id


def get_extraction_event(session: Session, event_id: str) -> dict | None:
    """Retrieve an extraction event by event_id."""
    stmt = select(extraction_events_pg).where(
        extraction_events_pg.c.event_id == UUID(event_id)
    )
    row = session.execute(stmt).first()
    if not row:
        return None
    return {
        "event_id": str(row.event_id),
        "batch_id": str(row.batch_id),
        "source_document_id": row.source_document_id,
        "ontology_module": row.ontology_module,
        "schema_version": row.schema_version,
        "provider_used": row.provider_used,
        "model_used": row.model_used,
        "chunks_total": row.chunks_total,
        "chunks_succeeded": row.chunks_succeeded,
        "chunks_failed": row.chunks_failed,
        "entities_extracted": row.entities_extracted,
        "relationships_extracted": row.relationships_extracted,
        "claims_accepted": row.claims_accepted,
        "claims_quarantined": row.claims_quarantined,
        "avg_confidence": row.avg_confidence,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "status": row.status,
        "created_at": row.created_at,
    }


def update_extraction_event_status(
    session: Session, event_id: str, status: str, metrics: dict | None = None
) -> bool:
    """Update status and optional metrics for an extraction event. Returns True if updated."""
    values: dict = {"status": status}
    if metrics:
        for key in (
            "chunks_total",
            "chunks_succeeded",
            "chunks_failed",
            "entities_extracted",
            "relationships_extracted",
            "claims_accepted",
            "claims_quarantined",
            "avg_confidence",
            "completed_at",
        ):
            if key in metrics:
                values[key] = metrics[key]
    stmt = (
        update(extraction_events_pg)
        .where(extraction_events_pg.c.event_id == UUID(event_id))
        .values(**values)
    )
    result = session.execute(stmt)
    session.flush()
    return result.rowcount > 0
