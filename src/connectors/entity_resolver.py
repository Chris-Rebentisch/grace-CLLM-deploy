"""Async entity resolver with D410 dual-threshold confidence gate.

Auto-bridges exact and high-confidence embedding matches; queues the rest
to ``entity_resolution_review_queue`` for human disposition.

Does NOT write ``Cross_System_Reference`` edges (v1 scope — ``Bridge_Entity`` only).
Does NOT branch on ``llm`` resolution method (forward-compatible; falls through to
``unresolved`` in current code).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.connectors.models import ConnectorRecord, ResolvedEntity
from src.graph.management_models import GraphNamespace
from src.shared.embeddings import cosine_similarity, embed_texts

logger = structlog.get_logger()


async def resolve_or_create(
    record: ConnectorRecord,
    namespace: GraphNamespace,
    registry,  # CanonicalEntityRegistry — duck-typed to avoid import coupling
    *,
    arcade_client,
    db: Session,
    ollama_base_url: str,
    embedding_model: str = "nomic-embed-text",
    high_confidence_floor: float = 0.92,
) -> ResolvedEntity:
    """Resolve a connector record against the canonical entity registry.

    Resolution flow (D410):
    1. ``registry.resolve(record.name, record.entity_type)`` → (entity, method).
    2. ``exact`` → write ``Bridge_Entity`` edge → outcome ``bridged``.
    3. ``embedding`` → independent cosine check via ``embed_texts`` (D265):
       - >= ``high_confidence_floor`` → bridge
       - < ``high_confidence_floor`` → queue with ``proposed_canonical_grace_id``
    4. ``unresolved`` → queue with ``proposed_canonical_grace_id = NULL``.
    """
    entity, resolution_method = await registry.resolve(
        record.name, record.entity_type
    )

    child_grace_id = str(uuid4())
    now = datetime.now(UTC)

    if resolution_method == "exact" and entity is not None:
        canonical_gid = str(entity.canonical_grace_id)
        await _write_bridge_entity(
            arcade_client,
            grace_id=str(uuid4()),
            canonical_grace_id=canonical_gid,
            child_grace_id=child_grace_id,
            namespace=namespace.database_name,
            resolution_method="exact",
            resolved_at=now,
        )
        _record_metric(record.source_system, "bridged")
        return ResolvedEntity(
            outcome="bridged",
            grace_id=child_grace_id,
            canonical_grace_id=canonical_gid,
        )

    if resolution_method == "embedding" and entity is not None:
        # Independent cosine check using embed_texts (D265, D410)
        query_emb = await embed_texts(
            [record.name],
            base_url=ollama_base_url,
            model=embedding_model,
        )
        if query_emb and entity.embedding_vector:
            query_vec = np.array(query_emb[0])
            candidate_vec = np.array(entity.embedding_vector).reshape(1, -1)
            sim = cosine_similarity(query_vec, candidate_vec)
            score = float(sim[0])

            if score >= high_confidence_floor:
                canonical_gid = str(entity.canonical_grace_id)
                await _write_bridge_entity(
                    arcade_client,
                    grace_id=str(uuid4()),
                    canonical_grace_id=canonical_gid,
                    child_grace_id=child_grace_id,
                    namespace=namespace.database_name,
                    resolution_method="embedding",
                    resolved_at=now,
                )
                _record_metric(record.source_system, "bridged")
                return ResolvedEntity(
                    outcome="bridged",
                    grace_id=child_grace_id,
                    canonical_grace_id=canonical_gid,
                )

            # Below threshold — queue with proposed canonical
            _insert_review_queue(
                db,
                namespace_id=namespace.id,
                source_record_id=record.source_record_id,
                entity_type=record.entity_type,
                record_payload=record.model_dump(mode="json"),
                proposed_canonical_grace_id=str(entity.canonical_grace_id),
                resolution_method="embedding",
            )
            _record_metric(record.source_system, "queued")
            return ResolvedEntity(
                outcome="queued",
                grace_id=child_grace_id,
                canonical_grace_id=None,
            )

    # unresolved (or llm fallthrough) — queue without proposed canonical
    _insert_review_queue(
        db,
        namespace_id=namespace.id,
        source_record_id=record.source_record_id,
        entity_type=record.entity_type,
        record_payload=record.model_dump(mode="json"),
        proposed_canonical_grace_id=None,
        resolution_method=resolution_method,
    )
    _record_metric(record.source_system, "queued")
    return ResolvedEntity(
        outcome="queued",
        grace_id=child_grace_id,
        canonical_grace_id=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _write_bridge_entity(
    arcade_client,
    *,
    grace_id: str,
    canonical_grace_id: str,
    child_grace_id: str,
    namespace: str,
    resolution_method: str,
    resolved_at: datetime,
) -> None:
    """Write a Bridge_Entity edge in ArcadeDB with 6 properties per migration_types.py:132."""
    cypher = (
        "CREATE (b:Bridge_Entity {"
        "grace_id: $grace_id, "
        "canonical_grace_id: $canonical_grace_id, "
        "child_grace_id: $child_grace_id, "
        "namespace: $namespace, "
        "resolution_method: $resolution_method, "
        "resolved_at: $resolved_at"
        "})"
    )
    params = {
        "grace_id": grace_id,
        "canonical_grace_id": canonical_grace_id,
        "child_grace_id": child_grace_id,
        "namespace": namespace,
        "resolution_method": resolution_method,
        "resolved_at": resolved_at.isoformat(),
    }
    await arcade_client.execute_cypher(cypher, params)


def _insert_review_queue(
    db: Session,
    *,
    namespace_id: str,
    source_record_id: str,
    entity_type: str,
    record_payload: dict,
    proposed_canonical_grace_id: str | None,
    resolution_method: str | None,
) -> None:
    """Insert a row into entity_resolution_review_queue."""
    import json

    db.execute(
        text("""
            INSERT INTO entity_resolution_review_queue
                (id, namespace_id, source_record_id, entity_type,
                 record_payload, proposed_canonical_grace_id,
                 resolution_method, status, created_at)
            VALUES
                (gen_random_uuid(), :ns_id, :src_id, :etype,
                 CAST(:payload AS jsonb), :proposed_gid,  -- F-45: ':payload::jsonb' never parsed as a bindparam under SQLAlchemy text()
                 :method, 'pending', NOW())
        """),
        {
            "ns_id": namespace_id,
            "src_id": source_record_id,
            "etype": entity_type,
            "payload": json.dumps(record_payload),
            "proposed_gid": proposed_canonical_grace_id,
            "method": resolution_method,
        },
    )
    db.commit()


def _record_metric(connector_type: str, outcome: str) -> None:
    """Increment grace_connector_sync_records_total. Best-effort."""
    try:
        from src.analytics.metrics import record_connector_sync_record
        record_connector_sync_record(connector_type=connector_type, outcome=outcome)
    except Exception:  # noqa: BLE001
        pass
