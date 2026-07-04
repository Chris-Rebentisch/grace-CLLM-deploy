"""SQLAlchemy ORM tables and CRUD operations for competency questions and clusters."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.discovery.cq_models import (
    CQCluster,
    CQPriority,
    CQSource,
    CQStatus,
    CQType,
    CQVerificationStatus,
    CompetencyQuestion,
)
from src.shared.database import Base


class CQClusterRow(Base):
    """SQLAlchemy ORM model for the cq_clusters table."""

    __tablename__ = "cq_clusters"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    canonical_cq_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("competency_questions.id", use_alter=True),
        nullable=True,
    )
    domain = Column(Text, default="other")
    agreement_tier = Column(String(10), default="low")
    source_passes = Column(JSONB, default=[])
    similarity_score = Column(Float, default=0.0)
    member_count = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    # Tier 1 fields
    cluster_quality_score = Column(Float, default=0.0)
    max_membership_probability = Column(Float, default=0.0)
    min_pairwise_similarity = Column(Float, default=0.0)
    # Tier 2 fields
    quality = Column(String(10), default="review")
    cross_domain = Column(Boolean, default=False)
    domain_distribution = Column(JSONB, default={})
    has_human_anchor = Column(Boolean, default=False)
    cq_type_distribution = Column(JSONB, default={})
    embedding_domain = Column(Text, default="other")
    embedding_domain_confidence = Column(Float, default=0.0)


class MergeRunRow(Base):
    """SQLAlchemy ORM model for the merge_runs table."""

    __tablename__ = "merge_runs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")
    model = Column(Text, default="")
    provider = Column(Text, default="")
    total_cqs_input = Column(Integer, default=0)
    total_clusters = Column(Integer, default=0)
    total_singletons = Column(Integer, default=0)
    total_gap_fills = Column(Integer, default=0)
    mean_cluster_size = Column(Float, default=0.0)
    mean_intra_similarity = Column(Float, default=0.0)
    agreement_distribution = Column(JSONB, default={})
    quality_distribution = Column(JSONB, default={})
    hierarchy_json = Column(JSONB, nullable=True)
    gap_report_json = Column(JSONB, nullable=True)
    tier3_results_json = Column(JSONB, nullable=True)
    duration_ms = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)


class SchemaExtractionRunRow(Base):
    """SQLAlchemy ORM model for the schema_extraction_runs table."""

    __tablename__ = "schema_extraction_runs"

    id = Column(Text, primary_key=True)
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")
    model = Column(Text, default="")
    provider = Column(Text, default="")
    total_entity_types = Column(Integer, default=0)
    total_relationships = Column(Integer, default=0)
    total_duration_ms = Column(Integer, default=0)
    cqs_used = Column(Integer, default=0)
    seed_reference_used = Column(Boolean, default=False)
    domains_processed = Column(JSONB, default=[])
    pass_outputs_json = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)


class SchemaMergeRunRow(Base):
    """SQLAlchemy ORM model for the schema_merge_runs table."""

    __tablename__ = "schema_merge_runs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    extraction_run_id = Column(Text, nullable=False, default="")
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")
    model = Column(Text, default="")
    provider = Column(Text, default="")
    input_entity_types = Column(Integer, default=0)
    input_relationships = Column(Integer, default=0)
    input_cqs = Column(Integer, default=0)
    seed_types_count = Column(Integer, default=0)
    merged_entity_types = Column(Integer, default=0)
    merged_relationships = Column(Integer, default=0)
    cq_coverage_rate = Column(Float, default=0.0)
    cross_pass_agreement_rate = Column(Float, default=0.0)
    provenance_distribution = Column(JSONB, default={})
    richness_distribution = Column(JSONB, default={})
    seed_schema_json = Column(JSONB, nullable=True)
    duration_ms = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)


class CompetencyQuestionRow(Base):
    """SQLAlchemy ORM model for the competency_questions table."""

    __tablename__ = "competency_questions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    canonical_text = Column(Text, nullable=False)
    raw_user_input = Column(Text, nullable=True)
    cq_type = Column(String(20), nullable=False, default="UNCLASSIFIED")
    domain = Column(Text, default="other")
    priority = Column(String(10), default="UNSET")
    source = Column(String(30), nullable=False)
    source_pass = Column(String(20), nullable=True)
    template_id = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="DRAFT")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())
    version = Column(Integer, default=1)
    previous_text = Column(Text, nullable=True)
    generation_confidence = Column(Float, default=0.0)
    verification_confidence = Column(Float, default=0.0)
    verification_status = Column(String(30), default="UNTESTED")
    verification_path = Column(Text, nullable=True)
    verification_gap = Column(Text, nullable=True)
    linked_document_ids = Column(JSONB, default=[])
    cluster_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("cq_clusters.id"),
        nullable=True,
    )
    metadata_extra = Column(JSONB, default={})
    # Tier 2 fields
    embedding_cq_type = Column(String(20), default="UNCLASSIFIED")
    embedding_cq_type_confidence = Column(Float, default=0.0)
    rule_cq_type = Column(String(20), default="UNCLASSIFIED")
    type_agreement = Column(Boolean, default=False)

    __table_args__ = (
        __import__("sqlalchemy").Index("ix_cq_status", "status"),
        __import__("sqlalchemy").Index("ix_cq_domain", "domain"),
        __import__("sqlalchemy").Index("ix_cq_source", "source"),
        __import__("sqlalchemy").Index("ix_cq_cluster_id", "cluster_id"),
        __import__("sqlalchemy").Index("ix_cq_verification_status", "verification_status"),
        __import__("sqlalchemy").Index("ix_cq_priority", "priority"),
    )


# --- Conversion helpers ---

def _cq_row_to_model(row: CompetencyQuestionRow) -> CompetencyQuestion:
    """Convert a SQLAlchemy row to a Pydantic CompetencyQuestion."""
    linked_ids = row.linked_document_ids or []
    return CompetencyQuestion(
        id=row.id,
        canonical_text=row.canonical_text,
        raw_user_input=row.raw_user_input,
        cq_type=CQType(row.cq_type),
        domain=row.domain or "other",
        priority=CQPriority(row.priority) if row.priority else CQPriority.UNSET,
        source=CQSource(row.source),
        source_pass=row.source_pass,
        template_id=row.template_id,
        status=CQStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version or 1,
        previous_text=row.previous_text,
        generation_confidence=row.generation_confidence or 0.0,
        verification_confidence=row.verification_confidence or 0.0,
        verification_status=CQVerificationStatus(row.verification_status) if row.verification_status else CQVerificationStatus.UNTESTED,
        verification_path=row.verification_path,
        verification_gap=row.verification_gap,
        linked_document_ids=[UUID(str(uid)) for uid in linked_ids],
        cluster_id=row.cluster_id,
        metadata_extra=row.metadata_extra or {},
        embedding_cq_type=row.embedding_cq_type or "UNCLASSIFIED",
        embedding_cq_type_confidence=row.embedding_cq_type_confidence or 0.0,
        rule_cq_type=row.rule_cq_type or "UNCLASSIFIED",
        type_agreement=row.type_agreement or False,
    )


def _cq_model_to_row(cq: CompetencyQuestion) -> CompetencyQuestionRow:
    """Convert a Pydantic CompetencyQuestion to a SQLAlchemy row."""
    return CompetencyQuestionRow(
        id=cq.id,
        canonical_text=cq.canonical_text,
        raw_user_input=cq.raw_user_input,
        cq_type=cq.cq_type.value,
        domain=cq.domain,
        priority=cq.priority.value,
        source=cq.source.value,
        source_pass=cq.source_pass,
        template_id=cq.template_id,
        status=cq.status.value,
        created_at=cq.created_at,
        updated_at=cq.updated_at,
        version=cq.version,
        previous_text=cq.previous_text,
        generation_confidence=cq.generation_confidence,
        verification_confidence=cq.verification_confidence,
        verification_status=cq.verification_status.value,
        verification_path=cq.verification_path,
        verification_gap=cq.verification_gap,
        linked_document_ids=[str(uid) for uid in cq.linked_document_ids],
        cluster_id=cq.cluster_id,
        metadata_extra=cq.metadata_extra,
        embedding_cq_type=cq.embedding_cq_type,
        embedding_cq_type_confidence=cq.embedding_cq_type_confidence,
        rule_cq_type=cq.rule_cq_type,
        type_agreement=cq.type_agreement,
    )


def _cluster_row_to_model(row: CQClusterRow) -> CQCluster:
    """Convert a SQLAlchemy row to a Pydantic CQCluster."""
    return CQCluster(
        id=row.id,
        canonical_cq_id=row.canonical_cq_id,
        domain=row.domain or "other",
        agreement_tier=row.agreement_tier or "low",
        source_passes=row.source_passes or [],
        similarity_score=row.similarity_score or 0.0,
        member_count=row.member_count or 0,
        created_at=row.created_at,
        cluster_quality_score=row.cluster_quality_score or 0.0,
        max_membership_probability=row.max_membership_probability or 0.0,
        min_pairwise_similarity=row.min_pairwise_similarity or 0.0,
        quality=row.quality or "review",
        cross_domain=row.cross_domain or False,
        domain_distribution=row.domain_distribution or {},
        has_human_anchor=row.has_human_anchor or False,
        cq_type_distribution=row.cq_type_distribution or {},
        embedding_domain=row.embedding_domain or "other",
        embedding_domain_confidence=row.embedding_domain_confidence or 0.0,
    )


def _cluster_model_to_row(cluster: CQCluster) -> CQClusterRow:
    """Convert a Pydantic CQCluster to a SQLAlchemy row."""
    return CQClusterRow(
        id=cluster.id,
        canonical_cq_id=cluster.canonical_cq_id,
        domain=cluster.domain,
        agreement_tier=cluster.agreement_tier,
        source_passes=cluster.source_passes,
        similarity_score=cluster.similarity_score,
        member_count=cluster.member_count,
        created_at=cluster.created_at,
        cluster_quality_score=cluster.cluster_quality_score,
        max_membership_probability=cluster.max_membership_probability,
        min_pairwise_similarity=cluster.min_pairwise_similarity,
        quality=cluster.quality,
        cross_domain=cluster.cross_domain,
        domain_distribution=cluster.domain_distribution,
        has_human_anchor=cluster.has_human_anchor,
        cq_type_distribution=cluster.cq_type_distribution,
        embedding_domain=cluster.embedding_domain,
        embedding_domain_confidence=cluster.embedding_domain_confidence,
    )


# --- CQ CRUD ---

def create_cq(db: Session, cq: CompetencyQuestion) -> CompetencyQuestion:
    """Insert a new competency question record."""
    row = _cq_model_to_row(cq)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _cq_row_to_model(row)


def get_cq_by_id(db: Session, cq_id: UUID) -> CompetencyQuestion | None:
    """Retrieve a CQ by its UUID."""
    row = db.query(CompetencyQuestionRow).filter(CompetencyQuestionRow.id == cq_id).first()
    return _cq_row_to_model(row) if row else None


def list_cqs(
    db: Session,
    status: CQStatus | None = None,
    domain: str | None = None,
    source: CQSource | None = None,
    priority: CQPriority | None = None,
    verification_status: CQVerificationStatus | None = None,
    cluster_id: UUID | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[CompetencyQuestion]:
    """List CQs with optional filters. Supports pagination."""
    query = db.query(CompetencyQuestionRow)
    if status is not None:
        query = query.filter(CompetencyQuestionRow.status == status.value)
    if domain is not None:
        query = query.filter(CompetencyQuestionRow.domain == domain)
    if source is not None:
        query = query.filter(CompetencyQuestionRow.source == source.value)
    if priority is not None:
        query = query.filter(CompetencyQuestionRow.priority == priority.value)
    if verification_status is not None:
        query = query.filter(CompetencyQuestionRow.verification_status == verification_status.value)
    if cluster_id is not None:
        query = query.filter(CompetencyQuestionRow.cluster_id == cluster_id)
    rows = query.offset(offset).limit(limit).all()
    return [_cq_row_to_model(row) for row in rows]


def update_cq(db: Session, cq_id: UUID, updates: dict) -> CompetencyQuestion | None:
    """Update specific fields. If canonical_text changes, increment version and store previous_text."""
    row = db.query(CompetencyQuestionRow).filter(CompetencyQuestionRow.id == cq_id).first()
    if row is None:
        return None

    if "canonical_text" in updates and updates["canonical_text"] != row.canonical_text:
        row.previous_text = row.canonical_text
        row.version = (row.version or 1) + 1

    for key, value in updates.items():
        if hasattr(row, key):
            setattr(row, key, value)

    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _cq_row_to_model(row)


def update_cq_status(db: Session, cq_id: UUID, status: CQStatus) -> CompetencyQuestion | None:
    """Update the lifecycle status of a CQ."""
    row = db.query(CompetencyQuestionRow).filter(CompetencyQuestionRow.id == cq_id).first()
    if row is None:
        return None
    row.status = status.value
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _cq_row_to_model(row)


def update_cq_verification(
    db: Session,
    cq_id: UUID,
    verification_status: CQVerificationStatus,
    verification_confidence: float,
    verification_path: str | None = None,
    verification_gap: str | None = None,
) -> CompetencyQuestion | None:
    """Update verification fields (OE-Assist pattern)."""
    row = db.query(CompetencyQuestionRow).filter(CompetencyQuestionRow.id == cq_id).first()
    if row is None:
        return None
    row.verification_status = verification_status.value
    row.verification_confidence = verification_confidence
    row.verification_path = verification_path
    row.verification_gap = verification_gap
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _cq_row_to_model(row)


def bulk_create_cqs(db: Session, cqs: list[CompetencyQuestion]) -> list[CompetencyQuestion]:
    """Bulk insert CQs. Used after merge step."""
    rows = [_cq_model_to_row(cq) for cq in cqs]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return [_cq_row_to_model(row) for row in rows]


def get_cq_summary(db: Session) -> dict:
    """Counts by status, domain, source, cq_type, priority, verification_status."""
    def _group_counts(column):
        rows = db.query(column, func.count()).group_by(column).all()
        return {row[0]: row[1] for row in rows}

    return {
        "by_status": _group_counts(CompetencyQuestionRow.status),
        "by_domain": _group_counts(CompetencyQuestionRow.domain),
        "by_source": _group_counts(CompetencyQuestionRow.source),
        "by_cq_type": _group_counts(CompetencyQuestionRow.cq_type),
        "by_priority": _group_counts(CompetencyQuestionRow.priority),
        "by_verification_status": _group_counts(CompetencyQuestionRow.verification_status),
        "total": db.query(CompetencyQuestionRow).count(),
    }


def get_cqs_by_domain(db: Session, domain: str) -> list[CompetencyQuestion]:
    """Get all CQs for a specific domain."""
    rows = db.query(CompetencyQuestionRow).filter(CompetencyQuestionRow.domain == domain).all()
    return [_cq_row_to_model(row) for row in rows]


def get_cqs_for_document(db: Session, document_id: UUID) -> list[CompetencyQuestion]:
    """Find all CQs linked to a specific document (searches linked_document_ids JSONB)."""
    doc_id_str = str(document_id)
    rows = (
        db.query(CompetencyQuestionRow)
        .filter(CompetencyQuestionRow.linked_document_ids.op("@>")(f'["{doc_id_str}"]'))
        .all()
    )
    return [_cq_row_to_model(row) for row in rows]


# --- Cluster CRUD ---

def create_cluster(db: Session, cluster: CQCluster) -> CQCluster:
    """Insert a new CQ cluster."""
    row = _cluster_model_to_row(cluster)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _cluster_row_to_model(row)


def get_cluster_by_id(db: Session, cluster_id: UUID) -> CQCluster | None:
    """Retrieve a cluster by its UUID."""
    row = db.query(CQClusterRow).filter(CQClusterRow.id == cluster_id).first()
    return _cluster_row_to_model(row) if row else None


def list_clusters(db: Session, domain: str | None = None) -> list[CQCluster]:
    """List clusters with optional domain filter."""
    query = db.query(CQClusterRow)
    if domain is not None:
        query = query.filter(CQClusterRow.domain == domain)
    rows = query.all()
    return [_cluster_row_to_model(row) for row in rows]


def get_cluster_members(db: Session, cluster_id: UUID) -> list[CompetencyQuestion]:
    """Get all CQs in a cluster."""
    rows = (
        db.query(CompetencyQuestionRow)
        .filter(CompetencyQuestionRow.cluster_id == cluster_id)
        .all()
    )
    return [_cq_row_to_model(row) for row in rows]


def update_cluster(db: Session, cluster_id: UUID, updates: dict) -> CQCluster | None:
    """Update specific fields of a cluster."""
    row = db.query(CQClusterRow).filter(CQClusterRow.id == cluster_id).first()
    if row is None:
        return None
    for key, value in updates.items():
        if hasattr(row, key):
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _cluster_row_to_model(row)


# --- Merge Run CRUD ---

def create_merge_run(db: Session, run_data: dict) -> MergeRunRow:
    """Insert a new merge run record."""
    row = MergeRunRow(
        id=run_data.get("id", uuid4()),
        started_at=run_data.get("started_at"),
        completed_at=run_data.get("completed_at"),
        status=run_data.get("status", "running"),
        model=run_data.get("model", ""),
        provider=run_data.get("provider", ""),
        total_cqs_input=run_data.get("total_cqs_input", 0),
        total_clusters=run_data.get("total_clusters", 0),
        total_singletons=run_data.get("total_singletons", 0),
        total_gap_fills=run_data.get("total_gap_fills", 0),
        mean_cluster_size=run_data.get("mean_cluster_size", 0.0),
        mean_intra_similarity=run_data.get("mean_intra_similarity", 0.0),
        agreement_distribution=run_data.get("agreement_distribution", {}),
        quality_distribution=run_data.get("quality_distribution", {}),
        hierarchy_json=run_data.get("hierarchy_json"),
        gap_report_json=run_data.get("gap_report_json"),
        tier3_results_json=run_data.get("tier3_results_json"),
        duration_ms=run_data.get("duration_ms", 0),
        error_message=run_data.get("error_message"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_merge_run(db: Session, run_id: UUID) -> MergeRunRow | None:
    """Retrieve a merge run by ID."""
    return db.query(MergeRunRow).filter(MergeRunRow.id == run_id).first()


def update_merge_run(db: Session, run_id: UUID, updates: dict) -> MergeRunRow | None:
    """Update a merge run record."""
    row = db.query(MergeRunRow).filter(MergeRunRow.id == run_id).first()
    if row is None:
        return None
    for key, value in updates.items():
        if hasattr(row, key):
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row
