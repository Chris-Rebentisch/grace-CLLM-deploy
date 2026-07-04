"""Repository for ``decomposition_runs`` (Chunk 40, D310).

Two-phase persistence pattern:

1. ``create_run()`` INSERTs a row with ``status='running'`` and NULL
   JSONB columns. ``archive_root_canonical_hash`` is the SHA-256 of
   ``Path(archive_root).resolve()``.
2. The orchestrator accumulates Layer 1–4 artifacts in memory.
3. ``finalize_run()`` UPDATEs ``status``, ``completed_at``, and any
   non-None JSONB cells in a single statement. The append-only
   trigger permits JSONB updates only when the OLD value IS NULL —
   first-write-only semantics that preserve D310's intent.

Path B resume (``create_resume_run()``) creates a successor row that
copies Layers 1–3 from a paused run and recomputes the archive hash;
mismatch raises ``ArchiveDriftError``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text


class ArchiveDriftError(RuntimeError):
    """Raised when a resume archive root no longer hashes to the original."""


# ---------- Helpers ----------


def _canonical_hash(archive_root: str | Path) -> str:
    """SHA-256 of the resolved absolute path (D310)."""
    resolved = str(Path(archive_root).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()


def _to_json(value: Any) -> str | None:
    """Coerce a Pydantic / dict / None layer artifact to a JSON string."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(mode="json"))
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, str):
        # Trust callers that already serialized.
        return value
    return json.dumps(value)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if isinstance(row, dict):
        return row
    return dict(row)


# ---------- CRUD ----------


def create_run(
    session,
    archive_root: str,
    operator: UUID | None,
    *,
    canonical_hash: str | None = None,
    resumed_from_run_id: UUID | None = None,
    seed_layer_artifacts: dict | None = None,
) -> dict:
    """INSERT a new ``decomposition_runs`` row (status='running').

    ``seed_layer_artifacts`` is used by the resume path to copy Layers
    1–3 in the same INSERT (avoiding a follow-up UPDATE that would
    require the first-write-only trigger semantics — INSERTs bypass
    the trigger entirely).
    """
    op_uuid = operator if operator is not None else None
    h = canonical_hash or _canonical_hash(archive_root)

    seed = seed_layer_artifacts or {}
    layer1 = _to_json(seed.get("layer1_summary"))
    layer2 = _to_json(seed.get("layer2_decision"))
    layer3 = _to_json(seed.get("layer3_decision"))

    sql = text(
        """
        INSERT INTO decomposition_runs (
            archive_root,
            archive_root_canonical_hash,
            status,
            operator,
            resumed_from_run_id,
            layer1_summary,
            layer2_decision,
            layer3_decision
        ) VALUES (
            :archive_root,
            :h,
            'running',
            :operator,
            :resumed_from_run_id,
            CAST(:layer1 AS JSONB),
            CAST(:layer2 AS JSONB),
            CAST(:layer3 AS JSONB)
        )
        RETURNING run_id, archive_root, archive_root_canonical_hash,
                  started_at, status, operator, resumed_from_run_id
        """
    )
    row = session.execute(
        sql,
        {
            "archive_root": archive_root,
            "h": h,
            "operator": op_uuid,
            "resumed_from_run_id": resumed_from_run_id,
            "layer1": layer1,
            "layer2": layer2,
            "layer3": layer3,
        },
    ).one()
    return _row_to_dict(row)


def finalize_run(
    session,
    run_id: UUID,
    status: str,
    completed_at: datetime | None,
    layer_artifacts: dict | None = None,
) -> dict:
    """UPDATE ``status`` + ``completed_at`` + any non-None JSONB cells.

    Single statement; the append-only trigger enforces first-write-only
    semantics on JSONB columns. ``layer_artifacts`` keys are the
    column names; values may be Pydantic models, dicts, or ``None``.
    Only non-None entries become column updates (NULL JSONB is left
    NULL).
    """
    layer_artifacts = layer_artifacts or {}
    completed = completed_at or datetime.now(timezone.utc)

    sets = ["status = :status", "completed_at = :completed_at"]
    params: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "completed_at": completed,
    }
    for col in (
        "layer1_summary",
        "layer2_decision",
        "layer3_decision",
        "layer4_hypotheses",
    ):
        value = layer_artifacts.get(col)
        if value is None:
            continue
        sets.append(f"{col} = CAST(:{col} AS JSONB)")
        params[col] = _to_json(value)

    # total_documents is an immutable column under the trigger when
    # OLD is non-NULL; we set it on first write only.
    if layer_artifacts.get("total_documents") is not None:
        sets.append("total_documents = :total_documents")
        params["total_documents"] = layer_artifacts["total_documents"]

    sql = text(
        f"""
        UPDATE decomposition_runs
        SET {", ".join(sets)}
        WHERE run_id = :run_id
        RETURNING run_id, status, completed_at,
                  layer1_summary, layer2_decision,
                  layer3_decision, layer4_hypotheses, total_documents
        """
    )
    row = session.execute(sql, params).one()
    return _row_to_dict(row)


