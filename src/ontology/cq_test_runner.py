"""CQ Test Runner: schema verbalization, LLM verification, orchestrator, gate."""

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog
import yaml
from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.ontology.cq_test_models import (
    TERMINAL_CQ_TEST_RUN_STATUSES,
    CQGapSeverity,
    CQGapType,
    CQTestGateResult,
    CQTestResult,
    CQTestResultEntry,
    CQTestRun,
    CQTestRunStatus,
)
from src.ontology.cq_test_prompts import CQ_VERIFICATION_SYSTEM_PROMPT, CQ_VERIFICATION_USER_PROMPT
from src.shared.database import Base
from src.shared.llm_provider import LLMProvider, get_provider

log = structlog.get_logger()


# --- ORM Row Class ---


class CQTestRunRow(Base):
    """SQLAlchemy ORM model for the cq_test_runs table."""

    __tablename__ = "cq_test_runs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    schema_version_id = Column(PG_UUID(as_uuid=True), nullable=False)
    schema_version_number = Column(Integer, nullable=True)
    is_proposed_schema = Column(Boolean, nullable=False, default=False)
    proposed_schema_json = Column(JSONB, nullable=True)
    total_cqs = Column(Integer, nullable=False, default=0)
    passing = Column(Integer, nullable=False, default=0)
    failing = Column(Integer, nullable=False, default=0)
    out_of_scope = Column(Integer, nullable=False, default=0)
    errors = Column(Integer, nullable=False, default=0)
    pass_rate = Column(Float, nullable=False, default=0.0)
    status = Column(String(20), nullable=False, default="running")
    model = Column(Text, nullable=True)
    provider = Column(Text, nullable=True)
    concurrency = Column(Integer, nullable=False, default=1)
    results_json = Column(JSONB, nullable=True)
    gap_summary = Column(JSONB, default={})
    duration_ms = Column(Integer, nullable=False, default=0)
    metadata_extra = Column(JSONB, default={})

    __table_args__ = (
        Index("ix_cq_test_runs_schema_version_id", "schema_version_id"),
        Index("ix_cq_test_runs_status", "status"),
        Index("ix_cq_test_runs_created_at", "created_at"),
    )


# --- Row-to-Model / Model-to-Row Converters ---


def _run_row_to_model(row: CQTestRunRow) -> CQTestRun:
    """Convert a SQLAlchemy CQTestRunRow to a Pydantic CQTestRun."""
    results = []
    if row.results_json:
        results = [CQTestResultEntry(**r) for r in row.results_json]
    return CQTestRun(
        id=row.id,
        created_at=row.created_at,
        completed_at=row.completed_at,
        schema_version_id=row.schema_version_id,
        schema_version_number=row.schema_version_number,
        is_proposed_schema=row.is_proposed_schema,
        proposed_schema_json=row.proposed_schema_json,
        total_cqs=row.total_cqs,
        passing=row.passing,
        failing=row.failing,
        out_of_scope=row.out_of_scope,
        errors=row.errors,
        pass_rate=row.pass_rate,
        status=CQTestRunStatus(row.status),
        model=row.model or "",
        provider=row.provider or "",
        concurrency=row.concurrency,
        results=results,
        gap_summary=row.gap_summary or {},
        duration_ms=row.duration_ms,
        metadata_extra=row.metadata_extra or {},
    )


def _run_model_to_row(run: CQTestRun) -> CQTestRunRow:
    """Convert a Pydantic CQTestRun to a SQLAlchemy CQTestRunRow."""
    return CQTestRunRow(
        id=run.id,
        created_at=run.created_at,
        completed_at=run.completed_at,
        schema_version_id=run.schema_version_id,
        schema_version_number=run.schema_version_number,
        is_proposed_schema=run.is_proposed_schema,
        proposed_schema_json=run.proposed_schema_json,
        total_cqs=run.total_cqs,
        passing=run.passing,
        failing=run.failing,
        out_of_scope=run.out_of_scope,
        errors=run.errors,
        pass_rate=run.pass_rate,
        status=run.status.value,
        model=run.model,
        provider=run.provider,
        concurrency=run.concurrency,
        results_json=[r.model_dump() for r in run.results],
        gap_summary=run.gap_summary,
        duration_ms=run.duration_ms,
        metadata_extra=run.metadata_extra,
    )


