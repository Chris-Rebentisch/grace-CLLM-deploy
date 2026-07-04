"""Change Directive repository — three entry points (D292, D294).

The two-writer property for ``status`` is the safety invariant of this
chunk: only :func:`create` (sets ``DRAFT`` on INSERT) and
:func:`transition` may write ``status``. :func:`patch_draft_metadata`
applies an explicit allowlist and refuses any non-draft directive.

Each call to :func:`transition` writes a hash-chained row in
``change_directive_state_transitions`` (pattern from
``src/ontology/schema_store.py:181-194``) and uses
``SELECT ... FOR UPDATE`` to serialize concurrent writers (R9).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import (
    ChangeDirectiveCreateRequest,
    ChangeDirectivePatchBody,
    DirectiveStatus,
)
from .state_machine import is_transition_allowed

logger = structlog.get_logger()


_PATCH_ALLOWLIST: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "affected_segments",
        "extension_metadata",
        "effective_date",
        "target_state_description",
        "realization_horizon",
        "responsible_executive",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def compute_transition_hash(
    row_payload: dict[str, Any], previous_hash: str | None
) -> str:
    """Hash chain pattern from ``src/ontology/schema_store.py:181-194``."""
    data = _canonical_json(row_payload)
    if previous_hash:
        data = data + previous_hash
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(
    session: Session,
    data: ChangeDirectiveCreateRequest,
    authored_by: UUID,
) -> dict[str, Any]:
    """Insert a new directive at ``status='draft'`` (D292 writer #1)."""
    directive_id = uuid4()
    now = _now()
    row: dict[str, Any] = {
        "directive_id": str(directive_id),
        "tier": data.tier,
        "title": data.title,
        "description": data.description,
        "authored_by": str(authored_by),
        "authored_at": now,
        "status": DirectiveStatus.DRAFT.value,
        "status_updated_at": now,
        "visibility": data.visibility,
        "visibility_named_list": (
            json.dumps(data.visibility_named_list)
            if data.visibility_named_list is not None
            else None
        ),
        "visibility_role_cluster": data.visibility_role_cluster,
        "affected_segments": json.dumps(data.affected_segments or []),
        "extension_metadata": (
            json.dumps(data.extension_metadata)
            if data.extension_metadata is not None
            else None
        ),
        "effective_date": data.effective_date,
        "target_state_description": data.target_state_description,
        "realization_horizon": data.realization_horizon,
        "responsible_executive": data.responsible_executive,
    }
    session.execute(
        text(
            """
            INSERT INTO change_directives (
                directive_id, tier, title, description, authored_by,
                authored_at, status, status_updated_at, visibility,
                visibility_named_list, visibility_role_cluster,
                affected_segments, extension_metadata,
                effective_date, target_state_description,
                realization_horizon, responsible_executive
            ) VALUES (
                :directive_id, :tier, :title, :description, :authored_by,
                :authored_at, :status, :status_updated_at, :visibility,
                CAST(:visibility_named_list AS jsonb),
                :visibility_role_cluster,
                CAST(:affected_segments AS jsonb),
                CAST(:extension_metadata AS jsonb),
                :effective_date, :target_state_description,
                :realization_horizon, :responsible_executive
            )
            """
        ),
        row,
    )
    return get_by_id(session, directive_id)


def get_by_id(session: Session, directive_id: UUID) -> dict[str, Any] | None:
    row = session.execute(
        text("SELECT * FROM change_directives WHERE directive_id = :id"),
        {"id": str(directive_id)},
    ).mappings().first()
    return dict(row) if row else None


def list_directives(
    session: Session,
    *,
    cursor: str | None = None,
    limit: int = 25,
    tier: str | None = None,
    status: str | None = None,
    authored_by: UUID | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    where_clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if tier:
        where_clauses.append("tier = :tier")
        params["tier"] = tier
    if status:
        where_clauses.append("status = :status")
        params["status"] = status
    if authored_by is not None:
        where_clauses.append("authored_by = :authored_by")
        params["authored_by"] = str(authored_by)
    if cursor:
        where_clauses.append("authored_at < :cursor")
        params["cursor"] = cursor
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    rows = session.execute(
        text(
            f"SELECT * FROM change_directives{where_sql} "
            "ORDER BY authored_at DESC LIMIT :limit"
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def transition(
    session: Session,
    directive_id: UUID,
    to_state: DirectiveStatus,
    transitioned_by: UUID,
    reason: str | None,
    superseded_by_directive_id: UUID | None = None,
) -> dict[str, Any]:
    """Sole post-INSERT ``status`` writer (D292 writer #2).

    Validates the transition, locks the directive row, writes the new
    status and a hash-chained row in
    ``change_directive_state_transitions`` — all in one transaction.
    """
    locked = session.execute(
        text(
            "SELECT directive_id, status FROM change_directives "
            "WHERE directive_id = :id FOR UPDATE"
        ),
        {"id": str(directive_id)},
    ).mappings().first()
    if not locked:
        raise HTTPException(status_code=404, detail="directive_not_found")
    from_state = DirectiveStatus(locked["status"])
    if not is_transition_allowed(from_state, to_state):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "illegal_transition",
                "from_state": from_state.value,
                "to_state": to_state.value,
            },
        )

    if to_state is DirectiveStatus.SUPERSEDED and superseded_by_directive_id is None:
        raise HTTPException(
            status_code=422,
            detail={"error": "supersession_requires_target_directive_id"},
        )

    now = _now()
    prev = session.execute(
        text(
            "SELECT hash_chain FROM change_directive_state_transitions "
            "WHERE directive_id = :id "
            "ORDER BY transitioned_at DESC LIMIT 1"
        ),
        {"id": str(directive_id)},
    ).scalar()
    transition_id = str(uuid4())
    payload = {
        "id": transition_id,
        "directive_id": str(directive_id),
        "from_state": from_state.value,
        "to_state": to_state.value,
        "superseded_by_directive_id": (
            str(superseded_by_directive_id)
            if superseded_by_directive_id is not None
            else None
        ),
        "transitioned_at": now.isoformat(),
        "transitioned_by": str(transitioned_by),
        "reason": reason,
    }
    new_hash = compute_transition_hash(payload, prev)
    session.execute(
        text(
            """
            INSERT INTO change_directive_state_transitions (
                id, directive_id, from_state, to_state,
                superseded_by_directive_id,
                transitioned_at, transitioned_by, reason,
                hash_chain, prev_transition_hash
            ) VALUES (
                :id, :directive_id, :from_state, :to_state,
                :superseded_by_directive_id,
                :transitioned_at, :transitioned_by, :reason,
                :hash_chain, :prev_hash
            )
            """
        ),
        {**payload, "hash_chain": new_hash, "prev_hash": prev},
    )

    session.execute(
        text(
            "UPDATE change_directives SET status = :status, "
            "status_updated_at = :now, "
            "superseded_by_directive_id = COALESCE(:supersedes, superseded_by_directive_id) "
            "WHERE directive_id = :id"
        ),
        {
            "status": to_state.value,
            "now": now,
            "supersedes": (
                str(superseded_by_directive_id)
                if superseded_by_directive_id is not None
                else None
            ),
            "id": str(directive_id),
        },
    )
    session.commit()
    return get_by_id(session, directive_id)


def patch_draft_metadata(
    session: Session,
    directive_id: UUID,
    patch_body: ChangeDirectivePatchBody,
    requesting_user: UUID,
) -> dict[str, Any]:
    """Draft-only body-metadata edit; never writes ``status`` or
    visibility fields (D292)."""
    locked = session.execute(
        text(
            "SELECT directive_id, status FROM change_directives "
            "WHERE directive_id = :id FOR UPDATE"
        ),
        {"id": str(directive_id)},
    ).mappings().first()
    if not locked:
        raise HTTPException(status_code=404, detail="directive_not_found")
    if locked["status"] != DirectiveStatus.DRAFT.value:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "patch_only_allowed_in_draft",
                "current_status": locked["status"],
            },
        )

    incoming = patch_body.model_dump(exclude_unset=True)
    # Server-side allowlist enforcement (defense-in-depth — Pydantic
    # already rejects via extra="forbid").
    for key in incoming:
        if key not in _PATCH_ALLOWLIST:
            raise HTTPException(
                status_code=422,
                detail={"error": "forbidden_patch_field", "field": key},
            )
    if not incoming:
        return get_by_id(session, directive_id)

    # Build dynamic SET clause restricted to the allowlist.
    set_clauses: list[str] = []
    params: dict[str, Any] = {"id": str(directive_id)}
    for key, value in incoming.items():
        if key == "affected_segments":
            set_clauses.append(f"{key} = CAST(:{key} AS jsonb)")
            params[key] = json.dumps(value)
        elif key == "extension_metadata":
            set_clauses.append(f"{key} = CAST(:{key} AS jsonb)")
            params[key] = json.dumps(value) if value is not None else None
        else:
            set_clauses.append(f"{key} = :{key}")
            params[key] = value

    session.execute(
        text(
            "UPDATE change_directives SET "
            + ", ".join(set_clauses)
            + " WHERE directive_id = :id"
        ),
        params,
    )
    session.commit()
    return get_by_id(session, directive_id)


