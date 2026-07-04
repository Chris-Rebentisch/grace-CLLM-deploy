"""Proposal API routes — list, get, decide, execute (D387/D389, Chunk 47; D392/D393, Chunk 48).

D246 invariant: this module does NOT import ``src.ontology.proposal_generator``.
The generator runs CLI-only. CI guard at
``tests/ontology/test_proposal_route_invocation_surface.py`` enforces.

D393 scoped D246 exception: this module DOES import ``src.ontology.change_executor``
for the synchronous single-proposal execute route. This is one of two executor import
sites; ``daemon_routes.py`` is the second (D393, Chunk 50). CI guard at
``tests/ontology/test_change_executor_route_isolation.py`` enforces both conditions
(change_executor IS present, proposal_generator IS absent).

Invariant: D389 route-isolation.
Carve-out: D393 synchronous execute.
Authorization source: chunk-48-spec-v6-FINAL.md §4 D393.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from src.ontology.change_executor import ExecutionResult, apply_proposal  # D393 scoped exception
from src.ontology.database import (
    GovernanceDecisionEventRow,
    SchemaProposalRow,
    get_active_version,
    get_proposal_by_id,
    list_proposals,
    update_proposal_decision,
)
from src.ontology.diff_engine import compute_om4ov_diff
from src.ontology.evidence_bundle import affected_types_from_parsed_change
from src.ontology.kgcl_models import KGCLParseError, ProposedSchemaChange
from src.ontology.kgcl_parser import parse_kgcl
from src.ontology.models import (
    HumanDecision,
    ProposalPriority,
    ProposalStatus,
    ProposalType,
    SignalType,
    classify_tier,
)
from src.shared.database import get_db

logger = structlog.get_logger()

router = APIRouter(prefix="/api/ontology/proposals", tags=["proposals"])

# Sentinel session for server-side proposal telemetry when no UI session is supplied
# (Chunk 47 AC16 — elicitation path parallels OTel; auth posture unchanged).
_PROPOSAL_TELEMETRY_SESSION_ID = UUID("00000000-0000-0000-0000-000000000047")


# --- Request / response models ---


class ProposalDecideRequest(BaseModel):
    """Body for POST /api/ontology/proposals/{proposal_id}/decide."""

    model_config = ConfigDict(extra="forbid")

    decision: HumanDecision = Field(description="Human reviewer decision")
    reviewer: str = Field(min_length=1, description="Reviewer identifier")
    modified_diff: dict | None = Field(default=None, description="Diff if decision=modified")
    notes: str | None = Field(default=None, description="Optional reviewer notes")


class ProposalCreateRequest(BaseModel):
    """Body for POST /api/ontology/proposals (D462).

    Exactly one of ``command_text`` or ``parsed_change`` must be provided.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_type: ProposalType
    command_text: str | None = None
    parsed_change: ProposedSchemaChange | None = None
    ontology_module: str | None = None

    @model_validator(mode="after")
    def _exactly_one_of_input(self) -> ProposalCreateRequest:
        has_text = self.command_text is not None
        has_parsed = self.parsed_change is not None
        if has_text and has_parsed:
            msg = "Provide exactly one of 'command_text' or 'parsed_change', not both"
            raise ValueError(msg)
        if not has_text and not has_parsed:
            msg = "Provide exactly one of 'command_text' or 'parsed_change'"
            raise ValueError(msg)
        return self


# --- Schema lookup adapter (D461/D462) ---


