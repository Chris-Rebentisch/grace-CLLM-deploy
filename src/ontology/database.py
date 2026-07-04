"""SQLAlchemy ORM tables and CRUD operations for Ontology Management."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.ontology.evidence_bundle import EvidenceBundle, evidence_bundle_from_db
from src.ontology.models import (
    CalibrationDecision,
    CalibrationRecord,
    HumanDecision,
    OntologyVersion,
    ProposalPriority,
    ProposalStatus,
    ProposalType,
    SchemaPromotionEvent,
    SchemaProposal,
    SignalType,
    TrustScore,
    VersionSource,
)
from src.shared.database import Base

log = structlog.get_logger()


# --- ORM Row Classes ---


class OntologyVersionRow(Base):
    """SQLAlchemy ORM model for the ontology_versions table."""

    __tablename__ = "ontology_versions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    version_number = Column(Integer, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    schema_json = Column(JSONB, nullable=False)
    schema_modules = Column(JSONB, nullable=False)
    patch_json = Column(JSONB, nullable=True)
    diff_summary = Column(JSONB, nullable=True)
    previous_version_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("ontology_versions.id"), nullable=True
    )
    hash_chain = Column(Text, nullable=False)
    source = Column(String(30), nullable=False)
    proposal_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("schema_proposals.id", use_alter=True),
        nullable=True,
    )
    reviewer = Column(Text, nullable=True)
    changelog = Column(Text, nullable=True)
    kgcl_commands = Column(JSONB, nullable=True)
    cq_coverage_snapshot = Column(JSONB, nullable=True)
    entity_type_count = Column(Integer, nullable=True)
    relationship_type_count = Column(Integer, nullable=True)
    promotion_gate_passed = Column(Boolean, nullable=True)
    promotion_gate_details = Column(JSONB, nullable=True)
    is_active = Column(Boolean, nullable=False, default=False)
    metadata_extra = Column(JSONB, default={})
    # D278 (Chunk 36): per-(segment_id, reviewer) ontology version
    # coexistence. Loose-string TEXT NULL; forward FK to a segments table
    # is deferred to Chunk 40.
    segment_id = Column(Text, nullable=True)

    __table_args__ = (
        # version_number unique index handled by unique=True on column
        # Partial index for fast active version lookup
    )


class SchemaProposalRow(Base):
    """SQLAlchemy ORM model for the schema_proposals table."""

    __tablename__ = "schema_proposals"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    proposal_type = Column(String(30), nullable=False)
    change_tier = Column(Integer, nullable=False)
    kgcl_command = Column(Text, nullable=False)
    proposed_diff = Column(JSONB, nullable=False)
    evidence = Column(JSONB, nullable=False)
    signal_type = Column(String(20), nullable=True)
    # F-0042 / ISS-0053 deferral closure: nullable — human-initiated /
    # signal-less proposals store NULL, never a fabricated 1.0 (D120/D217).
    # Migration: r4a_raw_confidence_nullable.
    raw_confidence = Column(Float, nullable=True)
    priority = Column(String(10), nullable=False, default="medium")
    status = Column(String(20), nullable=False, default="pending")
    current_schema_version_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("ontology_versions.id"), nullable=False
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewer = Column(Text, nullable=True)
    human_decision = Column(String(20), nullable=True)
    modification_distance = Column(Float, nullable=True)
    modified_diff = Column(JSONB, nullable=True)
    applied_autonomously = Column(Boolean, nullable=False, default=False)
    autonomy_confidence_at_time = Column(Float, nullable=True)
    trust_score_at_time = Column(Float, nullable=True)
    resulting_version_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("ontology_versions.id", use_alter=True),
        nullable=True,
    )
    cooling_period_expires_at = Column(DateTime(timezone=True), nullable=True)
    cooling_period_reverted = Column(Boolean, nullable=True)
    # Chunk 50 (D399) — cooling-period state columns.
    cooling_outcome = Column(String(20), nullable=True)
    reverted_at = Column(DateTime(timezone=True), nullable=True)
    reverted_by = Column(Text, nullable=True)
    reverted_proposal_id = Column(PG_UUID(as_uuid=True), ForeignKey("schema_proposals.id"), nullable=True)
    metadata_extra = Column(JSONB, default={})
    # Chunk 65 (D448) — correction carve-out column.
    is_correction = Column(Boolean, nullable=False, default=False, server_default="false")
    # Chunk 47 (D387) — additive columns for signal→proposal pipeline.
    ontology_module = Column(Text, nullable=True)
    dedup_hash = Column(String(64), nullable=True)
    overflow = Column(Boolean, nullable=False, default=False)
    generated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = ()


class CalibrationRecordRow(Base):
    """SQLAlchemy ORM model for the calibration_records table."""

    __tablename__ = "calibration_records"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    computed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    change_tier = Column(Integer, nullable=False)
    confidence_band_low = Column(Float, nullable=False)
    confidence_band_high = Column(Float, nullable=False)
    approval_rate = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False)
    trust_score = Column(Float, nullable=False)
    autonomy_threshold = Column(Float, nullable=False)
    autonomy_enabled = Column(Boolean, nullable=False, default=False)
    window_size = Column(Integer, nullable=False, default=50)
    risk_tolerance = Column(Float, nullable=False, default=0.95)

    __table_args__ = ()


class CalibrationDecisionRow(Base):
    """SQLAlchemy ORM model for the calibration_decisions table (Chunk 49, D394)."""

    __tablename__ = "calibration_decisions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("schema_proposals.id"), nullable=False
    )
    change_tier = Column(Integer, nullable=False)
    raw_confidence = Column(Float, nullable=False)
    decision = Column(String(20), nullable=False)
    modification_distance = Column(Float, nullable=True)
    ontology_module = Column(Text, nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = ()


class TrustScoreRow(Base):
    """SQLAlchemy ORM model for the trust_scores table (Chunk 49, D394)."""

    __tablename__ = "trust_scores"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tier = Column(Integer, nullable=False, unique=True)
    trust_score = Column(Float, nullable=False, default=0.0)
    autonomy_threshold = Column(Float, nullable=False, default=0.95)
    autonomy_enabled = Column(Boolean, nullable=False, default=False)
    window_size = Column(Integer, nullable=False, default=50)
    min_reviews_for_calibration = Column(Integer, nullable=False, default=50)
    risk_tolerance = Column(Float, nullable=False, default=0.95)
    total_decisions = Column(Integer, nullable=False, default=0)
    regression_detected = Column(Boolean, nullable=False, default=False)
    last_computed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = ()


class GovernanceDecisionEventRow(Base):
    """SQLAlchemy ORM for governance_decision_events (Chunk 50, D398)."""

    __tablename__ = "governance_decision_events"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    decision_type = Column(String(40), nullable=False)
    agent_id = Column(Text, nullable=True)
    proposal_id = Column(PG_UUID(as_uuid=True), ForeignKey("schema_proposals.id"), nullable=True)
    schema_version_id = Column(PG_UUID(as_uuid=True), nullable=True)
    tier = Column(Integer, nullable=True)
    trust_score_at_time = Column(Float, nullable=True)
    outcome = Column(String(20), nullable=True)
    reason = Column(Text, nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = ()


class KillSwitchHistoryRow(Base):
    """SQLAlchemy ORM for kill_switch_history (Chunk 65, D447).

    Append-only table recording kill-switch engage/disengage cycles with
    per-tier state snapshots, paired to elicitation events via session_id.
    """

    __tablename__ = "kill_switch_history"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    engaged_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    disengaged_at = Column(DateTime(timezone=True), nullable=True)
    engaged_by = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    previous_state = Column(JSONB, nullable=False)
    restored_state = Column(JSONB, nullable=True)
    related_elicitation_event_id = Column(PG_UUID(as_uuid=True), nullable=True)

    __table_args__ = ()


class SchemaPromotionEventRow(Base):
    """SQLAlchemy ORM model for the schema_promotion_events table."""

    __tablename__ = "schema_promotion_events"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    proposal_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("schema_proposals.id"), nullable=False
    )
    schema_version_before_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("ontology_versions.id"), nullable=False
    )
    proposed_schema_json = Column(JSONB, nullable=False)
    cq_pass_rate = Column(Float, nullable=True)
    cq_total = Column(Integer, nullable=True)
    cq_passing = Column(Integer, nullable=True)
    mine1_retention = Column(Float, nullable=True)
    mine1_sample_size = Column(Integer, nullable=True)
    gate_passed = Column(Boolean, nullable=False)
    gate_details = Column(JSONB, nullable=True)

    __table_args__ = ()


# --- Row-to-Model / Model-to-Row Converters ---


def _version_row_to_model(row: OntologyVersionRow) -> OntologyVersion:
    """Convert a SQLAlchemy OntologyVersionRow to a Pydantic OntologyVersion."""
    return OntologyVersion(
        id=row.id,
        version_number=row.version_number,
        created_at=row.created_at,
        schema_json=row.schema_json,
        schema_modules=row.schema_modules,
        patch_json=row.patch_json,
        diff_summary=row.diff_summary,
        previous_version_id=row.previous_version_id,
        hash_chain=row.hash_chain,
        source=VersionSource(row.source),
        proposal_id=row.proposal_id,
        reviewer=row.reviewer,
        changelog=row.changelog,
        kgcl_commands=row.kgcl_commands,
        cq_coverage_snapshot=row.cq_coverage_snapshot,
        entity_type_count=row.entity_type_count,
        relationship_type_count=row.relationship_type_count,
        promotion_gate_passed=row.promotion_gate_passed,
        promotion_gate_details=row.promotion_gate_details,
        is_active=row.is_active,
        metadata_extra=row.metadata_extra or {},
    )


def _version_model_to_row(version: OntologyVersion) -> OntologyVersionRow:
    """Convert a Pydantic OntologyVersion to a SQLAlchemy OntologyVersionRow."""
    return OntologyVersionRow(
        id=version.id,
        version_number=version.version_number,
        created_at=version.created_at,
        schema_json=version.schema_json,
        schema_modules=version.schema_modules,
        patch_json=version.patch_json,
        diff_summary=version.diff_summary,
        previous_version_id=version.previous_version_id,
        hash_chain=version.hash_chain,
        source=version.source.value,
        proposal_id=version.proposal_id,
        reviewer=version.reviewer,
        changelog=version.changelog,
        kgcl_commands=version.kgcl_commands,
        cq_coverage_snapshot=version.cq_coverage_snapshot,
        entity_type_count=version.entity_type_count,
        relationship_type_count=version.relationship_type_count,
        promotion_gate_passed=version.promotion_gate_passed,
        promotion_gate_details=version.promotion_gate_details,
        is_active=version.is_active,
        metadata_extra=version.metadata_extra,
    )


def _proposal_row_to_model(row: SchemaProposalRow) -> SchemaProposal:
    """Convert a SQLAlchemy SchemaProposalRow to a Pydantic SchemaProposal."""
    return SchemaProposal(
        id=row.id,
        created_at=row.created_at,
        proposal_type=ProposalType(row.proposal_type),
        change_tier=row.change_tier,
        kgcl_command=row.kgcl_command,
        proposed_diff=row.proposed_diff,
        evidence=evidence_bundle_from_db(row.evidence),
        signal_type=SignalType(row.signal_type) if row.signal_type else None,
        raw_confidence=row.raw_confidence,
        priority=ProposalPriority(row.priority),
        status=ProposalStatus(row.status),
        current_schema_version_id=row.current_schema_version_id,
        reviewed_at=row.reviewed_at,
        reviewer=row.reviewer,
        human_decision=HumanDecision(row.human_decision) if row.human_decision else None,
        modification_distance=row.modification_distance,
        modified_diff=row.modified_diff,
        applied_autonomously=row.applied_autonomously,
        autonomy_confidence_at_time=row.autonomy_confidence_at_time,
        trust_score_at_time=row.trust_score_at_time,
        resulting_version_id=row.resulting_version_id,
        cooling_period_expires_at=row.cooling_period_expires_at,
        cooling_period_reverted=row.cooling_period_reverted,
        cooling_outcome=row.cooling_outcome,
        reverted_at=row.reverted_at,
        reverted_by=row.reverted_by,
        reverted_proposal_id=str(row.reverted_proposal_id) if row.reverted_proposal_id else None,
        metadata_extra=row.metadata_extra or {},
        ontology_module=row.ontology_module,
        dedup_hash=row.dedup_hash,
        overflow=row.overflow or False,
        generated_at=row.generated_at,
    )


def _proposal_model_to_row(proposal: SchemaProposal) -> SchemaProposalRow:
    """Convert a Pydantic SchemaProposal to a SQLAlchemy SchemaProposalRow."""
    return SchemaProposalRow(
        id=proposal.id,
        created_at=proposal.created_at,
        proposal_type=proposal.proposal_type.value,
        change_tier=proposal.change_tier,
        kgcl_command=proposal.kgcl_command,
        proposed_diff=proposal.proposed_diff,
        evidence=proposal.evidence.model_dump(mode="json") if isinstance(proposal.evidence, EvidenceBundle) else proposal.evidence,
        signal_type=proposal.signal_type.value if proposal.signal_type else None,
        raw_confidence=proposal.raw_confidence,
        priority=proposal.priority.value,
        status=proposal.status.value,
        current_schema_version_id=proposal.current_schema_version_id,
        reviewed_at=proposal.reviewed_at,
        reviewer=proposal.reviewer,
        human_decision=proposal.human_decision.value if proposal.human_decision else None,
        modification_distance=proposal.modification_distance,
        modified_diff=proposal.modified_diff,
        applied_autonomously=proposal.applied_autonomously,
        autonomy_confidence_at_time=proposal.autonomy_confidence_at_time,
        trust_score_at_time=proposal.trust_score_at_time,
        resulting_version_id=proposal.resulting_version_id,
        cooling_period_expires_at=proposal.cooling_period_expires_at,
        cooling_period_reverted=proposal.cooling_period_reverted,
        cooling_outcome=proposal.cooling_outcome,
        reverted_at=proposal.reverted_at,
        reverted_by=proposal.reverted_by,
        reverted_proposal_id=proposal.reverted_proposal_id,
        metadata_extra=proposal.metadata_extra,
        ontology_module=proposal.ontology_module,
        dedup_hash=proposal.dedup_hash,
        overflow=proposal.overflow,
        generated_at=proposal.generated_at,
    )


def _calibration_row_to_model(row: CalibrationRecordRow) -> CalibrationRecord:
    """Convert a SQLAlchemy CalibrationRecordRow to a Pydantic CalibrationRecord."""
    return CalibrationRecord(
        id=row.id,
        computed_at=row.computed_at,
        change_tier=row.change_tier,
        confidence_band_low=row.confidence_band_low,
        confidence_band_high=row.confidence_band_high,
        approval_rate=row.approval_rate,
        sample_count=row.sample_count,
        trust_score=row.trust_score,
        autonomy_threshold=row.autonomy_threshold,
        autonomy_enabled=row.autonomy_enabled,
        window_size=row.window_size,
        risk_tolerance=row.risk_tolerance,
    )


def _calibration_model_to_row(record: CalibrationRecord) -> CalibrationRecordRow:
    """Convert a Pydantic CalibrationRecord to a SQLAlchemy CalibrationRecordRow."""
    return CalibrationRecordRow(
        id=record.id,
        computed_at=record.computed_at,
        change_tier=record.change_tier,
        confidence_band_low=record.confidence_band_low,
        confidence_band_high=record.confidence_band_high,
        approval_rate=record.approval_rate,
        sample_count=record.sample_count,
        trust_score=record.trust_score,
        autonomy_threshold=record.autonomy_threshold,
        autonomy_enabled=record.autonomy_enabled,
        window_size=record.window_size,
        risk_tolerance=record.risk_tolerance,
    )


def _promotion_row_to_model(row: SchemaPromotionEventRow) -> SchemaPromotionEvent:
    """Convert a SQLAlchemy SchemaPromotionEventRow to a Pydantic SchemaPromotionEvent."""
    return SchemaPromotionEvent(
        id=row.id,
        created_at=row.created_at,
        proposal_id=row.proposal_id,
        schema_version_before_id=row.schema_version_before_id,
        proposed_schema_json=row.proposed_schema_json,
        cq_pass_rate=row.cq_pass_rate,
        cq_total=row.cq_total,
        cq_passing=row.cq_passing,
        mine1_retention=row.mine1_retention,
        mine1_sample_size=row.mine1_sample_size,
        gate_passed=row.gate_passed,
        gate_details=row.gate_details,
    )


def _promotion_model_to_row(event: SchemaPromotionEvent) -> SchemaPromotionEventRow:
    """Convert a Pydantic SchemaPromotionEvent to a SQLAlchemy SchemaPromotionEventRow."""
    return SchemaPromotionEventRow(
        id=event.id,
        created_at=event.created_at,
        proposal_id=event.proposal_id,
        schema_version_before_id=event.schema_version_before_id,
        proposed_schema_json=event.proposed_schema_json,
        cq_pass_rate=event.cq_pass_rate,
        cq_total=event.cq_total,
        cq_passing=event.cq_passing,
        mine1_retention=event.mine1_retention,
        mine1_sample_size=event.mine1_sample_size,
        gate_passed=event.gate_passed,
        gate_details=event.gate_details,
    )


# --- CRUD Functions: OntologyVersion ---


def create_version(db: Session, version: OntologyVersion) -> OntologyVersion:
    """Insert a new ontology version. Append-only — trigger prevents mutation."""
    row = _version_model_to_row(version)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("ontology_version_created", version_number=version.version_number, version_id=str(version.id))
    return _version_row_to_model(row)


def get_version_by_id(db: Session, version_id: UUID) -> OntologyVersion | None:
    """Retrieve a version by its UUID."""
    row = db.query(OntologyVersionRow).filter(OntologyVersionRow.id == version_id).first()
    return _version_row_to_model(row) if row else None


def get_version_by_number(db: Session, version_number: int) -> OntologyVersion | None:
    """Retrieve a version by its integer version number."""
    row = (
        db.query(OntologyVersionRow)
        .filter(OntologyVersionRow.version_number == version_number)
        .first()
    )
    return _version_row_to_model(row) if row else None


def get_active_version(
    db: Session,
    segment_id: str | None = None,
    reviewer: str | None = None,
) -> OntologyVersion | None:
    """Retrieve the currently active production ontology version.

    D278 (Chunk 36): when both ``segment_id`` and ``reviewer`` are
    provided, the active row is resolved per ``(segment_id, reviewer)``
    coexistence partition. Legacy behavior — global ``is_active=True`` —
    is preserved when either argument is ``None``.
    """
    query = db.query(OntologyVersionRow).filter(
        OntologyVersionRow.is_active.is_(True)
    )
    if segment_id is not None and reviewer is not None:
        query = query.filter(
            OntologyVersionRow.segment_id == segment_id,
            OntologyVersionRow.reviewer == reviewer,
        )
    row = query.first()
    return _version_row_to_model(row) if row else None


def list_versions(db: Session, limit: int = 100, offset: int = 0) -> list[OntologyVersion]:
    """List all versions ordered by version_number descending. Supports pagination."""
    rows = (
        db.query(OntologyVersionRow)
        .order_by(OntologyVersionRow.version_number.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_version_row_to_model(row) for row in rows]


def set_active_version(
    db: Session,
    version_id: UUID,
    segment_id: str | None = None,
    reviewer: str | None = None,
) -> OntologyVersion | None:
    """Set a version as active. Deactivates all others in a single transaction.

    D278 (Chunk 36): when both ``segment_id`` and ``reviewer`` are
    provided, only rows matching that ``(segment_id, reviewer)`` partition
    are deactivated before activating ``version_id``. Legacy behavior —
    global single-active deactivation — is preserved when either argument
    is ``None``.

    Returns the newly active version, or None if version_id not found.
    """
    target = db.query(OntologyVersionRow).filter(OntologyVersionRow.id == version_id).first()
    if target is None:
        return None
    # Deactivate the relevant partition.
    deactivate = db.query(OntologyVersionRow).filter(
        OntologyVersionRow.is_active.is_(True)
    )
    if segment_id is not None and reviewer is not None:
        deactivate = deactivate.filter(
            OntologyVersionRow.segment_id == segment_id,
            OntologyVersionRow.reviewer == reviewer,
        )
    deactivate.update({"is_active": False}, synchronize_session="fetch")
    # Activate the target
    target.is_active = True
    db.commit()
    db.refresh(target)
    log.info("active_version_set", version_id=str(version_id), version_number=target.version_number)
    return _version_row_to_model(target)


def get_next_version_number(db: Session) -> int:
    """Return the next available version number (max + 1, or 1 if no versions exist)."""
    max_num = db.query(func.max(OntologyVersionRow.version_number)).scalar()
    return (max_num or 0) + 1


# --- CRUD Functions: SchemaProposal ---


def create_proposal(db: Session, proposal: SchemaProposal) -> SchemaProposal:
    """Insert a new schema proposal."""
    row = _proposal_model_to_row(proposal)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("schema_proposal_created", proposal_id=str(proposal.id), proposal_type=proposal.proposal_type.value)
    return _proposal_row_to_model(row)


def get_proposal_by_id(db: Session, proposal_id: UUID) -> SchemaProposal | None:
    """Retrieve a proposal by UUID."""
    row = db.query(SchemaProposalRow).filter(SchemaProposalRow.id == proposal_id).first()
    return _proposal_row_to_model(row) if row else None


def list_proposals(
    db: Session,
    status: ProposalStatus | None = None,
    change_tier: int | None = None,
    signal_type: SignalType | None = None,
    ontology_module: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[SchemaProposal]:
    """List proposals with optional filters.

    Tier-weighted FIFO: Tier 3 first, then 2, then 1; within each tier,
    ordered by ``generated_at`` ascending (FIFO). Defensive NULL
    handling: ``generated_at`` may be NULL for legacy rows pre-dating
    Chunk 47 (spec §18.2 expects zero, but sort must not break on NULL).
    """
    query = db.query(SchemaProposalRow)
    if status is not None:
        query = query.filter(SchemaProposalRow.status == status.value)
    if change_tier is not None:
        query = query.filter(SchemaProposalRow.change_tier == change_tier)
    if signal_type is not None:
        query = query.filter(SchemaProposalRow.signal_type == signal_type.value)
    if ontology_module is not None:
        query = query.filter(SchemaProposalRow.ontology_module == ontology_module)
    rows = (
        query.order_by(
            SchemaProposalRow.change_tier.desc(),
            func.coalesce(
                SchemaProposalRow.generated_at,
                SchemaProposalRow.created_at,
            ).asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_proposal_row_to_model(row) for row in rows]


def update_proposal_decision(
    db: Session,
    proposal_id: UUID,
    human_decision: HumanDecision,
    reviewer: str,
    modification_distance: float | None = None,
    modified_diff: dict | None = None,
    resulting_version_id: UUID | None = None,
) -> SchemaProposal | None:
    """Record a human decision on a proposal. Updates status, reviewer, decision fields."""
    row = db.query(SchemaProposalRow).filter(SchemaProposalRow.id == proposal_id).first()
    if row is None:
        return None
    row.human_decision = human_decision.value
    row.reviewer = reviewer
    row.reviewed_at = datetime.now(UTC)
    row.modification_distance = modification_distance
    row.modified_diff = modified_diff
    row.resulting_version_id = resulting_version_id
    # Map human decision to proposal status
    status_map = {
        HumanDecision.APPROVED: ProposalStatus.APPROVED,
        HumanDecision.REJECTED: ProposalStatus.REJECTED,
        HumanDecision.MODIFIED: ProposalStatus.MODIFIED,
        HumanDecision.DEFERRED: ProposalStatus.DEFERRED,
    }
    row.status = status_map[human_decision].value
    db.commit()
    db.refresh(row)
    log.info("proposal_decision_recorded", proposal_id=str(proposal_id), decision=human_decision.value)
    return _proposal_row_to_model(row)


def update_proposal_status(
    db: Session,
    proposal_id: UUID,
    status: ProposalStatus,
    resulting_version_id: UUID | None = None,
    metadata_extra: dict | None = None,
) -> SchemaProposal | None:
    """Update just the status of a proposal (e.g., to 'superseded' or 'applied').

    Chunk 48 (D392): additive ``resulting_version_id`` and ``metadata_extra``
    kwargs for change-executor write-back. Existing callers unaffected.
    """
    row = db.query(SchemaProposalRow).filter(SchemaProposalRow.id == proposal_id).first()
    if row is None:
        return None
    row.status = status.value
    if resulting_version_id is not None:
        row.resulting_version_id = resulting_version_id
    if metadata_extra is not None:
        existing = row.metadata_extra or {}
        existing.update(metadata_extra)
        row.metadata_extra = existing
    db.commit()
    db.refresh(row)
    return _proposal_row_to_model(row)


def get_proposal_summary(db: Session) -> dict:
    """Return counts by status, by tier, by signal_type."""
    status_rows = (
        db.query(SchemaProposalRow.status, func.count())
        .group_by(SchemaProposalRow.status)
        .all()
    )
    by_status = {row[0]: row[1] for row in status_rows}

    tier_rows = (
        db.query(SchemaProposalRow.change_tier, func.count())
        .group_by(SchemaProposalRow.change_tier)
        .all()
    )
    by_tier = {row[0]: row[1] for row in tier_rows}

    signal_rows = (
        db.query(SchemaProposalRow.signal_type, func.count())
        .group_by(SchemaProposalRow.signal_type)
        .all()
    )
    by_signal_type = {row[0]: row[1] for row in signal_rows}

    return {
        "by_status": by_status,
        "by_tier": by_tier,
        "by_signal_type": by_signal_type,
    }


# --- CRUD Functions: CalibrationRecord ---


def create_calibration_record(db: Session, record: CalibrationRecord) -> CalibrationRecord:
    """Insert a new calibration snapshot."""
    row = _calibration_model_to_row(record)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("calibration_record_created", tier=record.change_tier, record_id=str(record.id))
    return _calibration_row_to_model(row)


def get_latest_calibration(db: Session, change_tier: int) -> CalibrationRecord | None:
    """Retrieve the most recent calibration record for a given tier."""
    row = (
        db.query(CalibrationRecordRow)
        .filter(CalibrationRecordRow.change_tier == change_tier)
        .order_by(CalibrationRecordRow.computed_at.desc())
        .first()
    )
    return _calibration_row_to_model(row) if row else None


def list_calibration_history(
    db: Session, change_tier: int, limit: int = 50
) -> list[CalibrationRecord]:
    """List calibration history for a tier, ordered by computed_at descending."""
    rows = (
        db.query(CalibrationRecordRow)
        .filter(CalibrationRecordRow.change_tier == change_tier)
        .order_by(CalibrationRecordRow.computed_at.desc())
        .limit(limit)
        .all()
    )
    return [_calibration_row_to_model(row) for row in rows]


# --- CRUD Functions: SchemaPromotionEvent ---


def create_promotion_event(db: Session, event: SchemaPromotionEvent) -> SchemaPromotionEvent:
    """Insert a quality gate result."""
    row = _promotion_model_to_row(event)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("promotion_event_created", event_id=str(event.id), gate_passed=event.gate_passed)
    return _promotion_row_to_model(row)


def get_promotion_event_by_id(db: Session, event_id: UUID) -> SchemaPromotionEvent | None:
    """Retrieve a promotion event by UUID."""
    row = (
        db.query(SchemaPromotionEventRow)
        .filter(SchemaPromotionEventRow.id == event_id)
        .first()
    )
    return _promotion_row_to_model(row) if row else None


def get_promotion_events_for_proposal(
    db: Session, proposal_id: UUID
) -> list[SchemaPromotionEvent]:
    """Retrieve all promotion events for a given proposal."""
    rows = (
        db.query(SchemaPromotionEventRow)
        .filter(SchemaPromotionEventRow.proposal_id == proposal_id)
        .order_by(SchemaPromotionEventRow.created_at.desc())
        .all()
    )
    return [_promotion_row_to_model(row) for row in rows]


# --- CRUD Functions: CalibrationDecision (Chunk 49, D394) ---


def create_calibration_decision(
    db: Session,
    proposal_id: UUID,
    tier: int,
    raw_confidence: float,
    decision: str,
    modification_distance: float | None = None,
    ontology_module: str | None = None,
) -> CalibrationDecision:
    """Insert a new calibration decision row. Canonical name — sole writer."""
    row = CalibrationDecisionRow(
        proposal_id=proposal_id,
        change_tier=tier,
        raw_confidence=raw_confidence,
        decision=decision,
        modification_distance=modification_distance,
        ontology_module=ontology_module,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("calibration_decision_created", tier=tier, decision=decision, proposal_id=str(proposal_id))
    return CalibrationDecision(
        proposal_id=row.proposal_id,
        change_tier=row.change_tier,
        raw_confidence=row.raw_confidence,
        decision=row.decision,
        modification_distance=row.modification_distance,
        ontology_module=row.ontology_module,
        recorded_at=row.recorded_at,
    )


def get_calibration_decisions_for_tier(
    db: Session, tier: int, limit: int | None = None
) -> list[CalibrationDecision]:
    """Get calibration decisions for a tier, ordered by recorded_at ascending."""
    query = (
        db.query(CalibrationDecisionRow)
        .filter(CalibrationDecisionRow.change_tier == tier)
        .order_by(CalibrationDecisionRow.recorded_at.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    rows = query.all()
    return [
        CalibrationDecision(
            proposal_id=r.proposal_id,
            change_tier=r.change_tier,
            raw_confidence=r.raw_confidence,
            decision=r.decision,
            modification_distance=r.modification_distance,
            ontology_module=r.ontology_module,
            recorded_at=r.recorded_at,
        )
        for r in rows
    ]


def get_calibration_records_for_tier(db: Session, tier: int) -> list[CalibrationRecord]:
    """Get existing calibration_records rows for a tier (for regression baseline)."""
    rows = (
        db.query(CalibrationRecordRow)
        .filter(CalibrationRecordRow.change_tier == tier)
        .all()
    )
    return [_calibration_row_to_model(row) for row in rows]


def delete_calibration_records_for_tier(db: Session, tier: int) -> int:
    """Delete all calibration_records rows for a tier. Returns count deleted."""
    count = (
        db.query(CalibrationRecordRow)
        .filter(CalibrationRecordRow.change_tier == tier)
        .delete(synchronize_session="fetch")
    )
    db.commit()
    log.info("calibration_records_deleted_for_tier", tier=tier, count=count)
    return count


# --- CRUD Functions: TrustScore (Chunk 49, D394) ---


def get_trust_score_for_tier(db: Session, tier: int) -> TrustScore | None:
    """Get trust score row for a tier."""
    row = db.query(TrustScoreRow).filter(TrustScoreRow.tier == tier).first()
    if row is None:
        return None
    return TrustScore(
        tier=row.tier,
        trust_score=row.trust_score,
        autonomy_threshold=row.autonomy_threshold,
        autonomy_enabled=row.autonomy_enabled,
        window_size=row.window_size,
        min_reviews_for_calibration=row.min_reviews_for_calibration,
        risk_tolerance=row.risk_tolerance,
        total_decisions=row.total_decisions,
        regression_detected=row.regression_detected,
        last_computed_at=row.last_computed_at,
    )


def upsert_trust_score(
    db: Session,
    tier: int,
    trust_score: float,
    total_decisions: int,
    regression_detected: bool,
    last_computed_at: datetime,
    window_size: int | None = None,
    min_reviews_for_calibration: int | None = None,
    risk_tolerance: float | None = None,
) -> TrustScore:
    """Upsert trust_scores row for a tier. Creates with defaults if absent (cold start)."""
    row = db.query(TrustScoreRow).filter(TrustScoreRow.tier == tier).first()
    if row is None:
        row = TrustScoreRow(tier=tier)
        db.add(row)
    row.trust_score = trust_score
    row.total_decisions = total_decisions
    row.regression_detected = regression_detected
    row.last_computed_at = last_computed_at
    if window_size is not None:
        row.window_size = window_size
    if min_reviews_for_calibration is not None:
        row.min_reviews_for_calibration = min_reviews_for_calibration
    if risk_tolerance is not None:
        row.risk_tolerance = risk_tolerance
    db.commit()
    db.refresh(row)
    log.info("trust_score_upserted", tier=tier, trust_score=trust_score, total_decisions=total_decisions)
    return TrustScore(
        tier=row.tier,
        trust_score=row.trust_score,
        autonomy_threshold=row.autonomy_threshold,
        autonomy_enabled=row.autonomy_enabled,
        window_size=row.window_size,
        min_reviews_for_calibration=row.min_reviews_for_calibration,
        risk_tolerance=row.risk_tolerance,
        total_decisions=row.total_decisions,
        regression_detected=row.regression_detected,
        last_computed_at=row.last_computed_at,
    )
