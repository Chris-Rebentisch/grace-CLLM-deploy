"""Repository for ``permission_matrices`` (Chunk 42, D331).

Hash-chained CRUD for the operator-ratified PermissionMatrix
governance table. Mirrors the D326 ``segmentation_maps`` pattern.

* ``insert_matrix`` — sole writer; INSERT with SHA-256 canonical-JSON
  ``payload_hash`` (server-computed; client-supplied hashes ignored).
  ``previous_hash`` from the most-recent matrix (NULL for first row).
  Concurrent inserts serialize via ``SELECT ... FOR UPDATE`` on the
  prior chain head.
* ``get_active_matrix`` — most-recent matrix (or None).
* ``get_matrix_versions`` — chain ordered newest-first, paginated.
* ``get_matrix_by_id`` — by surrogate UUID PK.
* ``verify_chain`` — returns ``(valid, chain_length, broken_at)``.
* ``_compute_payload_hash`` — exposed for testing the canonical-JSON
  contract.

Hash chain is computed over canonical JSON of the Pydantic
``model_dump(mode='json')`` output (sorted keys, no whitespace).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from src.permissions.models import PermissionMatrix


def _compute_payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON of the payload dict.

    Server-side computation per D331 — client-supplied hashes are
    ignored. ``sort_keys=True`` + ``separators=(",",":")`` gives the
    canonical form.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if isinstance(row, dict):
        return row
    return dict(row)


def insert_matrix(
    session,
    *,
    matrix: PermissionMatrix,
    created_by: str | None = None,
    version_label: str | None = None,
) -> dict:
    """INSERT a new ``permission_matrices`` row with hash chaining.

    Concurrent insert protection: the prior chain head is locked via
    ``SELECT ... FOR UPDATE`` so two parallel ratifications cannot
    write rows with the same ``previous_hash``.
    """
    payload_dict = matrix.model_dump(mode="json")
    payload_hash = _compute_payload_hash(payload_dict)

    prev_row = session.execute(
        text(
            """
            SELECT payload_hash
            FROM permission_matrices
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE
            """
        )
    ).one_or_none()
    previous_hash = prev_row[0] if prev_row is not None else None

    sql = text(
        """
        INSERT INTO permission_matrices (
            payload,
            payload_hash,
            previous_hash,
            created_by,
            version_label
        ) VALUES (
            CAST(:payload AS JSONB),
            :payload_hash,
            :previous_hash,
            :created_by,
            :version_label
        )
        RETURNING permission_matrix_id, payload_hash, previous_hash,
                  created_at, created_by, version_label
        """
    )
    row = session.execute(
        sql,
        {
            "payload": json.dumps(payload_dict, default=str),
            "payload_hash": payload_hash,
            "previous_hash": previous_hash,
            "created_by": created_by,
            "version_label": version_label,
        },
    ).one()
    return _row_to_dict(row)


def get_active_matrix(session) -> dict | None:
    sql = text(
        """
        SELECT permission_matrix_id, payload, payload_hash, previous_hash,
               created_at, created_by, version_label
        FROM permission_matrices
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    row = session.execute(sql).one_or_none()
    return _row_to_dict(row) if row is not None else None


def get_matrix_versions(
    session,
    *,
    limit: int = 25,
    offset: int = 0,
) -> list[dict]:
    sql = text(
        """
        SELECT permission_matrix_id, payload, payload_hash, previous_hash,
               created_at, created_by, version_label
        FROM permission_matrices
        ORDER BY created_at DESC
        LIMIT :lim OFFSET :off
        """
    )
    rows = session.execute(sql, {"lim": limit, "off": offset}).all()
    return [_row_to_dict(r) for r in rows]


def get_matrix_by_id(session, matrix_id: UUID) -> dict | None:
    sql = text(
        """
        SELECT permission_matrix_id, payload, payload_hash, previous_hash,
               created_at, created_by, version_label
        FROM permission_matrices
        WHERE permission_matrix_id = :id
        """
    )
    row = session.execute(sql, {"id": matrix_id}).one_or_none()
    return _row_to_dict(row) if row is not None else None


def verify_chain(session) -> dict:
    """Walk the chain newest → oldest, asserting each row's
    ``previous_hash`` matches the next row's ``payload_hash``."""
    sql = text(
        """
        SELECT payload_hash, previous_hash
        FROM permission_matrices
        ORDER BY created_at ASC
        """
    )
    rows = session.execute(sql).all()
    if not rows:
        return {"valid": True, "chain_length": 0, "broken_at": None}

    expected_prev: str | None = None
    for r in rows:
        prev = r[1]
        if prev != expected_prev:
            return {
                "valid": False,
                "chain_length": len(rows),
                "broken_at": r[0],
            }
        expected_prev = r[0]
    return {"valid": True, "chain_length": len(rows), "broken_at": None}