def _schema_lookup(db: Session) -> Callable[[str], list[str]]:
    """Build a schema-aware lookup closure for KGCL short-form disambiguation.

    Reads the active ontology version's ``schema_json["entity_types"]`` and
    constructs reverse indexes: ``property_name -> [class_names]`` and
    ``entity_name -> [class_names]``.

    Invariant: top-level entity container is ``schema_json["entity_types"]``
    (matching ``_apply_change_to_schema`` at ``change_executor.py:80``).
    Authorization: D461 / D462.
    """
    active = get_active_version(db)
    if active is None:
        # No active schema — all lookups return empty (triggers ENTITY_NOT_FOUND).
        return lambda _name: []

    entity_types: dict = active.schema_json.get("entity_types", {})

    # Reverse indexes.
    property_to_classes: dict[str, list[str]] = {}
    entity_name_to_classes: dict[str, list[str]] = {}

    for class_name, class_def in entity_types.items():
        # Index entity name for add-synonym lookups.
        entity_name_to_classes.setdefault(class_name, []).append(class_name)

        # Index properties for rename-property lookups.
        props = class_def.get("properties", {}) if isinstance(class_def, dict) else {}
        for prop_name in props:
            property_to_classes.setdefault(prop_name, []).append(class_name)

    def _lookup(name: str) -> list[str]:
        # Check property index first (rename property), then entity name (add synonym).
        if name in property_to_classes:
            return property_to_classes[name]
        if name in entity_name_to_classes:
            return entity_name_to_classes[name]
        return []

    return _lookup


def _serialize_change_to_kgcl(change: ProposedSchemaChange) -> str:
    """Reconstruct canonical KGCL command text from a ProposedSchemaChange.

    Needed for the ``parsed_change`` input path — ``kgcl_command`` is NOT NULL.
    """
    from src.ontology.kgcl_models import KGCLCommandKind

    kind = change.command_kind

    if kind == KGCLCommandKind.CREATE_CLASS:
        return f"create class '{change.target_name}'"
    if kind == KGCLCommandKind.OBSOLETE_CLASS:
        return f"obsolete class '{change.target_name}'"
    if kind == KGCLCommandKind.CHANGE_DESCRIPTION:
        return f"change description of '{change.target_name}'"
    if kind == KGCLCommandKind.CREATE_RELATIONSHIP:
        return f"create relationship '{change.target_name}'"
    if kind == KGCLCommandKind.OBSOLETE_RELATIONSHIP:
        return f"obsolete relationship '{change.target_name}'"
    if kind == KGCLCommandKind.CHANGE_RELATIONSHIP:
        return f"change relationship '{change.target_name}'"
    if kind == KGCLCommandKind.ADD_PROPERTY:
        return f"add property '{change.property_name or change.target_name}' to class '{change.entity_name}'"
    if kind == KGCLCommandKind.REMOVE_PROPERTY:
        return f"remove property '{change.property_name or change.target_name}' from class '{change.entity_name}'"
    if kind == KGCLCommandKind.CHANGE_PROPERTY:
        return f"change property '{change.property_name or change.target_name}' on class '{change.entity_name}'"
    if kind == KGCLCommandKind.ADD_SYNONYM:
        return f"add synonym '{change.synonym}' for class '{change.target_name}'"
    if kind == KGCLCommandKind.RENAME_PROPERTY:
        return f"rename property '{change.property_name or change.target_name}' to '{change.new_name}' on class '{change.entity_name}'"
    if kind == KGCLCommandKind.SPLIT_CLASS:
        targets = " ".join(f"'{t}'" for t in (change.split_into or []))
        return f"split class '{change.target_name}' into {targets}"
    if kind == KGCLCommandKind.MOVE_CLASS:
        return f"move class '{change.target_name}' from '{change.old_parent}' to '{change.new_parent}'"
    if kind == KGCLCommandKind.CHANGE_DOMAIN_RANGE:
        return f"change {change.change_target} of '{change.target_name}' to '{change.to_type}'"

    return f"# unsupported command kind: {kind.value}"


# --- Idempotency cache for create route (D462) ---

# In-memory cache: {key: (status_code, body_dict, monotonic_timestamp)}
_idempotency_cache: dict[str, tuple[int, dict, float]] = {}
_IDEMPOTENCY_TTL_SECONDS = 3600  # 60 minutes


