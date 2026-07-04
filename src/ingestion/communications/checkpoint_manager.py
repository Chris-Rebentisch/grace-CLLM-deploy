"""Checkpoint manager — load/flush incremental sync cursors (Chunk 57, D424).

Backed by ``ingestion_checkpoints`` table (c57a migration). UPSERT pattern:
``INSERT ... ON CONFLICT (source_id) DO UPDATE``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text

from src.ingestion.models import IngestionCheckpoint

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def load_checkpoint(session: Session, source_id: UUID) -> IngestionCheckpoint | None:
    """Load the checkpoint for a source, or ``None`` if no checkpoint exists."""
    row = session.execute(
        text(
            "SELECT checkpoint_type, checkpoint_value "
            "FROM ingestion_checkpoints WHERE source_id = :sid"
        ),
        {"sid": str(source_id)},
    ).fetchone()

    if row is None:
        return None

    return IngestionCheckpoint(
        checkpoint_type=row[0],
        value=row[1],
    )


def flush_checkpoint(
    session: Session,
    source_id: UUID,
    checkpoint_type: str,
    checkpoint_value: str,
) -> None:
    """Upsert the checkpoint for a source.

    ``INSERT ... ON CONFLICT (source_id) DO UPDATE`` — single-row-per-source.
    Checkpoint flush is outside the per-batch event INSERT transaction (OQ-4).
    """
    session.execute(
        text(
            "INSERT INTO ingestion_checkpoints (source_id, checkpoint_type, checkpoint_value, last_synced_at) "
            "VALUES (:sid, :ctype, :cval, NOW()) "
            "ON CONFLICT (source_id) DO UPDATE "
            "SET checkpoint_type = EXCLUDED.checkpoint_type, "
            "    checkpoint_value = EXCLUDED.checkpoint_value, "
            "    last_synced_at = NOW()"
        ),
        {"sid": str(source_id), "ctype": checkpoint_type, "cval": checkpoint_value},
    )
    session.commit()
