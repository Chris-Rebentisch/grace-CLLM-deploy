"""Execute compiled OpenCypher per EvidenceCriterion (Chunk 39, D302)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from src.change_directives.snapshot_pipeline.config import SnapshotPipelineConfig
from src.change_directives.snapshot_pipeline.payload_hash import compute_payload_hash
from src.graph.arcade_client import ArcadeClient


def _collect_grace_ids(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        gid = obj.get("grace_id")
        if isinstance(gid, str) and gid:
            out.add(gid)
        for v in obj.values():
            _collect_grace_ids(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _collect_grace_ids(x, out)


def _arcade_result_rows(arcade_payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = arcade_payload.get("result") or []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            rows.append(json.loads(json.dumps(item, default=str, sort_keys=True)))
        else:
            rows.append({"_raw": str(item)})
    return rows


@dataclass
class CriterionResult:
    criterion_id: UUID
    satisfied: bool
    measured_value: float | int | None
    query_executed_at: datetime
    result_hash: str
    sample_grace_ids: list[str]
    counter_evidence: dict[str, Any] | None


async def execute_criterion(
    client: ArcadeClient,
    criterion_id: UUID,
    compiled_query: str | None,
    config: SnapshotPipelineConfig,
    *,
    tier: str,
    observation_time: datetime,
) -> CriterionResult:
    """Run compiled OpenCypher (must be non-null for SI); build D302 envelope."""
    executed_at = observation_time
    if not compiled_query:
        rows: list[dict[str, Any]] = []
        sample: list[str] = []
        ce = None
        if tier == "Operational_Adjustment":
            ce = {
                "first_seen_at": None,
                "last_seen_at": None,
                "sample_grace_ids": [],
            }
        return CriterionResult(
            criterion_id=criterion_id,
            satisfied=False,
            measured_value=None,
            query_executed_at=executed_at,
            result_hash=compute_payload_hash(rows),
            sample_grace_ids=sample,
            counter_evidence=ce,
        )

    payload = await client.execute_cypher(compiled_query.strip())
    rows = _arcade_result_rows(payload)
    gid_set: set[str] = set()
    _collect_grace_ids(rows, gid_set)
    sample = sorted(gid_set)[: config.sample_id_cap]
    satisfied = len(rows) > 0
    measured_value: float | int | None = float(len(rows)) if rows else None

    ce = None
    if tier == "Operational_Adjustment":
        if rows:
            iso = executed_at.isoformat()
            ce = {
                "first_seen_at": iso,
                "last_seen_at": iso,
                "sample_grace_ids": list(sample),
            }
        else:
            ce = {
                "first_seen_at": None,
                "last_seen_at": None,
                "sample_grace_ids": [],
            }

    return CriterionResult(
        criterion_id=criterion_id,
        satisfied=satisfied,
        measured_value=measured_value,
        query_executed_at=executed_at,
        result_hash=compute_payload_hash(rows),
        sample_grace_ids=list(sample),
        counter_evidence=ce,
    )