def get_run(session, run_id: UUID) -> dict | None:
    sql = text(
        """
        SELECT run_id, archive_root, archive_root_canonical_hash,
               started_at, completed_at, status, total_documents,
               operator, resumed_from_run_id,
               layer1_summary, layer2_decision,
               layer3_decision, layer4_hypotheses,
               layer5_decision, layer6_validation,
               created_at
        FROM decomposition_runs
        WHERE run_id = :run_id
        """
    )
    row = session.execute(sql, {"run_id": run_id}).one_or_none()
    return _row_to_dict(row) if row is not None else None


def latest_completed_run_for_archive_hash(
    session, canonical_hash: str
) -> dict | None:
    """Use the ``(hash, status, started_at DESC)`` index."""
    sql = text(
        """
        SELECT run_id, archive_root, archive_root_canonical_hash,
               started_at, completed_at, status,
               layer1_summary, layer2_decision,
               layer3_decision, layer4_hypotheses
        FROM decomposition_runs
        WHERE archive_root_canonical_hash = :h
          AND status = 'completed'
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    row = session.execute(sql, {"h": canonical_hash}).one_or_none()
    return _row_to_dict(row) if row is not None else None


def update_layer5_decision(
    session,
    run_id: UUID,
    payload: Any,
) -> dict:
    """First-write-only UPDATE of ``layer5_decision`` JSONB (Chunk 41, D327).

    Relies on the c41b trigger extension: NULL → value is allowed; a
    second write raises ``check_violation``. ``payload`` may be a
    Pydantic model, a dict, or a JSON string.
    """
    sql = text(
        """
        UPDATE decomposition_runs
        SET layer5_decision = CAST(:payload AS JSONB)
        WHERE run_id = :run_id
        RETURNING run_id, status, layer5_decision
        """
    )
    row = session.execute(
        sql,
        {"run_id": run_id, "payload": _to_json(payload)},
    ).one()
    return _row_to_dict(row)


def update_layer6_validation(
    session,
    run_id: UUID,
    payload: Any,
) -> dict:
    """First-write-only UPDATE of ``layer6_validation`` JSONB (Chunk 41, D327).

    Same trigger semantics as :func:`update_layer5_decision`.
    """
    sql = text(
        """
        UPDATE decomposition_runs
        SET layer6_validation = CAST(:payload AS JSONB)
        WHERE run_id = :run_id
        RETURNING run_id, status, layer6_validation
        """
    )
    row = session.execute(
        sql,
        {"run_id": run_id, "payload": _to_json(payload)},
    ).one()
    return _row_to_dict(row)


# Status transition helpers for the Layer 5–7 lifecycle (Chunk 41, D327).
# These are thin wrappers over UPDATE; the c41b CHECK constraint
# enforces the 7-value enumeration. The append-only trigger permits
# status transitions (``status`` is not on the immutable list).
_VALID_STATUS_TRANSITIONS: frozenset[str] = frozenset(
    {
        "running",
        "completed",
        "failed",
        "paused_pre_layer4",
        "paused_pre_layer5",
        "paused_pre_layer6",
        "paused_pre_layer7",
    }
)


def transition_status(session, run_id: UUID, new_status: str) -> dict:
    """UPDATE ``status`` to ``new_status``. Validates against c41b CHECK.

    Raises ``ValueError`` if ``new_status`` is not in the seven-value
    enumeration. The Postgres CHECK is the second line of defense.
    """
    if new_status not in _VALID_STATUS_TRANSITIONS:
        raise ValueError(
            f"Unknown decomposition_runs.status: {new_status!r}. "
            f"Valid: {sorted(_VALID_STATUS_TRANSITIONS)}"
        )
    sql = text(
        """
        UPDATE decomposition_runs
        SET status = :status
        WHERE run_id = :run_id
        RETURNING run_id, status
        """
    )
    row = session.execute(
        sql, {"run_id": run_id, "status": new_status}
    ).one()
    return _row_to_dict(row)


def create_resume_run(session, paused_run_id: UUID) -> dict:
    """Path B resume per D310.

    Reads the paused row, recomputes ``archive_root_canonical_hash``,
    raises ``ArchiveDriftError`` on mismatch, then INSERTs a successor
    row carrying Layers 1–3 forward. The successor row's
    ``resumed_from_run_id`` points to the paused row.
    """
    paused = get_run(session, paused_run_id)
    if paused is None:
        raise ValueError(f"Run {paused_run_id} not found")
    if paused["status"] != "paused_pre_layer4":
        raise ValueError(
            f"Run {paused_run_id} is not paused_pre_layer4 "
            f"(actual status: {paused['status']})"
        )

    fresh_hash = _canonical_hash(paused["archive_root"])
    if fresh_hash != paused["archive_root_canonical_hash"]:
        raise ArchiveDriftError(
            f"Archive root hash mismatch for {paused['archive_root']}: "
            f"recomputed {fresh_hash} != stored "
            f"{paused['archive_root_canonical_hash']}"
        )

    return create_run(
        session,
        archive_root=paused["archive_root"],
        operator=paused.get("operator"),
        canonical_hash=fresh_hash,
        resumed_from_run_id=paused_run_id,
        seed_layer_artifacts={
            "layer1_summary": paused.get("layer1_summary"),
            "layer2_decision": paused.get("layer2_decision"),
            "layer3_decision": paused.get("layer3_decision"),
        },
    )