# --- CRUD Functions ---


def create_test_run(db: Session, run: CQTestRun) -> CQTestRun:
    """Insert a new CQ test run."""
    row = _run_model_to_row(run)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("cq_test_run_created", run_id=str(row.id))
    return _run_row_to_model(row)


def update_test_run(db: Session, run_id: UUID, **kwargs) -> CQTestRun | None:
    """Update a test run (status, results, metrics)."""
    row = db.query(CQTestRunRow).filter(CQTestRunRow.id == run_id).first()
    if row is None:
        return None

    for key, value in kwargs.items():
        if key == "results":
            row.results_json = [r.model_dump() for r in value]
        elif key == "status" and isinstance(value, CQTestRunStatus):
            row.status = value.value
        elif hasattr(row, key):
            setattr(row, key, value)

    db.commit()
    db.refresh(row)
    return _run_row_to_model(row)


def mark_test_run_failed(db: Session, run_id: UUID, error_message: str) -> CQTestRun | None:
    """Mark a run as failed, persisting the error and completion time.

    F-0035 / ISS-0026 (validation run 2026-07-03) capture-the-why: a
    background CQ-test failure was only structlog'd (`background_cq_test_failed`)
    and the run row stayed `status='running', total_cqs=0` for six hours — anyone
    polling the run_id waited forever and the operator had to SQL-flip the row.
    This helper is the single failure-propagation path: status → failed,
    completed_at stamped, and the error message persisted in metadata_extra
    (the row has no dedicated error column; JSONB avoids a migration).

    Never overwrites a terminal row (e.g. an operator cancel that raced the
    failing task).
    """
    row = db.query(CQTestRunRow).filter(CQTestRunRow.id == run_id).first()
    if row is None:
        return None
    if row.status in {s.value for s in TERMINAL_CQ_TEST_RUN_STATUSES}:
        return _run_row_to_model(row)
    row.status = CQTestRunStatus.FAILED.value
    row.completed_at = datetime.now(UTC)
    extra = dict(row.metadata_extra or {})
    extra["error"] = error_message
    row.metadata_extra = extra
    db.commit()
    db.refresh(row)
    log.info("cq_test_run_marked_failed", run_id=str(run_id), error=error_message)
    return _run_row_to_model(row)


def cancel_test_run(db: Session, run_id: UUID) -> tuple[str, CQTestRun | None]:
    """Cancel a running CQ test run.

    F-0035 / ISS-0026: there was no cancel path — zombie `running` rows could
    only be cleared with manual SQL. Returns (outcome, run) where outcome is
    one of: "cancelled" (running → cancelled), "conflict" (already terminal),
    "not_found" (no such run). The in-flight background task observes the flip
    via the cooperative checks in run_cq_tests() and stops issuing LLM calls.
    """
    row = db.query(CQTestRunRow).filter(CQTestRunRow.id == run_id).first()
    if row is None:
        return ("not_found", None)
    if row.status in {s.value for s in TERMINAL_CQ_TEST_RUN_STATUSES}:
        return ("conflict", _run_row_to_model(row))
    row.status = CQTestRunStatus.CANCELLED.value
    row.completed_at = datetime.now(UTC)
    extra = dict(row.metadata_extra or {})
    extra["cancelled"] = True
    row.metadata_extra = extra
    db.commit()
    db.refresh(row)
    log.info("cq_test_run_cancelled", run_id=str(run_id))
    return ("cancelled", _run_row_to_model(row))