def _idempotency_evict() -> None:
    """Evict stale entries from the idempotency cache."""
    now = time.monotonic()
    stale = [k for k, (_, _, ts) in _idempotency_cache.items() if now - ts > _IDEMPOTENCY_TTL_SECONDS]
    for k in stale:
        del _idempotency_cache[k]


# --- Cursor pagination helper ---


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc


# --- Modification distance calculation (D389) ---

# Mutable fields for modification_distance denominator.
# Excludes id, created_at, status, generated_at per spec.
_MUTABLE_FIELDS = frozenset({
    "proposal_type", "change_tier", "kgcl_command", "proposed_diff",
    "evidence", "signal_type", "raw_confidence", "priority",
    "current_schema_version_id", "reviewer", "human_decision",
    "modification_distance", "modified_diff", "applied_autonomously",
    "autonomy_confidence_at_time", "trust_score_at_time",
    "resulting_version_id", "cooling_period_expires_at",
    "cooling_period_reverted", "metadata_extra", "ontology_module",
    "dedup_hash", "overflow",
})


def _compute_modification_distance(modified_diff: dict | None) -> float | None:
    """D389: count(changed_fields) / count(total_mutable_fields)."""
    if modified_diff is None:
        return None
    changed = len(modified_diff)
    total = len(_MUTABLE_FIELDS)
    return min(changed / total, 1.0) if total > 0 else 0.0


# --- Routes ---