def list_active_directives_for_snapshots(
    session: Session,
    *,
    directive_id: UUID | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Active directives only, stable order for snapshot CLI (Chunk 39)."""
    params: dict[str, Any] = {}
    where = "status = :active"
    params["active"] = DirectiveStatus.ACTIVE.value
    if directive_id is not None:
        where += " AND directive_id = :did"
        params["did"] = str(directive_id)
    lim_sql = ""
    if limit is not None:
        lim = max(1, int(limit))
        lim_sql = " LIMIT :lim"
        params["lim"] = lim
    rows = session.execute(
        text(
            "SELECT * FROM change_directives WHERE "
            + where
            + " ORDER BY directive_id::text ASC"
            + lim_sql
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def insert_realization_snapshot(
    session: Session,
    *,
    directive_id: UUID,
    snapshot_at: datetime,
    criteria_results: list[dict[str, Any]],
    progress_percentage: float | None,
    velocity: float | None,
    evidence_count_consistent: int | None,
    evidence_count_counter: int | None,
    first_evidence_seen_at: datetime | None,
    last_counter_evidence_seen_at: datetime | None,
    criteria_all_satisfied: bool | None,
) -> dict[str, Any]:
    """Append one snapshot row (append-only table)."""
    row_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO change_directive_realization_snapshots (
                id, directive_id, snapshot_at, criteria_results,
                progress_percentage, velocity,
                evidence_count_consistent, evidence_count_counter,
                first_evidence_seen_at, last_counter_evidence_seen_at,
                criteria_all_satisfied
            ) VALUES (
                :id, :did, :snap_at, CAST(:crit AS jsonb),
                :prog, :vel,
                :ecc, :ecounter,
                :first_seen, :last_counter,
                :all_sat
            )
            """
        ),
        {
            "id": str(row_id),
            "did": str(directive_id),
            "snap_at": snapshot_at,
            "crit": json.dumps(criteria_results),
            "prog": progress_percentage,
            "vel": velocity,
            "ecc": evidence_count_consistent,
            "ecounter": evidence_count_counter,
            "first_seen": first_evidence_seen_at,
            "last_counter": last_counter_evidence_seen_at,
            "all_sat": criteria_all_satisfied,
        },
    )
    session.commit()
    got = session.execute(
        text(
            "SELECT * FROM change_directive_realization_snapshots "
            "WHERE id = :id"
        ),
        {"id": str(row_id)},
    ).mappings().first()
    return dict(got) if got else {}


