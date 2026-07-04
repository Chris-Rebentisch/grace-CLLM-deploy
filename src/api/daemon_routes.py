"""Agent daemon API routes (Chunk 50, D398/D399/D400).

Four routes under ``/api/ontology/daemon/``:
  - PATCH /kill-switch — admin-key gated
  - GET /status — read path, no auth
  - POST /{proposal_id}/confirm — admin-key gated
  - POST /{proposal_id}/revert — admin-key gated

D246 invariant: this module does NOT import ``src.ontology.agent_daemon``.
The daemon runs CLI-only.

D393 scoped D246 exception: this module DOES import ``src.ontology.change_executor``
for confirm/revert proposal execution. This is a D393-authorized second import site
(after ``proposal_routes.py``). CI guard at
``tests/ontology/test_agent_daemon_route_isolation.py`` enforces.

Invariant: D246 — agent_daemon never imported by route modules.
Carve-out: D393 — change_executor.apply_proposal imported for revert flow.
Authorization source: chunk-50-spec-v5-FINAL.md §7.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.elicitation.bridge import enqueue_event
from src.ontology.database import (
    GovernanceDecisionEventRow,
    KillSwitchHistoryRow,
    SchemaProposalRow,
    TrustScoreRow,
)
from src.ontology.kgcl_inverter import invert as kgcl_invert
from src.ontology.models import ProposalStatus
from src.shared.database import get_session_factory

logger = structlog.get_logger(__name__)
UTC = timezone.utc

router = APIRouter(prefix="/api/ontology/daemon", tags=["daemon"])


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _require_admin_key(request: Request) -> None:
    """Mutating-route admin-key enforcement."""
    admin_key = os.environ.get("GRACE_ADMIN_KEY", "")
    if not admin_key:
        client_host = request.client.host if request.client else None
        if client_host in {"127.0.0.1", "::1", "testclient"}:
            return
        raise HTTPException(status_code=401, detail="admin key required")
    submitted = request.headers.get("X-Admin-Key", "")
    if not submitted or not secrets.compare_digest(admin_key, submitted):
        raise HTTPException(status_code=401, detail="admin key required")


def _get_db():
    factory = get_session_factory()
    return factory()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class KillSwitchRequest(BaseModel):
    """Request body for PATCH /kill-switch.

    Chunk 65 (D446): ``reason`` is mandatory. Under admin-key path,
    reason.strip() must be >= 10 chars (422). Under loopback dev bypass,
    empty reason is admitted with ``<dev-loopback-no-reason>`` placeholder.
    """
    autonomy_enabled: bool
    reason: str = ""


class KillSwitchResponse(BaseModel):
    """Response for PATCH /kill-switch.

    Chunk 65 (D446/D447) extends with per-tier state snapshots and history row ID.
    """
    autonomy_enabled: bool
    tiers_updated: int
    previous_state: dict[str, bool] | None = None
    restored_state: dict[str, bool] | None = None
    history_id: str | None = None


class TierStatus(BaseModel):
    """Per-tier status."""
    tier: int
    autonomy_enabled: bool
    regression_detected: bool


class DaemonStatusResponse(BaseModel):
    """Response for GET /status.

    Chunk 65 (D447): ``previous_state`` carries the per-tier state snapshot
    from the open ``kill_switch_history`` row when ``kill_switch_engaged=true``
    (for the CP8 restore-state dialog).
    """
    last_tick_at: str | None = None
    proposals_in_cooling: int
    kill_switch_engaged: bool
    tiers: list[TierStatus]
    previous_state: dict[str, bool] | None = None


class RevertRequest(BaseModel):
    """Request body for POST /{proposal_id}/revert.

    F-0043 / ISS-0050 (validation run 2026-07-03): revert required
    ``reverted_by`` while the sibling confirm route family used ``reviewer``,
    so operators 422'd on first try. Both routes now accept BOTH field names
    additively; ``reverted_by`` remains the documented/preferred name here.
    """
    model_config = ConfigDict(populate_by_name=True)

    reverted_by: str = Field(
        validation_alias=AliasChoices("reverted_by", "reviewer"),
        description="Operator handle performing the revert (alias: reviewer).",
    )
    reason: str | None = None


class ConfirmRequest(BaseModel):
    """Optional request body for POST /{proposal_id}/confirm.

    F-0043 / ISS-0050: confirm historically took no body; it now accepts an
    optional operator handle under EITHER ``reviewer`` (documented/preferred)
    or ``reverted_by`` (alias, symmetric with the revert route). Omitting the
    body preserves the previous behavior (recorded as "operator").
    """
    model_config = ConfigDict(populate_by_name=True)

    reviewer: str | None = Field(
        default=None,
        validation_alias=AliasChoices("reviewer", "reverted_by"),
        description="Operator handle performing the confirm (alias: reverted_by).",
    )
    reason: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _validate_reason(body: KillSwitchRequest, request: Request) -> str:
    """Validate and return the reason string per D446 rules.

    Under admin-key path: reason.strip() must be >= 10 chars (422).
    Under loopback dev bypass: empty reason admitted with placeholder.
    """
    admin_key = os.environ.get("GRACE_ADMIN_KEY", "")
    reason = body.reason.strip() if body.reason else ""
    if admin_key:
        # Admin-key path — enforce minimum length
        if len(reason) < 10:
            raise HTTPException(
                status_code=422,
                detail="reason must be at least 10 characters when admin key is set",
            )
        return reason
    # Loopback dev bypass — admit empty reason with placeholder
    return reason if reason else "<dev-loopback-no-reason>"


@router.patch("/kill-switch")
def kill_switch(body: KillSwitchRequest, request: Request):
    """Single-transaction four-table kill-switch audit write (D446/D447).

    Transaction shape: kill_switch_history + trust_scores + governance_decision_events
    + elicitation_events. All succeed or all roll back — the trust-score mutation
    MUST NOT persist without its audit trail.
    """
    _require_admin_key(request)
    reason = _validate_reason(body, request)

    db = _get_db()
    try:
        tiers = db.query(TrustScoreRow).all()

        # Step A: Snapshot per-tier autonomy_enabled as previous_state.
        previous_state: dict[str, bool] = {
            str(row.tier): row.autonomy_enabled for row in tiers
        }

        engaging = not body.autonomy_enabled  # engage = disabling autonomy
        now = datetime.now(UTC)

        if engaging:
            # --- ENGAGE flow ---
            from uuid import uuid4 as _uuid4
            history_id = _uuid4()
            history_row = KillSwitchHistoryRow(
                id=history_id,
                engaged_at=now,
                engaged_by=request.headers.get("X-Admin-Key-Actor", "operator"),
                reason=reason,
                previous_state=previous_state,
            )
            db.add(history_row)

            # Mutate trust_scores
            updated = 0
            for row in tiers:
                row.autonomy_enabled = body.autonomy_enabled
                updated += 1

            # GovernanceDecisionEventRow
            gov_event = GovernanceDecisionEventRow(
                decision_type="kill_switch_engaged",
                agent_id="operator",
                outcome="engaged",
                reason=reason,
                recorded_at=now,
            )
            db.add(gov_event)

            # Elicitation event via CP3-extended enqueue_event (D446 session-id pairing)
            enqueue_event(
                event_type="kill_switch_engaged",
                payload={
                    "actor": request.headers.get("X-Admin-Key-Actor", "operator"),
                    "all_tiers_disabled": True,
                    "reason": reason,
                    "previous_state": previous_state,
                },
                db=db,
                session_id_override=history_id,
            )

            # Single commit — all four writes succeed or all roll back
            db.commit()

            return KillSwitchResponse(
                autonomy_enabled=body.autonomy_enabled,
                tiers_updated=updated,
                previous_state=previous_state,
                history_id=str(history_id),
            )

        else:
            # --- DISENGAGE flow ---
            # Read the open engage row
            open_row = db.query(KillSwitchHistoryRow).filter(
                KillSwitchHistoryRow.disengaged_at.is_(None)
            ).first()

            if open_row is not None:
                # Restore per-tier state from snapshot (NOT blanket-enable per D447)
                restored_state = open_row.previous_state or {}
                updated = 0
                for row in tiers:
                    tier_key = str(row.tier)
                    if tier_key in restored_state:
                        row.autonomy_enabled = restored_state[tier_key]
                    else:
                        row.autonomy_enabled = body.autonomy_enabled
                    updated += 1

                # Close the history row
                open_row.disengaged_at = now
                open_row.restored_state = restored_state

                history_id = open_row.id

                # GovernanceDecisionEventRow
                gov_event = GovernanceDecisionEventRow(
                    decision_type="kill_switch_disengaged",
                    agent_id="operator",
                    outcome="disengaged",
                    reason=reason,
                    recorded_at=now,
                )
                db.add(gov_event)

                # Elicitation event (D446 session-id pairing — same history_id)
                enqueue_event(
                    event_type="kill_switch_disengaged",
                    payload={
                        "actor": request.headers.get("X-Admin-Key-Actor", "operator"),
                        "all_tiers_enabled": all(restored_state.get(str(t.tier), True) for t in tiers),
                        "reason": reason,
                        "restored_state": restored_state,
                    },
                    db=db,
                    session_id_override=history_id,
                )

                db.commit()

                return KillSwitchResponse(
                    autonomy_enabled=body.autonomy_enabled,
                    tiers_updated=updated,
                    previous_state=previous_state,
                    restored_state=restored_state,
                    history_id=str(history_id),
                )
            else:
                # F-027 / ISS-0010: a disengage/enable request with NO open engage
                # row must NOT blanket-enable. The removed "legacy compat" branch
                # set autonomy_enabled=true on ALL tiers, granting tiers that never
                # earned autonomy (observed live after force-disengage: snapshot
                # {1:on, 2:off, 3:off} was lost, then this path enabled all 3).
                # There is nothing to restore from, so refuse with 409 and write
                # no state change and no audit event (nothing happened).
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No open kill-switch engage row: there is no per-tier "
                        "snapshot to restore, and blanket-enabling autonomy on all "
                        "tiers would grant tiers autonomy they never earned. "
                        "Re-enable autonomy per-tier through the calibration "
                        "surface (trust_scores) instead."
                    ),
                )
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        detail = str(exc.orig) if exc.orig else str(exc)
        if "uix_kill_switch_history_active" in detail:
            raise HTTPException(
                status_code=409,
                detail="Kill switch is already engaged. Disengage before re-engaging.",
            ) from exc
        raise HTTPException(status_code=500, detail=detail) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/status")
def daemon_status():
    """Read path; no admin key required."""
    db = _get_db()
    try:
        tiers = db.query(TrustScoreRow).order_by(TrustScoreRow.tier).all()
        cooling_count = db.query(SchemaProposalRow).filter(
            SchemaProposalRow.status == "cooling"
        ).count()

        # Check last tick from governance events
        last_event = db.query(GovernanceDecisionEventRow).order_by(
            GovernanceDecisionEventRow.recorded_at.desc()
        ).first()

        # F-027 / ISS-0010: derive engaged-ness from the presence of an open
        # engage row in kill_switch_history, NOT from all-tiers-off. The prior
        # all-tiers-off heuristic kept reporting kill_switch_engaged=true after
        # force-disengage (which closes the row but restores nothing), and
        # false-positives whenever an operator legitimately disables every
        # tier via calibration.
        open_row = db.query(KillSwitchHistoryRow).filter(
            KillSwitchHistoryRow.disengaged_at.is_(None)
        ).first()
        engaged = open_row is not None

        # Chunk 65 (D447): include previous_state from the open kill_switch_history row
        # when kill_switch is engaged, for the CP8 restore-state dialog.
        prev_state = open_row.previous_state if open_row is not None else None

        return DaemonStatusResponse(
            last_tick_at=last_event.recorded_at.isoformat() if last_event else None,
            proposals_in_cooling=cooling_count,
            kill_switch_engaged=engaged,
            tiers=[TierStatus(
                tier=r.tier,
                autonomy_enabled=r.autonomy_enabled,
                regression_detected=r.regression_detected,
            ) for r in tiers],
            previous_state=prev_state,
        )
    finally:
        db.close()


class ForceDisengageRequest(BaseModel):
    """Request body for POST /kill-switch/force-disengage."""
    reason: str = ""


@router.post("/kill-switch/force-disengage")
def force_disengage(body: ForceDisengageRequest, request: Request):
    """Close an orphan engage row left by a uvicorn restart (R2, Chunk 65).

    Admin-key gated. Does NOT restore per-tier trust_scores.autonomy_enabled.
    The engaged_by field stays as originally written — the force-disengage
    actor is recorded only in the GovernanceDecisionEventRow.

    Response semantics (F-0043 / ISS-0050 documentation gap):
    ``restored_state`` is ALWAYS ``null`` in this route's response. Unlike
    the normal disengage path (``PATCH /kill-switch`` with
    ``autonomy_enabled=true``), force-disengage only closes the orphan
    history row — it deliberately does NOT restore the per-tier autonomy
    snapshot. ``restored_state: null`` therefore means "nothing was
    restored; tiers remain as they are", not "restore state unknown".
    Operators who want per-tier state back must re-enable tiers explicitly
    afterwards.
    """
    _require_admin_key(request)
    db = _get_db()
    try:
        open_row = db.query(KillSwitchHistoryRow).filter(
            KillSwitchHistoryRow.disengaged_at.is_(None)
        ).first()
        if open_row is None:
            raise HTTPException(status_code=404, detail="No open engage row found")

        now = datetime.now(UTC)
        open_row.disengaged_at = now
        # restored_state is None — force-disengage does not restore per-tier state
        open_row.restored_state = None

        reason = body.reason.strip() if body.reason else "<force-disengage>"

        gov_event = GovernanceDecisionEventRow(
            decision_type="kill_switch_force_disengaged",
            agent_id="operator",
            outcome="force_disengaged",
            reason=reason,
            recorded_at=now,
        )
        db.add(gov_event)
        db.commit()

        return {
            "id": str(open_row.id),
            "engaged_at": open_row.engaged_at.isoformat() if open_row.engaged_at else None,
            "disengaged_at": now.isoformat(),
            "engaged_by": open_row.engaged_by,
            "reason": open_row.reason,
            "restored_state": None,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/{proposal_id}/confirm")
def confirm_proposal(
    proposal_id: UUID,
    request: Request,
    body: ConfirmRequest | None = None,
):
    """Confirm a COOLING proposal -> APPLIED (D399).

    Body is optional (F-0043 / ISS-0050): ``{"reviewer": ...}`` or
    ``{"reverted_by": ...}`` attributes the governance event to the named
    operator; omitting the body records the generic "operator".
    """
    _require_admin_key(request)
    db = _get_db()
    try:
        proposal = db.query(SchemaProposalRow).filter_by(id=proposal_id).first()
        if not proposal:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if proposal.status != "cooling":
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is not in COOLING status (current: {proposal.status})",
            )

        now = datetime.now(UTC)
        proposal.status = "applied"
        proposal.cooling_outcome = "confirmed"

        # Record governance event. F-0043 / ISS-0050: attribute to the named
        # operator when the optional body supplies one.
        event = GovernanceDecisionEventRow(
            decision_type="cooling_confirmed",
            agent_id=(body.reviewer if body and body.reviewer else "operator"),
            proposal_id=proposal_id,
            tier=proposal.change_tier,
            outcome="confirmed",
            recorded_at=now,
        )
        db.add(event)
        db.commit()

        return {"status": "applied", "cooling_outcome": "confirmed", "proposal_id": str(proposal_id)}
    finally:
        db.close()


@router.post("/{proposal_id}/revert")
def revert_proposal(proposal_id: UUID, body: RevertRequest, request: Request):
    """Revert a COOLING proposal -> REVERTED (D399, D400).

    Execution sequence per spec §7.4:
    1. Load proposal, verify COOLING
    2. kgcl_inverter.invert() — 422 if None
    3. Create inverse proposal row
    4. apply_proposal(db, inverse_proposal.id)
    5. Transition original to REVERTED
    6. Record governance event
    """
    _require_admin_key(request)
    db = _get_db()
    try:
        proposal = db.query(SchemaProposalRow).filter_by(id=proposal_id).first()
        if not proposal:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if proposal.status != "cooling":
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is not in COOLING status (current: {proposal.status})",
            )

        # Step 2: Get inverse KGCL command.
        inverse_cmd = kgcl_invert(proposal.kgcl_command)
        if inverse_cmd is None:
            raise HTTPException(
                status_code=422,
                detail=f"Command is not revertible: {proposal.kgcl_command}",
            )

        now = datetime.now(UTC)

        # Step 3: Create inverse proposal row.
        from uuid import uuid4
        inverse_id = uuid4()
        inverse_row = SchemaProposalRow(
            id=inverse_id,
            proposal_type=proposal.proposal_type,
            change_tier=proposal.change_tier,
            kgcl_command=inverse_cmd,
            proposed_diff={},
            evidence=proposal.evidence,
            raw_confidence=1.0,
            priority="high",
            status="approved",
            current_schema_version_id=proposal.resulting_version_id or proposal.current_schema_version_id,
            reviewer="system:revert",
            reviewed_at=now,
            applied_autonomously=True,
        )
        db.add(inverse_row)
        db.flush()

        # Step 4: Apply inverse proposal.
        try:
            from src.ontology.change_executor import apply_proposal
            result = asyncio.run(apply_proposal(db, inverse_id))
            if not result.success:
                raise HTTPException(
                    status_code=500,
                    detail=f"Inverse proposal execution failed: {result.error}",
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Revert execution error: {e}")

        # Step 5: Transition original to REVERTED.
        proposal.status = "reverted"
        proposal.cooling_outcome = "reverted"
        proposal.reverted_at = now
        proposal.reverted_by = body.reverted_by
        proposal.reverted_proposal_id = inverse_id

        # Step 6: Record governance event.
        event = GovernanceDecisionEventRow(
            decision_type="cooling_reverted",
            agent_id="operator",
            proposal_id=proposal_id,
            tier=proposal.change_tier,
            outcome="reverted",
            reason=body.reason,
            recorded_at=now,
        )
        db.add(event)
        db.commit()

        return {
            "status": "reverted",
            "cooling_outcome": "reverted",
            "proposal_id": str(proposal_id),
            "inverse_proposal_id": str(inverse_id),
        }
    finally:
        db.close()
