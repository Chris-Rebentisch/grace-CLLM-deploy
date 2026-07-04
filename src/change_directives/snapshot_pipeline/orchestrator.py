"""Snapshot pipeline orchestrator (Chunk 39, D299–D303)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.change_directives.snapshot_pipeline.aggregations import (
    compute_criteria_all_satisfied,
    compute_progress_percentage,
)
from src.change_directives.snapshot_pipeline.config import SnapshotPipelineConfig
from src.change_directives.snapshot_pipeline.evidence_executor import (
    CriterionResult,
    execute_criterion,
)
from src.change_directives.snapshot_pipeline.velocity import compute_velocity
from src.change_directives import repository
from src.graph.arcade_client import ArcadeClient

logger = structlog.get_logger()


def _load_criteria(session: Session, directive_id: UUID) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT * FROM change_directive_evidence_criteria "
            "WHERE directive_id = :id ORDER BY created_at ASC"
        ),
        {"id": str(directive_id)},
    ).mappings().all()
    return [dict(r) for r in rows]


def _result_to_json(cr: CriterionResult, tier: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "criterion_id": str(cr.criterion_id),
        "satisfied": cr.satisfied,
        "measured_value": cr.measured_value,
        "query_executed_at": cr.query_executed_at.isoformat(),
        "result_hash": cr.result_hash,
        "sample_grace_ids": cr.sample_grace_ids,
    }
    if tier == "Operational_Adjustment" and cr.counter_evidence is not None:
        out["counter_evidence"] = cr.counter_evidence
    return out


async def run_snapshots(
    session: Session,
    arcade: ArcadeClient,
    config: SnapshotPipelineConfig,
    observation_time: datetime,
    *,
    directive_id: UUID | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> None:
    """Process active directives; persist one snapshot row each (unless dry-run)."""
    directives = repository.list_active_directives_for_snapshots(
        session,
        directive_id=directive_id,
        limit=limit,
    )
    for drow in directives:
        did = UUID(str(drow["directive_id"]))
        tier = str(drow["tier"])
        try:
            criteria = _load_criteria(session, did)
            results: list[CriterionResult] = []
            for c in criteria:
                cid = UUID(str(c["criterion_id"]))
                cq = c.get("compiled_query")
                if isinstance(cq, str):
                    cq = cq.strip() or None
                cr = await execute_criterion(
                    arcade,
                    cid,
                    cq,
                    config,
                    tier=tier,
                    observation_time=observation_time,
                )
                results.append(cr)

            criteria_json = [_result_to_json(r, tier) for r in results]

            progress: float | None
            if tier == "Operational_Adjustment" and not criteria_json:
                progress = None
            else:
                progress = compute_progress_percentage(criteria_json)

            all_sat = compute_criteria_all_satisfied(criteria_json, tier)

            vel: float | None = None
            ecc: int | None = None
            ecounter: int | None = None
            first_seen: datetime | None = None
            last_counter: datetime | None = None

            if tier == "Strategic_Initiative":
                ecc = sum(1 for r in results if r.satisfied)
            elif tier == "Operational_Adjustment":
                total_samples = 0
                exec_times: list[datetime] = []
                for r in results:
                    if r.satisfied:
                        exec_times.append(r.query_executed_at)
                        ce = r.counter_evidence or {}
                        ids = ce.get("sample_grace_ids") or []
                        total_samples += len(ids)
                ecounter = total_samples if total_samples else None
                if exec_times:
                    first_seen = min(exec_times)
                    last_counter = max(exec_times)

            prev = repository.get_latest_snapshot(session, did)
            if progress is not None and prev is not None:
                prev_p = prev.get("progress_percentage")
                prev_at = prev["snapshot_at"]
                if prev_p is not None:
                    vel = compute_velocity(
                        float(prev_p),
                        float(progress),
                        prev_at,
                        observation_time,
                    )

            if dry_run:
                logger.info(
                    "snapshot_run_completed",
                    directive_id=str(did),
                    dry_run=True,
                    tier=tier,
                )
                continue

            repository.insert_realization_snapshot(
                session,
                directive_id=did,
                snapshot_at=observation_time,
                criteria_results=criteria_json,
                progress_percentage=progress,
                velocity=vel,
                evidence_count_consistent=ecc,
                evidence_count_counter=ecounter,
                first_evidence_seen_at=first_seen,
                last_counter_evidence_seen_at=last_counter,
                criteria_all_satisfied=all_sat,
            )
            logger.info(
                "snapshot_run_completed",
                directive_id=str(did),
                dry_run=False,
                tier=tier,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "snapshot_run_failed",
                directive_id=str(did),
                error=str(exc),
            )
