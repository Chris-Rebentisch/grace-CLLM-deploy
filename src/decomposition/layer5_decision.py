"""Layer 5 Structured Interview decision logic (Chunk 41, D320).

Pure functions for:

* :func:`record_layer5_decision` — first-write-only persistence of the
  ``Layer5DecisionPayload`` into ``decomposition_runs.layer5_decision``.
* :func:`trigger_reformulation_path_b` — IDEA2-style single-pass
  reformulation. Inserts a successor ``decomposition_runs`` row via
  Path B (copies Layer 1–3 JSONB + canonical hash from predecessor;
  ``resumed_from_run_id = predecessor``). Single-pass cap enforced
  by counting ``reject_all_reformulate`` decisions in the chain
  ancestors.

The five ``decision_kind`` values (D320):

* ``accepted_segmented`` — operator picked one segmented hypothesis.
* ``accepted_null`` — operator accepted the mandatory null hypothesis.
* ``rerun_finer`` / ``rerun_coarser`` — handled by
  :mod:`src.decomposition.rerun_repository` (NOT this module).
* ``reject_all_reformulate`` — single-pass reformulation; this module
  inserts the Path B successor row.

EC-12 discipline preserved: none of the five ``decision_kind`` values
intersects the D281+D289 8-token forbidden list.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from src.decomposition.config import load_config
from src.decomposition.run_repository import (
    create_run,
    get_run,
    update_layer5_decision,
)
from src.decomposition.segmentation_map_models import Layer5DecisionPayload


VALID_DECISION_KINDS: frozenset[str] = frozenset(
    {
        "accepted_segmented",
        "accepted_null",
        "rerun_finer",
        "rerun_coarser",
        "reject_all_reformulate",
    }
)


class ReformulationCapExceededError(RuntimeError):
    """Lineage already contains the maximum permitted reformulation passes."""


def _count_reformulations_in_chain(
    session, predecessor_run_id: UUID
) -> int:
    """Count ``reject_all_reformulate`` decisions in the ancestor chain.

    Walks ``resumed_from_run_id`` from ``predecessor_run_id`` toward the
    root. Each ancestor whose ``layer5_decision`` JSONB has
    ``decision_kind = 'reject_all_reformulate'`` increments the count.
    The predecessor itself is included if its decision was a
    reformulation (so the next attempt sees the cap correctly).
    """
    sql = text(
        """
        WITH RECURSIVE lineage AS (
            SELECT run_id, resumed_from_run_id, layer5_decision
            FROM decomposition_runs
            WHERE run_id = :start
            UNION ALL
            SELECT r.run_id, r.resumed_from_run_id, r.layer5_decision
            FROM decomposition_runs r
            JOIN lineage l ON r.run_id = l.resumed_from_run_id
        )
        SELECT COUNT(*) FROM lineage
        WHERE layer5_decision IS NOT NULL
          AND layer5_decision->>'decision_kind' = 'reject_all_reformulate'
        """
    )
    return int(session.execute(sql, {"start": predecessor_run_id}).scalar() or 0)


def record_layer5_decision(
    session,
    *,
    run_id: UUID,
    payload: Layer5DecisionPayload,
) -> dict:
    """First-write-only persistence of the Layer 5 decision (D320).

    Returns the updated row. Trigger-enforced first-write-only — a
    second write raises ``check_violation`` (the c41b extension to the
    append-only trigger).
    """
    if payload.decision_kind not in VALID_DECISION_KINDS:
        raise ValueError(
            f"Unknown decision_kind: {payload.decision_kind!r}. "
            f"Valid: {sorted(VALID_DECISION_KINDS)}"
        )
    return update_layer5_decision(session, run_id, payload)


def trigger_reformulation_path_b(
    session,
    *,
    predecessor_run_id: UUID,
    operator_rationale: str,
    decided_by: UUID | None = None,
    cap_override: int | None = None,
) -> dict:
    """IDEA2 single-pass reformulation — Path B successor INSERT.

    Steps:

    1. Verify the predecessor's chain has not already exhausted the
       reformulation cap (``layer5.reformulation_pass_cap`` from
       ``config/decomposition.yaml``; default 1). Raises
       :class:`ReformulationCapExceededError` if exceeded.
    2. Read the predecessor row; copy Layers 1–3 JSONB forward at
       INSERT time (bypasses the trigger, same as Path B in Chunk 40).
    3. INSERT a fresh ``decomposition_runs`` row with
       ``resumed_from_run_id = predecessor`` and status ``running``.
    4. Return the new row dict.

    The operator's rationale is **not** persisted as part of the
    successor row payload here (the orchestrator appends it to the
    Layer 4 prompt context when the successor reaches Layer 4). The
    rationale is recorded in the predecessor's ``layer5_decision``
    JSONB by the route layer before this function fires.
    """
    cap = (
        cap_override
        if cap_override is not None
        else load_config().layer5.reformulation_pass_cap
    )

    # Count reformulations in the existing chain (predecessor + ancestors).
    existing_reformulations = _count_reformulations_in_chain(
        session, predecessor_run_id
    )
    if existing_reformulations >= cap:
        raise ReformulationCapExceededError(
            f"Reformulation cap ({cap}) reached for this lineage "
            f"(already {existing_reformulations})."
        )

    pred = get_run(session, predecessor_run_id)
    if pred is None:
        raise ValueError(f"Predecessor run {predecessor_run_id} not found")

    # Path B copy-forward: Layers 1–3 (Layer 4 will recompute under the
    # reformulated prompt context).
    seed_artifacts = {
        "layer1_summary": pred.get("layer1_summary"),
        "layer2_decision": pred.get("layer2_decision"),
        "layer3_decision": pred.get("layer3_decision"),
    }

    return create_run(
        session,
        archive_root=pred["archive_root"],
        operator=pred.get("operator") if decided_by is None else decided_by,
        canonical_hash=pred["archive_root_canonical_hash"],
        resumed_from_run_id=predecessor_run_id,
        seed_layer_artifacts=seed_artifacts,
    )