@router.post("", status_code=201)
async def create_proposal_route(
    body: ProposalCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Create a schema proposal from KGCL text or pre-parsed change (D462).

    Mutating — admin-key required when ``GRACE_ADMIN_KEY`` is set; loopback
    bypass otherwise.

    Invariant: D389 route-isolation — this module does NOT import
    ``src.ontology.proposal_generator``.
    Carve-out: D462 amends D389 to permit a create route while preserving
    the generator import prohibition.
    Authorization source: chunk-70-spec-v5-FINAL.md §4 D462 / D356.
    """
    from src.ontology.change_executor import _apply_change_to_schema

    # --- Idempotency-Key check ---
    if idempotency_key is not None:
        if not (8 <= len(idempotency_key) <= 128):
            raise HTTPException(status_code=422, detail="Idempotency-Key must be 8–128 characters")
        _idempotency_evict()
        cached = _idempotency_cache.get(idempotency_key)
        if cached is not None:
            status_code, cached_body, _ts = cached
            return cached_body

    # --- Parse or validate ---
    if body.command_text is not None:
        lookup_fn = _schema_lookup(db)
        try:
            parsed = parse_kgcl(body.command_text, schema_lookup_fn=lookup_fn)
        except KGCLParseError as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": e.message,
                    "error_kind": e.error_kind,
                    "candidates": e.candidates,
                },
            ) from e
        kgcl_command = body.command_text
    else:
        # parsed_change path — already validated by Pydantic.
        parsed = body.parsed_change  # type: ignore[assignment]
        kgcl_command = _serialize_change_to_kgcl(parsed)

    # --- Generate proposed_diff ---
    active = get_active_version(db)
    if active is None:
        raise HTTPException(status_code=422, detail="No active ontology version")

    new_schema = _apply_change_to_schema(active.schema_json, parsed)
    proposed_diff = compute_om4ov_diff(active.schema_json, new_schema)

    # --- Tier classification ---
    change_tier = classify_tier(body.proposal_type)

    # --- Minimal evidence dict (spec §4 D462 — coercible by evidence_bundle_from_db) ---
    # Capture-the-why (F-0042 / ISS-0053, validation run 2026-07-03):
    # operator-authored proposals previously fabricated signal scaffolding
    # (signal_type="A", signal_strength=0.0) despite having NO source
    # signals — a contradictory pairing accepted silently. Documented
    # choice: NORMALIZATION — signal fields are absent (None) when
    # source_signal_ids is empty (the create route accepts no signal
    # fields, so there is nothing for a 422 to reject). raw_confidence
    # is None: a human-initiated proposal has NO agent confidence, and
    # D120/D217 forbids fabricating one. (The interim 1.0 sentinel was
    # removed 2026-07-03 when migration r4a_raw_confidence_nullable
    # dropped the NOT NULL constraint — ISS-0053 deferral closure.)
    # F-0040 / ISS-0053: affected_entity_types comes from the parsed
    # change — never empty when the KGCL target names the type.
    evidence = {
        "source_signal_ids": [],
        "signal_type": None,
        "signal_strength": None,
        "affected_entity_types": affected_types_from_parsed_change(parsed),
        "ontology_module": body.ontology_module or "general",
    }

    # --- Persist ---
    row = SchemaProposalRow(
        id=uuid4(),
        proposal_type=body.proposal_type.value,
        change_tier=change_tier,
        kgcl_command=kgcl_command,
        proposed_diff=proposed_diff,
        evidence=evidence,
        raw_confidence=None,
        priority="medium",
        status="pending",
        current_schema_version_id=active.id,
        ontology_module=body.ontology_module,
        # F-0042 / ISS-0053: honest provenance — this row was operator-
        # authored, not signal-sourced.
        signal_type=SignalType.HUMAN_INITIATED.value,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    response_body = {
        "id": str(row.id),
        "status": row.status,
        "proposal_type": row.proposal_type,
        "change_tier": row.change_tier,
        "kgcl_command": row.kgcl_command,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }

    # --- Cache for idempotency ---
    if idempotency_key is not None:
        _idempotency_cache[idempotency_key] = (201, response_body, time.monotonic())

    return response_body


@router.get("")
async def list_proposals_route(
    tier: int | None = Query(default=None, ge=1, le=3),
    status: str | None = Query(default=None),
    ontology_module: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """List proposals with tier-weighted FIFO ordering."""
    offset = _decode_cursor(cursor)

    status_enum = None
    if status is not None:
        try:
            status_enum = ProposalStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid status: {status}") from exc

    proposals = list_proposals(
        db,
        status=status_enum,
        change_tier=tier,
        ontology_module=ontology_module,
        limit=limit + 1,
        offset=offset,
    )

    next_cursor: str | None = None
    if len(proposals) > limit:
        proposals = proposals[:limit]
        next_cursor = str(offset + limit)

    items = [p.model_dump(mode="json") for p in proposals]
    return {"items": items, "next_cursor": next_cursor}


@router.get("/{proposal_id}")
async def get_proposal_route(
    proposal_id: UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Get a single proposal with typed EvidenceBundle."""
    proposal = get_proposal_by_id(db, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal.model_dump(mode="json")


@router.post("/{proposal_id}/decide")
async def decide_proposal_route(
    proposal_id: UUID,
    body: ProposalDecideRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Record a human decision on a proposal. Mutating — admin-key required."""
    # Check proposal exists and is pending.
    existing = get_proposal_by_id(db, proposal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if existing.status != ProposalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Proposal status is '{existing.status.value}', expected 'pending'",
        )

    # Compute modification distance for "modified" decisions (D389).
    mod_distance = _compute_modification_distance(body.modified_diff) if body.decision == HumanDecision.MODIFIED else None

    updated = update_proposal_decision(
        db,
        proposal_id=proposal_id,
        human_decision=body.decision,
        reviewer=body.reviewer,
        modification_distance=mod_distance,
        modified_diff=body.modified_diff,
    )

    if updated is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Chunk 49 (D394) — best-effort calibration decision INSERT + elicitation emit.
    # Maps APPROVED/MODIFIED → "approved", REJECTED → "rejected", DEFERRED → skip.
    # F-0042 / ISS-0053 deferral closure: raw_confidence is None for
    # human-initiated / signal-less proposals (r4a_raw_confidence_nullable)
    # — skip calibration recording for those: there is no agent confidence
    # to calibrate against, and calibration_decisions.raw_confidence stays
    # NOT NULL by design (only signal-backed confidence is calibratable).
    try:
        if body.decision != HumanDecision.DEFERRED and updated.raw_confidence is not None:
            cal_decision = "rejected" if body.decision == HumanDecision.REJECTED else "approved"

            from src.ontology.database import create_calibration_decision

            create_calibration_decision(
                db,
                proposal_id=proposal_id,
                tier=updated.change_tier,
                raw_confidence=updated.raw_confidence,
                decision=cal_decision,
                modification_distance=mod_distance,
                ontology_module=getattr(updated, "ontology_module", None),
            )

            # OTel counter for calibration decisions.
            from src.analytics.metrics import record_calibration_decision

            record_calibration_decision(
                tier=str(updated.change_tier),
                decision=cal_decision,
            )

            # Elicitation event for calibration decision.
            from src.elicitation.event_writer import write_event as _cal_write_event
            from src.elicitation.models import (
                ElicitationEventEnvelope as _CalEnvelope,
                validate_payload_for_event_type as _cal_validate,
            )

            _cal_validated = _cal_validate(
                "calibration_decision_recorded",
                {
                    "proposal_id": str(proposal_id),
                    "tier": updated.change_tier,
                    "decision": cal_decision,
                },
            )
            _cal_envelope = _CalEnvelope(
                event_id=uuid4(),
                event_type="calibration_decision_recorded",
                session_id=_PROPOSAL_TELEMETRY_SESSION_ID,
                actor_type="human",
                phase_name="none",
                emitted_at=datetime.now(UTC),
                schema_version=1,
                grace_version="0.1.0",
                payload=_cal_validated.model_dump(mode="json"),
                payload_schema_version=1,
            )
            _cal_write_event(db, _cal_envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proposal.decide.calibration_hook_failed", error=str(exc))

    # Best-effort OTel counter.
    try:
        from src.analytics.metrics import record_proposal_decided
        record_proposal_decided(decision=body.decision.value)
    except Exception:  # noqa: BLE001
        pass

    # D387 — append-only elicitation (spec §6 Step 7 / AC16).
    try:
        from src.elicitation.event_writer import write_event
        from src.elicitation.models import (
            ElicitationEventEnvelope,
            validate_payload_for_event_type,
        )

        validated = validate_payload_for_event_type(
            "proposal_decided",
            {
                "proposal_id": str(proposal_id),
                "decision": body.decision.value,
                "reviewer_hash": hashlib.sha256(
                    body.reviewer.encode("utf-8"),
                ).hexdigest(),
            },
        )
        envelope = ElicitationEventEnvelope(
            event_id=uuid4(),
            event_type="proposal_decided",
            session_id=_PROPOSAL_TELEMETRY_SESSION_ID,
            actor_type="human",
            phase_name="none",
            emitted_at=datetime.now(UTC),
            schema_version=1,
            grace_version="0.1.0",
            payload=validated.model_dump(mode="json"),
            payload_schema_version=1,
        )
        write_event(db, envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proposal.decide.telemetry_failed", error=str(exc))

    return updated.model_dump(mode="json")


# --- Chunk 48 routes: execute, preview, batch-trigger (D392/D393) ---

# In-flight batch tracking for concurrent-trigger protection.
_batch_in_progress: dict[str, UUID] = {}


def _release_batch_lock_when_proc_exits(proc: subprocess.Popen) -> None:
    """Pop DV1 in-flight sentinel once the spawned batch child process exits."""

    try:
        proc.wait()
    finally:
        _batch_in_progress.pop("active", None)


@router.post("/{proposal_id}/execute")
async def execute_proposal_route(
    proposal_id: UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Execute an approved proposal. Mutating — admin-key required (D393).

    D393 scoped D246 exception: imports ``change_executor.apply_proposal``
    directly for synchronous single-proposal execution.
    """
    # Pre-check: proposal exists and is approved.
    existing = get_proposal_by_id(db, proposal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if existing.status == ProposalStatus.APPLIED:
        raise HTTPException(status_code=409, detail="Proposal already applied")
    if existing.status != ProposalStatus.APPROVED:
        raise HTTPException(
            status_code=409,
            detail=f"Proposal status is '{existing.status.value}', expected 'approved'",
        )

    result = await apply_proposal(db, proposal_id)

    # Best-effort telemetry: emit proposal_executed event.
    try:
        from src.elicitation.event_writer import write_event
        from src.elicitation.models import (
            ElicitationEventEnvelope,
            validate_payload_for_event_type,
        )

        outcome = "applied" if result.success else (result.error or "error")
        validated = validate_payload_for_event_type(
            "proposal_executed",
            {
                "proposal_id": str(proposal_id),
                "tier": classify_tier(existing.proposal_type),
                "outcome": outcome,
            },
        )
        envelope = ElicitationEventEnvelope(
            event_id=uuid4(),
            event_type="proposal_executed",
            session_id=_PROPOSAL_TELEMETRY_SESSION_ID,
            actor_type="system",
            phase_name="none",
            emitted_at=datetime.now(UTC),
            schema_version=1,
            grace_version="0.1.0",
            payload=validated.model_dump(mode="json"),
            payload_schema_version=1,
        )
        write_event(db, envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proposal.execute.telemetry_failed", error=str(exc))

    return result.model_dump(mode="json")


# F-0040 / ISS-0053: static per-tier review-obligation legend so reviewers
# see what a proposal's change_tier obligates WITHOUT cross-referencing docs.
_CHANGE_TIER_LEGEND: dict[int, str] = {
    1: (
        "Tier 1 (low risk — additive property/synonym). Eligible for earned-"
        "autonomy auto-apply once calibrated; human review optional but "
        "recommended while trust is being established."
    ),
    2: (
        "Tier 2 (medium risk — new entity type, new relationship, or property "
        "modification). Eligible for earned-autonomy auto-apply at higher trust "
        "thresholds; human review expected by default."
    ),
    3: (
        "Tier 3 (high risk — split/merge/deprecate types, hierarchy or "
        "domain/range changes). ALWAYS human-reviewed; never auto-applied "
        "(Earned Autonomy System hard rule)."
    ),
}

# F-0040 / ISS-0053: preview never runs the CQ gate — say so explicitly.
_CQ_GATE_NOTE = (
    "The CQ non-regression gate runs at execute time "
    "(POST /api/ontology/proposals/{proposal_id}/execute), not during preview."
)


def _get_graph_client():
    """Build an ArcadeClient from settings (mirrors graph_routes._get_client).

    Uses ``ArcadeConfig.from_settings`` — NEVER bare ``ArcadeConfig()``
    (blind-run-3 F-022 class: bare config silently reads the wrong live DB).
    Separate function so tests can patch it.
    """
    from src.graph.arcade_client import ArcadeClient
    from src.graph.config import ArcadeConfig
    from src.shared.config import get_settings

    return ArcadeClient(config=ArcadeConfig.from_settings(get_settings()))


def _first_count(result: dict) -> int | None:
    """Pull the first integer value out of an ArcadeDB count-query result."""
    rows = result.get("result") or []
    if rows and isinstance(rows[0], dict):
        for value in rows[0].values():
            if isinstance(value, (int, float)):
                return int(value)
    return None


_SAFE_TYPE_NAME = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


async def _collect_usage_counts(db: Session, affected_types: list[str]) -> dict:
    """Per-affected-type usage counts for the preview response.

    Capture-the-why (F-0040 / ISS-0053, validation run 2026-07-03):
    reviewers deciding a deprecate_type proposal had NO usage counts — no
    way to see whether the type is live. Adds instance count and inbound-
    relationship count (graph) plus open-claim count (Postgres
    extraction_claims, status='quarantined'). Read-only; tolerates an
    absent graph gracefully (counts null + graph_available=false).
    """
    usage: dict[str, dict] = {}
    graph_available = True
    client = None
    try:
        client = _get_graph_client()
    except Exception:  # noqa: BLE001
        graph_available = False

    try:
        for type_name in affected_types:
            entry: dict[str, int | None] = {
                "instance_count": None,
                "open_claim_count": None,
                "inbound_relationship_count": None,
            }
            # Defense: type names are interpolated into Cypher — only allow
            # identifier characters (matches ontology PascalCase_With_Underscores).
            name_safe = bool(type_name) and all(c in _SAFE_TYPE_NAME for c in type_name)

            if graph_available and name_safe:
                try:
                    res = await client.execute_cypher(
                        f"MATCH (n:`{type_name}`) RETURN count(n) AS c"
                    )
                    entry["instance_count"] = _first_count(res)
                    res = await client.execute_cypher(
                        f"MATCH (:`{type_name}`)<-[r]-() RETURN count(r) AS c"
                    )
                    entry["inbound_relationship_count"] = _first_count(res)
                except Exception:  # noqa: BLE001
                    graph_available = False
                    logger.warning(
                        "proposal.preview.graph_unavailable",
                        affected_type=type_name,
                    )

            try:
                from sqlalchemy import text as _sql_text

                claim_count = db.execute(
                    _sql_text(
                        "SELECT count(*) FROM extraction_claims "
                        "WHERE status = 'quarantined' AND "
                        "(entity_type = :t OR subject_type = :t OR object_type = :t)"
                    ),
                    {"t": type_name},
                ).scalar()
                entry["open_claim_count"] = int(claim_count or 0)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "proposal.preview.claim_count_unavailable",
                    affected_type=type_name,
                )

            usage[type_name] = entry
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    return {"by_type": usage, "graph_available": graph_available}


@router.post("/{proposal_id}/preview")
async def preview_proposal_route(
    proposal_id: UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Preview a proposal's parsed change and diff without persisting.

    Read-only POST per D237 READONLY_ROUTES — no CQ gate, no ratification.

    F-0040 / ISS-0053 (validation run 2026-07-03): response now also
    carries ``affected_entity_types``, per-type ``usage`` counts,
    ``change_tier_legend``, and an explicit ``cq_gate`` note. NOTE:
    ``proposed_diff`` on the ROW is populated at creation time only — the
    c47a append-only trigger makes it immutable post-INSERT, so preview
    must NOT attempt a row backfill (it would be rejected by the trigger).
    """
    from src.ontology.change_executor import _apply_change_to_schema
    from src.ontology.kgcl_models import KGCLParseError

    existing = get_proposal_by_id(db, proposal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    try:
        parsed = parse_kgcl(existing.kgcl_command)
    except KGCLParseError as e:
        raise HTTPException(status_code=422, detail=f"KGCL parse error: {e.message}") from e

    active = get_active_version(db)
    if active is None:
        raise HTTPException(status_code=422, detail="No active ontology version")

    new_schema = _apply_change_to_schema(active.schema_json, parsed)
    diff = compute_om4ov_diff(active.schema_json, new_schema)

    # F-0040 / ISS-0053: affected types from the parse result (never rely
    # on the possibly-empty stored evidence bundle).
    affected_types = affected_types_from_parsed_change(parsed)
    usage = await _collect_usage_counts(db, affected_types)

    tier = existing.change_tier

    return {
        "parsed": parsed.model_dump(mode="json"),
        "diff": diff,
        "affected_entity_types": affected_types,
        "usage": usage,
        "change_tier": tier,
        "change_tier_legend": {
            "current": _CHANGE_TIER_LEGEND.get(tier),
            "tiers": {str(k): v for k, v in _CHANGE_TIER_LEGEND.items()},
        },
        "cq_gate": {"runs_at": "execute", "note": _CQ_GATE_NOTE},
    }


class ProposalCorrectRequest(BaseModel):
    """Body for POST /api/ontology/proposals/{proposal_id}/correct (D448)."""
    model_config = ConfigDict(extra="forbid")
    proposal_type: str = Field(description="Corrected ProposalType enum value")
    reason: str = Field(min_length=1, description="Reason for the correction")


@router.post("/{proposal_id}/correct")
async def correct_proposal_route(
    proposal_id: UUID,
    body: ProposalCorrectRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Correct a proposal's proposal_type within the 60-minute carve-out (D448).

    Admin-key gated. Exercises the c65b trigger correction carve-out:
    proposal_type UPDATE within 60 minutes of row creation when is_correction
    flips false → true, one-shot only.

    Invariant: schema_proposals append-only trigger (Chunk 47).
    Carve-out: narrow proposal_type-only correction within 60-minute window,
               one-shot is_correction flip (false → true).
    Authorization: D448.
    """
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    # Validate proposal_type against the ProposalType enum
    try:
        validated_type = ProposalType(body.proposal_type)
    except ValueError as exc:
        valid_values = [e.value for e in ProposalType]
        raise HTTPException(
            status_code=422,
            detail=f"Invalid proposal_type: {body.proposal_type}. Valid values: {valid_values}",
        ) from exc

    proposal = db.query(SchemaProposalRow).filter_by(id=proposal_id).first()
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    try:
        proposal.proposal_type = validated_type.value
        proposal.is_correction = True

        # Record governance event
        gov_event = GovernanceDecisionEventRow(
            decision_type="proposal_corrected",
            agent_id="operator",
            proposal_id=proposal_id,
            outcome="corrected",
            reason=body.reason,
            recorded_at=datetime.now(UTC),
        )
        db.add(gov_event)
        db.commit()
    except SAIntegrityError as exc:
        db.rollback()
        detail = str(exc.orig) if exc.orig else str(exc)
        # Trigger RAISE EXCEPTION with ERRCODE='check_violation' surfaces as
        # psycopg2 CheckViolation (pgcode 23514).  Match on pgcode or message.
        pgcode = getattr(exc.orig, "pgcode", "") or ""
        if pgcode == "23514" or "append-only" in detail.lower() or "correction" in detail.lower():
            raise HTTPException(
                status_code=409,
                detail=f"Correction rejected by trigger: {detail}",
            ) from exc
        raise HTTPException(status_code=500, detail=detail) from exc
    except Exception as exc:
        db.rollback()
        logger.error("proposal.correct.error", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=500, detail="Internal error while processing proposal"
        ) from exc

    return {
        "id": str(proposal.id),
        "proposal_type": proposal.proposal_type,
        "is_correction": True,
        "status": "corrected",
    }


@router.post("/batch-trigger", status_code=202)
async def batch_trigger_route() -> dict:
    """Trigger batch execution of all approved proposals. Mutating — admin-key required.

    D246-compliant: spawns CLI via subprocess.Popen. Returns 202 + batch_id.
    """
    batch_id = uuid4()

    # Concurrent-trigger protection.
    if _batch_in_progress:
        existing_id = next(iter(_batch_in_progress.values()))
        raise HTTPException(
            status_code=409,
            detail=f"Batch already in progress: {existing_id}",
        )

    _batch_in_progress["active"] = batch_id

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "src.ontology.change_executor", "batch"],
            start_new_session=True,
        )
    except Exception as exc:
        _batch_in_progress.pop("active", None)
        raise HTTPException(status_code=500, detail=f"Failed to start batch: {exc}") from exc

    threading.Thread(
        target=_release_batch_lock_when_proc_exits,
        args=(proc,),
        daemon=True,
        name=f"grace-change-executor-batch-wait-{batch_id}",
    ).start()

    return {"batch_id": str(batch_id), "status": "accepted"}