def _is_run_cancelled(db: Session, run_id: UUID) -> bool:
    """Re-read the run's status to honor an operator cancel (F-0035/ISS-0026).

    Postgres READ COMMITTED lets each statement see the latest committed data,
    so a cancel committed from the API session is visible to the background
    task's session on its next SELECT.
    """
    status = (
        db.query(CQTestRunRow.status).filter(CQTestRunRow.id == run_id).scalar()
    )
    return status == CQTestRunStatus.CANCELLED.value


def get_test_run_by_id(db: Session, run_id: UUID) -> CQTestRun | None:
    """Retrieve a test run by UUID."""
    row = db.query(CQTestRunRow).filter(CQTestRunRow.id == run_id).first()
    return _run_row_to_model(row) if row else None


def list_test_runs(
    db: Session,
    schema_version_id: UUID | None = None,
    status: CQTestRunStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[CQTestRun]:
    """List test runs with optional filters. Ordered by created_at descending."""
    query = db.query(CQTestRunRow)
    if schema_version_id is not None:
        query = query.filter(CQTestRunRow.schema_version_id == schema_version_id)
    if status is not None:
        query = query.filter(CQTestRunRow.status == status.value)
    rows = query.order_by(CQTestRunRow.created_at.desc()).offset(offset).limit(limit).all()
    return [_run_row_to_model(row) for row in rows]


def get_latest_test_run(db: Session, schema_version_id: UUID) -> CQTestRun | None:
    """Get the most recent completed test run for a given schema version."""
    row = (
        db.query(CQTestRunRow)
        .filter(
            CQTestRunRow.schema_version_id == schema_version_id,
            CQTestRunRow.status == CQTestRunStatus.COMPLETED.value,
        )
        .order_by(CQTestRunRow.created_at.desc())
        .first()
    )
    return _run_row_to_model(row) if row else None


def get_latest_test_run_in_ancestry(
    db: Session,
    schema_version_id: UUID,
    max_depth: int = 10,
) -> CQTestRun | None:
    """Latest completed run for a version OR any of its ancestors (F2-08).

    second validation run F2-08: the differential gate's baseline was fetched for the
    ACTIVE version only, and every ratification mints a new version with zero
    runs — so the moment proposal #1 ratified, proposal #2 (and the daemon's
    applies, and even the REVERT safety path) found no baseline, degraded to
    the absolute gate, and auto-rejected. Four deterministic occurrences in
    one run. A version's ancestors ARE its non-regression reference (the diff
    between adjacent versions is exactly the change the gate approved), so
    walk ``previous_version_id`` until a completed run is found. ``max_depth``
    bounds pathological chains.
    """
    from src.ontology.database import OntologyVersionRow

    current_id: UUID | None = schema_version_id
    for _ in range(max_depth):
        if current_id is None:
            return None
        run = get_latest_test_run(db, current_id)
        if run is not None:
            return run
        parent = (
            db.query(OntologyVersionRow.previous_version_id)
            .filter(OntologyVersionRow.id == current_id)
            .first()
        )
        current_id = parent[0] if parent else None
    return None


# --- Schema Verbalization ---


def verbalize_schema(schema_json: dict) -> str:
    """Convert a schema JSON dict into structured plain text for LLM reasoning.

    Handles both flat GrACE format and Pydantic $defs format.
    Deterministic output: sorted keys for consistent ordering.
    """
    lines = ["ONTOLOGY SCHEMA DESCRIPTION", ""]

    # Detect format
    if "entity_types" in schema_json:
        entity_types = schema_json["entity_types"]
        relationships = schema_json.get("relationships", {})
    elif "$defs" in schema_json:
        entity_types = schema_json["$defs"]
        relationships = {}
    else:
        entity_types = {}
        relationships = {}

    # Normalize: entity_types can be a list or dict
    if isinstance(entity_types, list):
        et_dict = {et.get("name", f"type_{i}"): et for i, et in enumerate(entity_types)}
    else:
        et_dict = entity_types

    if isinstance(relationships, list):
        rel_dict = {r.get("name", f"rel_{i}"): r for i, r in enumerate(relationships)}
    else:
        rel_dict = relationships

    # Entity Types
    lines.append("Entity Types:")
    if not et_dict:
        lines.append("  (none)")
    for name in sorted(et_dict.keys()):
        data = et_dict[name]
        desc = data.get("description", "No description.")
        lines.append(f"- {name}: {desc}")

        # Properties
        props = data.get("properties", [])
        if isinstance(props, list):
            prop_strs = []
            for p in props:
                if isinstance(p, dict):
                    pname = p.get("name", "?")
                    dtype = p.get("data_type", p.get("type", "unknown"))
                    required = p.get("required", False)
                    s = f"{pname} ({dtype}"
                    if required:
                        s += ", required"
                    s += ")"
                    prop_strs.append(s)
                else:
                    prop_strs.append(str(p))
            lines.append(f"  Properties: {', '.join(prop_strs) if prop_strs else 'none'}.")
        elif isinstance(props, dict):
            prop_strs = []
            for pname in sorted(props.keys()):
                pdata = props[pname]
                dtype = pdata.get("type", "unknown") if isinstance(pdata, dict) else "unknown"
                prop_strs.append(f"{pname} ({dtype})")
            lines.append(f"  Properties: {', '.join(prop_strs) if prop_strs else 'none'}.")
        else:
            lines.append("  Properties: none.")

        parent = data.get("parent_type")
        lines.append(f"  Parent type: {parent if parent else 'none'}.")
        domain = data.get("domain", "general")
        lines.append(f"  Domain: {domain}.")
        lines.append("")

    # Relationships
    lines.append("Relationships:")
    # F-38 (validation run, 2026-07-01): every GrACE relationship carries
    # the system temporal-validity properties valid_from / valid_to, but they are
    # NOT listed in the schema's `edge_properties`, so the CQ judge treated
    # temporal "since when / as of" questions as unanswerable and produced ~3
    # false FAIL verdicts. State the implicit system properties so the judge
    # knows temporal edge facts are answerable.
    if rel_dict:
        lines.append(
            "  (Every relationship also carries system temporal-validity "
            "properties valid_from and valid_to, enabling 'since when' / "
            "'as of' temporal queries.)"
        )
    if not rel_dict:
        lines.append("  (none)")
    for name in sorted(rel_dict.keys()):
        data = rel_dict[name]
        source = data.get("source_type", "?")
        target = data.get("target_type", "?")
        desc = data.get("description", "")
        lines.append(f"- {name}: {source} -> {target}")
        if desc:
            lines.append(f"  Description: {desc}")

        edge_props = data.get("edge_properties", [])
        if isinstance(edge_props, list) and edge_props:
            ep_strs = []
            for ep in edge_props:
                if isinstance(ep, dict):
                    ep_strs.append(f"{ep.get('name', '?')} ({ep.get('data_type', ep.get('type', 'unknown'))})")
                else:
                    ep_strs.append(str(ep))
            lines.append(f"  Edge properties: {', '.join(ep_strs)}.")
        else:
            lines.append("  Edge properties: none.")

        richness = data.get("richness_tier", "simple")
        lines.append(f"  Richness: {richness}.")
        lines.append("")

    return "\n".join(lines)


# --- CQ Verification ---


async def verify_single_cq(
    verbalized_schema: str,
    cq_text: str,
    cq_id: str,
    cq_domain: str,
    provider: LLMProvider,
) -> CQTestResultEntry:
    """Verify a single CQ against the verbalized schema via one LLM call."""
    user_prompt = CQ_VERIFICATION_USER_PROMPT.format(
        verbalized_schema=verbalized_schema,
        cq_text=cq_text,
    )

    try:
        response = await provider.generate(
            system_prompt=CQ_VERIFICATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.0,
            json_mode=True,
        )

        parsed = _parse_verification_response(response.text)
        if parsed is None:
            return CQTestResultEntry(
                cq_id=cq_id,
                cq_text=cq_text,
                domain=cq_domain,
                result=CQTestResult.ERROR,
                error_message="Could not parse LLM response as JSON",
            )

        result_str = parsed.get("result", "error")
        if result_str == "pass":
            result = CQTestResult.PASS
        elif result_str == "fail":
            result = CQTestResult.FAIL
        else:
            result = CQTestResult.ERROR

        gap_type = None
        gap_severity = None
        if result == CQTestResult.FAIL:
            gt = parsed.get("gap_type")
            if gt:
                try:
                    gap_type = CQGapType(gt)
                except ValueError:
                    pass
            gs = parsed.get("gap_severity")
            if gs:
                try:
                    gap_severity = CQGapSeverity(gs)
                except ValueError:
                    pass

        return CQTestResultEntry(
            cq_id=cq_id,
            cq_text=cq_text,
            domain=cq_domain,
            result=result,
            confidence=float(parsed.get("confidence", 0.0)),
            traced_path=parsed.get("path"),
            gap_type=gap_type,
            gap_severity=gap_severity,
            gap_details=parsed.get("gap_details"),
            reasoning=parsed.get("reasoning", ""),
        )

    except Exception as e:
        log.error("cq_verification_failed", cq_id=cq_id, error=str(e))
        return CQTestResultEntry(
            cq_id=cq_id,
            cq_text=cq_text,
            domain=cq_domain,
            result=CQTestResult.ERROR,
            error_message=str(e),
        )


def _parse_verification_response(text: str) -> dict | None:
    """Parse JSON from LLM verification response."""
    from src.discovery.ollama_client import _parse_json_robust

    result = _parse_json_robust(text)
    if isinstance(result, dict):
        return result
    return None


# --- CQ Loading ---


def load_testable_cqs(db: Session) -> tuple[list[dict], list[dict]]:
    """Load CQs from the database.

    Returns (accepted_cqs, out_of_scope_cqs) as lists of dicts with cq_id, cq_text, domain.

    F-0036 / ISS-0026 (validation run 2026-07-03) capture-the-why: this
    previously loaded rows via ``cq_database.list_cqs``, which rebuilds the full
    ``CompetencyQuestion`` Pydantic model — whose ``validate_domain`` validator
    re-checks each stored domain against the *current* discovery.yaml
    ``domain_categories`` whitelist (loaded at process start). A row that passed
    storage under an older/other config exploded mid-pipeline at READ-BACK,
    which (pre-fix) became a swallowed log line and a permanent zombie run.
    Strict domain validation belongs at import/authoring time only; read-back
    for a test run must tolerate unlisted domains. We therefore read the three
    needed columns directly off the ORM row (no model re-validation) and emit a
    structlog warning per unlisted domain, then proceed.
    """
    from src.discovery.cq_database import CompetencyQuestionRow
    from src.discovery.cq_models import CQStatus
    from src.discovery.models import get_valid_domains

    try:
        valid_domains = set(get_valid_domains())
    except Exception:  # config unavailable — tolerate everything
        valid_domains = None

    def _load(status: CQStatus) -> list[dict]:
        rows = (
            db.query(
                CompetencyQuestionRow.id,
                CompetencyQuestionRow.canonical_text,
                CompetencyQuestionRow.domain,
            )
            .filter(CompetencyQuestionRow.status == status.value)
            .limit(1000)
            .all()
        )
        dicts: list[dict] = []
        for cq_id, cq_text, domain in rows:
            domain = domain or "other"
            if valid_domains is not None and domain not in valid_domains:
                # F-0036: warn-and-proceed — treated as valid-but-unlisted.
                log.warning(
                    "cq_domain_not_in_current_whitelist",
                    cq_id=str(cq_id),
                    domain=domain,
                )
            dicts.append({"cq_id": str(cq_id), "cq_text": cq_text, "domain": domain})
        return dicts

    return _load(CQStatus.ACCEPTED), _load(CQStatus.OUT_OF_SCOPE)


# --- Wall-clock cap (F-0035 / ISS-0026 deferral closure) ---

_EVAL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "eval_config.yaml"
_DEFAULT_MAX_RUN_SECONDS = 3600.0


def _load_max_run_seconds() -> float:
    """Read ``cq_test.max_run_seconds`` from ``config/eval_config.yaml``.

    F-0035 / ISS-0026 deferral closure (2026-07-03) capture-the-why: the
    fix round landed failure propagation + cancel, but a hung LLM call
    chain could still run unbounded ("no timeout" in the finding was
    deferred pending a scheduler decision). This is the D246-safe answer:
    a configurable wall-clock cap the runner SELF-checks at the same
    cooperative points as the cancel check — no scheduler, no watchdog
    thread. Follows the eval_config.yaml knob pattern
    (``mine_api.timeout_seconds`` in ``src/api/extraction_routes.py``).
    Returns the default (3600s) when the config is unreadable; a value
    <= 0 disables the cap.
    """
    try:
        with open(_EVAL_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return float((cfg.get("cq_test") or {}).get("max_run_seconds", _DEFAULT_MAX_RUN_SECONDS))
    except Exception:  # config unavailable/malformed — fall back to default
        return _DEFAULT_MAX_RUN_SECONDS


# --- Test Runner Orchestrator ---


async def run_cq_tests(
    db: Session,
    schema_version_id: UUID | None = None,
    proposed_schema_json: dict | None = None,
    concurrency: int = 1,
    existing_run_id: UUID | None = None,
) -> CQTestRun:
    """Run the full CQ test suite against a schema."""
    from src.ontology.database import get_active_version, get_version_by_id

    start_time = time.time()

    # Load schema
    is_proposed = proposed_schema_json is not None
    if is_proposed:
        schema_json = proposed_schema_json
        # Need a version ID for storage - use active version
        active = get_active_version(db)
        version_id = active.id if active else uuid4()
        version_number = active.version_number if active else None
    elif schema_version_id:
        version = get_version_by_id(db, schema_version_id)
        if version is None:
            raise ValueError(f"Schema version {schema_version_id} not found")
        schema_json = version.schema_json
        version_id = version.id
        version_number = version.version_number
    else:
        active = get_active_version(db)
        if active is None:
            raise ValueError("No active schema version found")
        schema_json = active.schema_json
        version_id = active.id
        version_number = active.version_number

    # Verbalize
    verbalized = verbalize_schema(schema_json)

    # Load CQs
    accepted_cqs, oos_cqs = load_testable_cqs(db)

    # Get provider
    provider = get_provider()

    # Create run record — or reuse the row the API route already created so the
    # run_id returned to the caller IS the executing row.
    # F-58 (validation run): the /cq-test/run route created its own row and
    # returned that id, then this function created a SECOND row that actually got
    # the results. The returned id therefore stayed `running 0/0` forever.
    if existing_run_id is not None:
        run = update_test_run(
            db,
            existing_run_id,
            schema_version_id=version_id,
            schema_version_number=version_number,
            is_proposed_schema=is_proposed,
            proposed_schema_json=proposed_schema_json,
            total_cqs=len(accepted_cqs) + len(oos_cqs),
            status=CQTestRunStatus.RUNNING,
            model=getattr(provider, "model", ""),
            provider=provider.provider_name,
            concurrency=concurrency,
        )
        if run is None:
            raise ValueError(f"CQ test run {existing_run_id} not found")
    else:
        run = CQTestRun(
            schema_version_id=version_id,
            schema_version_number=version_number,
            is_proposed_schema=is_proposed,
            proposed_schema_json=proposed_schema_json,
            total_cqs=len(accepted_cqs) + len(oos_cqs),
            status=CQTestRunStatus.RUNNING,
            model=getattr(provider, "model", ""),
            provider=provider.provider_name,
            concurrency=concurrency,
        )
        run = create_test_run(db, run)

    try:
        # Verify CQs
        results: list[CQTestResultEntry] = []

        # Out-of-scope CQs: pre-classified, no LLM call
        for cq in oos_cqs:
            results.append(CQTestResultEntry(
                cq_id=cq["cq_id"],
                cq_text=cq["cq_text"],
                domain=cq["domain"],
                result=CQTestResult.OUT_OF_SCOPE,
                reasoning="CQ marked as out of scope",
            ))

        # F-0035 / ISS-0026 deferral closure: wall-clock cap, self-checked
        # cooperatively (D246-safe — no scheduler, no watchdog thread).
        max_run_seconds = _load_max_run_seconds()

        # Accepted CQs: run through LLM
        if concurrency <= 1:
            # Sequential
            for cq in accepted_cqs:
                # F-0035 / ISS-0026: cooperative cancel — honor an operator
                # POST .../cancel between LLM calls instead of burning the
                # rest of the suite against a cancelled run.
                if _is_run_cancelled(db, run.id):
                    log.info("cq_test_run_cancel_observed", run_id=str(run.id))
                    return get_test_run_by_id(db, run.id)
                # F-0035 / ISS-0026 deferral closure: wall-clock cap check at
                # the same cooperative point as the cancel check — a hung LLM
                # call chain must not run unbounded. Exceeding the cap marks
                # the run failed and stops issuing calls.
                elapsed_s = time.time() - start_time
                if max_run_seconds > 0 and elapsed_s > max_run_seconds:
                    msg = f"wall_clock_cap_exceeded after {int(elapsed_s)}s"
                    log.error(
                        "cq_test_run_wall_clock_cap",
                        run_id=str(run.id),
                        max_run_seconds=max_run_seconds,
                        elapsed_s=elapsed_s,
                    )
                    mark_test_run_failed(db, run.id, msg)
                    return get_test_run_by_id(db, run.id)
                entry = await verify_single_cq(
                    verbalized, cq["cq_text"], cq["cq_id"], cq["domain"], provider
                )
                results.append(entry)
        else:
            # Parallel with semaphore
            sem = asyncio.Semaphore(concurrency)

            async def verify_with_sem(cq: dict) -> CQTestResultEntry:
                async with sem:
                    return await verify_single_cq(
                        verbalized, cq["cq_text"], cq["cq_id"], cq["domain"], provider
                    )

            tasks = [verify_with_sem(cq) for cq in accepted_cqs]
            llm_results = await asyncio.gather(*tasks)
            results.extend(llm_results)

        # Aggregate
        passing = sum(1 for r in results if r.result == CQTestResult.PASS)
        failing = sum(1 for r in results if r.result == CQTestResult.FAIL)
        out_of_scope = sum(1 for r in results if r.result == CQTestResult.OUT_OF_SCOPE)
        errors = sum(1 for r in results if r.result == CQTestResult.ERROR)
        testable = len(results) - out_of_scope
        pass_rate = passing / testable if testable > 0 else 0.0

        # Gap summary
        gap_summary = _build_gap_summary(results)

        elapsed_ms = int((time.time() - start_time) * 1000)

        # F-0035 / ISS-0026: final cooperative-cancel guard — never overwrite
        # an operator cancellation with `completed` (covers the parallel path
        # and a cancel landing after the last LLM call).
        if _is_run_cancelled(db, run.id):
            log.info("cq_test_run_cancel_observed", run_id=str(run.id))
            return get_test_run_by_id(db, run.id)

        # Update run
        updated = update_test_run(
            db, run.id,
            status=CQTestRunStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            results=results,
            passing=passing,
            failing=failing,
            out_of_scope=out_of_scope,
            errors=errors,
            pass_rate=pass_rate,
            gap_summary=gap_summary,
            duration_ms=elapsed_ms,
        )

        log.info(
            "cq_test_run_completed",
            run_id=str(run.id),
            pass_rate=pass_rate,
            passing=passing,
            failing=failing,
            total=len(results),
        )
        return updated

    except Exception as e:
        log.error("cq_test_run_failed", run_id=str(run.id), error=str(e))
        # F-0035 / ISS-0026: persist the error + completed_at (previously only
        # the status flipped, leaving no operator-visible failure detail).
        # Try the write directly; only roll back and retry when the session
        # holds a failed transaction (an unconditional rollback would discard
        # more than needed, e.g. under SAVEPOINT-bound test sessions).
        try:
            mark_test_run_failed(db, run.id, str(e))
        except Exception:
            db.rollback()
            mark_test_run_failed(db, run.id, str(e))
        raise


def _gate_decision(
    pass_rate: float,
    threshold: float,
    baseline_pass_rate: float | None,
    epsilon: float,
) -> tuple[bool, str]:
    """Pure gate decision (F-38). Differential when a baseline is supplied
    (``pass_rate >= baseline - epsilon``); absolute otherwise
    (``pass_rate >= threshold``)."""
    if baseline_pass_rate is not None:
        return (pass_rate >= (baseline_pass_rate - epsilon), "differential")
    return (pass_rate >= threshold, "absolute")


async def run_non_regression_gate(
    db: Session,
    proposed_schema_json: dict,
    threshold: float = 0.90,
    concurrency: int = 1,
    baseline_pass_rate: float | None = None,
    epsilon: float = 0.05,
) -> CQTestGateResult:
    """Run the non-regression quality gate against a proposed schema.

    F-38 (validation run, 2026-07-01) capture-the-why: the gate was purely
    ABSOLUTE (``pass_rate >= 0.90``). But an honest, gap-rich schema scores ~0.567
    even with a good judge (the deliberate golden gaps are real absences), so under
    an absolute 0.90 gate NO proposal could ever pass — the whole Signal→Proposal→
    Execute loop was dead. When ``baseline_pass_rate`` (the CURRENT/active schema's
    pass rate) is supplied, the gate becomes DIFFERENTIAL: the proposal passes when
    it does not regress the corpus's answerability by more than ``epsilon``
    (``proposed >= baseline - epsilon``). This is the correct "non-regression"
    semantic. When no baseline is supplied the absolute threshold is retained
    (backward compatible — existing callers/tests unchanged).
    """
    run = await run_cq_tests(
        db, proposed_schema_json=proposed_schema_json, concurrency=concurrency
    )

    failing_cqs = [r for r in run.results if r.result == CQTestResult.FAIL]

    gate_passed, gate_mode = _gate_decision(
        run.pass_rate, threshold, baseline_pass_rate, epsilon
    )

    return CQTestGateResult(
        gate_passed=gate_passed,
        pass_rate=run.pass_rate,
        threshold=threshold,
        baseline_pass_rate=baseline_pass_rate,
        gate_mode=gate_mode,
        total_cqs=run.total_cqs,
        passing=run.passing,
        failing=run.failing,
        failing_cqs=failing_cqs,
        test_run_id=run.id,
    )


def _build_gap_summary(results: list[CQTestResultEntry]) -> dict:
    """Build gap counts by type and severity."""
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}

    for r in results:
        if r.result == CQTestResult.FAIL:
            if r.gap_type:
                by_type[r.gap_type.value] = by_type.get(r.gap_type.value, 0) + 1
            if r.gap_severity:
                by_severity[r.gap_severity.value] = by_severity.get(r.gap_severity.value, 0) + 1

    return {"by_type": by_type, "by_severity": by_severity}