def get_latest_snapshot(
    session: Session, directive_id: UUID
) -> dict[str, Any] | None:
    row = session.execute(
        text(
            "SELECT * FROM change_directive_realization_snapshots "
            "WHERE directive_id = :id ORDER BY snapshot_at DESC LIMIT 1"
        ),
        {"id": str(directive_id)},
    ).mappings().first()
    return dict(row) if row else None


def list_snapshot_history(
    session: Session,
    directive_id: UUID,
    *,
    limit: int = 30,
    ascending: bool = False,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Historical snapshots; default newest-first for API list."""
    limit = max(1, min(int(limit), 100))
    params: dict[str, Any] = {"id": str(directive_id), "limit": limit}
    since_clause = ""
    if since is not None:
        since_clause = " AND snapshot_at >= :since"
        params["since"] = since
    order = "snapshot_at ASC" if ascending else "snapshot_at DESC"
    rows = session.execute(
        text(
            "SELECT * FROM change_directive_realization_snapshots "
            "WHERE directive_id = :id"
            + since_clause
            + " ORDER BY "
            + order
            + " LIMIT :limit"
        ),
        params,
    ).mappings().all()
    out = [dict(r) for r in rows]
    if ascending:
        return out
    return list(reversed(out))


def compute_velocity_band(
    snapshot_row: dict[str, Any] | None, is_stalled: bool
) -> str | None:
    """D305 band label from persisted velocity + derived stalled flag."""
    if snapshot_row is None:
        return None
    raw_v = snapshot_row.get("velocity")
    if raw_v is None:
        return None
    v = float(raw_v)
    if is_stalled or v == 0.0:
        return "stalled"
    if v >= 0.5:
        return "accelerating"
    if v >= 0.05:
        return "steady"
    if v > 0:
        return "slowing"
    return "slowing"


def compute_is_stalled_for_directive(
    session: Session,
    directive_id: UUID,
    directive_row: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """Route-layer stalled derivation (D303)."""
    from src.change_directives.snapshot_pipeline.config import (
        load_snapshot_config,
    )
    from src.change_directives.snapshot_pipeline import velocity as vel_mod

    cfg = load_snapshot_config()
    obs = now or _now()
    cutoff = obs - timedelta(days=cfg.velocity_window_days)
    hist = list_snapshot_history(
        session,
        directive_id,
        limit=500,
        ascending=True,
        since=cutoff,
    )
    velocities: list[float] = []
    for h in hist:
        vv = h.get("velocity")
        if vv is not None:
            velocities.append(float(vv))
    return vel_mod.compute_is_stalled(
        velocities,
        str(directive_row.get("status") or ""),
        directive_row["status_updated_at"],
        obs,
        cfg,
    )
