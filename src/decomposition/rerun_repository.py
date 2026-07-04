"""Repository for ±1.5× resolution re-run flow (Chunk 41, D321).

A re-run is a successor ``decomposition_runs`` row that:

* Inherits Layer 1 + Layer 2 JSONB from a predecessor (D277 §3.4 —
  re-run scope is Layer 3 + Layer 4 only).
* Sets ``resumed_from_run_id`` to the predecessor (chain walk).
* Verifies the archive root canonical hash still matches the
  predecessor; raises :class:`ArchiveDriftError` on mismatch.
* Enforces a hard cap of ``5`` re-runs per ``archive_root_canonical_hash``
  lineage; raises :class:`RerunCapExceededError` on the 6th attempt.

``direction: Literal['finer', 'coarser']`` selects the ±1.5×
``resolution_parameter`` adjustment. Layer 3 + Layer 4 will recompute
on the successor row.

The chain walk stays purely SQL — no recursion in Python — so it
remains O(depth) on the index ``decomposition_runs(resumed_from_run_id)``.

Trigger discipline: the predecessor row is **never modified** by this
function. New row INSERT goes through :func:`run_repository.create_run`
which carries Layer 1/2 forward at INSERT time (D310 trigger only
guards UPDATE; INSERT bypasses it).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import text

from src.decomposition.run_repository import (
    ArchiveDriftError,
    _canonical_hash,
    create_run,
    get_run,
)


# Hard cap: 5 re-runs per lineage. The 6th attempt raises.
RERUN_HARD_CAP: int = 5


class RerunCapExceededError(RuntimeError):
    """Raised when a lineage already has the maximum permitted re-runs."""


def _count_lineage_reruns(session, predecessor_run_id: UUID) -> int:
    """Walk ``resumed_from_run_id`` ancestors; return lineage depth.

    The predecessor itself counts as 0; each ancestor adds 1. We use a
    single recursive CTE for portability + index efficiency.
    """
    sql = text(
        """
        WITH RECURSIVE lineage AS (
            SELECT run_id, resumed_from_run_id, 0 AS depth
            FROM decomposition_runs
            WHERE run_id = :start
            UNION ALL
            SELECT r.run_id, r.resumed_from_run_id, l.depth + 1
            FROM decomposition_runs r
            JOIN lineage l ON r.run_id = l.resumed_from_run_id
            WHERE l.resumed_from_run_id IS NOT NULL
        )
        SELECT COALESCE(MAX(depth), 0) FROM lineage
        """
    )
    return int(session.execute(sql, {"start": predecessor_run_id}).scalar() or 0)


def lineage_depth(session, predecessor_run_id: UUID) -> int:
    """Public alias for chain-walk depth (oldest ancestor distance)."""
    return _count_lineage_reruns(session, predecessor_run_id)


def create_rerun_run(
    session,
    *,
    predecessor_run_id: UUID,
    direction: Literal["finer", "coarser"],
) -> dict:
    """Create a re-run successor row with cap + drift enforcement.

    Returns the new row dict. Raises:

    * :class:`ValueError` — predecessor not found or invalid direction.
    * :class:`ArchiveDriftError` — recomputed archive hash differs.
    * :class:`RerunCapExceededError` — lineage already at hard cap.
    """
    if direction not in ("finer", "coarser"):
        raise ValueError(
            f"direction must be 'finer' or 'coarser' (got: {direction!r})"
        )

    pred = get_run(session, predecessor_run_id)
    if pred is None:
        raise ValueError(f"Predecessor run {predecessor_run_id} not found")

    # Cap enforcement: the predecessor's lineage depth + 1 (the new row)
    # must not exceed the hard cap.
    current_depth = _count_lineage_reruns(session, predecessor_run_id)
    if current_depth + 1 > RERUN_HARD_CAP:
        raise RerunCapExceededError(
            f"Re-run lineage hard cap of {RERUN_HARD_CAP} reached "
            f"(current depth: {current_depth}). Accept a hypothesis or "
            "accept the null hypothesis to exit."
        )

    fresh_hash = _canonical_hash(pred["archive_root"])
    if fresh_hash != pred["archive_root_canonical_hash"]:
        raise ArchiveDriftError(
            f"Archive root hash mismatch for {pred['archive_root']}: "
            f"recomputed {fresh_hash} != stored "
            f"{pred['archive_root_canonical_hash']}"
        )

    # Copy Layer 1 + Layer 2 forward (D277 §3.4: re-run scope is L3+L4 only).
    seed_artifacts = {
        "layer1_summary": pred.get("layer1_summary"),
        "layer2_decision": pred.get("layer2_decision"),
        # Layer 3 NOT copied — it recomputes under the new resolution.
    }

    new_row = create_run(
        session,
        archive_root=pred["archive_root"],
        operator=pred.get("operator"),
        canonical_hash=fresh_hash,
        resumed_from_run_id=predecessor_run_id,
        seed_layer_artifacts=seed_artifacts,
    )
    # Surface the lineage_depth + direction for telemetry callers.
    new_row["lineage_depth"] = current_depth + 1
    new_row["direction"] = direction
    return new_row
