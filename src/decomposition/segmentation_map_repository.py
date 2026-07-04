"""Repository for ``segmentation_maps`` (Chunk 41, D326).

Hash-chained CRUD for the Layer 7 Segmentation Map governance table.

* ``create_map`` — INSERT with SHA-256 canonical-JSON ``payload_hash``;
  ``previous_hash`` from the most recent map for the same
  ``decomposition_run_id`` (NULL for the first map).
* ``latest_map_for_run`` — most-recent map for a run (or None).
* ``chain_for_run`` — full hash chain for a run, oldest-first.
* ``get_map_by_id`` — by surrogate UUID PK.
* ``_compute_payload_hash`` — exposed for testing the canonical-JSON
  contract.

Hash chain is computed over canonical-JSON of the Pydantic
``model_dump(mode='json')`` output (sorted keys, no whitespace).
This sidesteps any ``pydantic_yaml`` round-trip non-determinism
(Risk R8).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from src.decomposition.segmentation_map_models import SegmentationMap


def _compute_payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON of the payload dict."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if isinstance(row, dict):
        return row
    return dict(row)


def create_map(
    session,
    *,
    sm: SegmentationMap,
    created_by: UUID | None = None,
) -> dict:
    """INSERT a new ``segmentation_maps`` row with hash chaining.

    ``previous_hash`` is the latest map's ``payload_hash`` for the
    same ``decomposition_run_id``; NULL on first write.
    """
    payload_dict = sm.model_dump(mode="json")
    payload_hash = _compute_payload_hash(payload_dict)

    prev_row = session.execute(
        text(
            """
            SELECT payload_hash
            FROM segmentation_maps
            WHERE decomposition_run_id = :run
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"run": sm.decomposition_run_id},
    ).one_or_none()
    previous_hash = prev_row[0] if prev_row is not None else None

    sql = text(
        """
        INSERT INTO segmentation_maps (
            decomposition_run_id,
            schema_version,
            payload_hash,
            previous_hash,
            created_by,
            payload,
            null_hypothesis_accepted
        ) VALUES (
            :run,
            :schema_version,
            :payload_hash,
            :previous_hash,
            :created_by,
            CAST(:payload AS JSONB),
            :nha
        )
        RETURNING segmentation_map_id, decomposition_run_id, schema_version,
                  payload_hash, previous_hash, created_at, created_by,
                  null_hypothesis_accepted
        """
    )
    row = session.execute(
        sql,
        {
            "run": sm.decomposition_run_id,
            "schema_version": sm.schema_version,
            "payload_hash": payload_hash,
            "previous_hash": previous_hash,
            "created_by": created_by,
            "payload": json.dumps(payload_dict, default=str),
            "nha": sm.null_hypothesis_accepted,
        },
    ).one()
    return _row_to_dict(row)


def latest_map_for_run(session, run_id: UUID) -> dict | None:
    sql = text(
        """
        SELECT segmentation_map_id, decomposition_run_id, schema_version,
               payload_hash, previous_hash, created_at, created_by,
               payload, null_hypothesis_accepted
        FROM segmentation_maps
        WHERE decomposition_run_id = :r
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    row = session.execute(sql, {"r": run_id}).one_or_none()
    return _row_to_dict(row) if row is not None else None


def chain_for_run(session, run_id: UUID) -> list[dict]:
    """Return the hash chain oldest-first."""
    sql = text(
        """
        SELECT segmentation_map_id, decomposition_run_id, schema_version,
               payload_hash, previous_hash, created_at, created_by,
               payload, null_hypothesis_accepted
        FROM segmentation_maps
        WHERE decomposition_run_id = :r
        ORDER BY created_at ASC
        """
    )
    rows = session.execute(sql, {"r": run_id}).all()
    return [_row_to_dict(r) for r in rows]


def get_map_by_id(session, map_id: UUID) -> dict | None:
    sql = text(
        """
        SELECT segmentation_map_id, decomposition_run_id, schema_version,
               payload_hash, previous_hash, created_at, created_by,
               payload, null_hypothesis_accepted
        FROM segmentation_maps
        WHERE segmentation_map_id = :id
        """
    )
    row = session.execute(sql, {"id": map_id}).one_or_none()
    return _row_to_dict(row) if row is not None else None
