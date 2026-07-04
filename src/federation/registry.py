"""Canonical entity resolution registry (Chunk 51, D404).

Async registry for cross-system entity resolution. Uses JSON-array
embeddings in PostgreSQL (no pgvector). Three-tier resolution:
exact name+type → embedding similarity → LLM disambiguation.

Constructor requires ``ollama_base_url`` and ``embedding_model``
for the async ``embed_texts()`` call, following the ``EntityResolver``
pattern at ``src/extraction/entity_resolver.py:305-309``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid4

import numpy as np
import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.federation.models import CanonicalEntity, FederationConfig
from src.shared.embeddings import cosine_similarity, embed_texts

logger = structlog.get_logger()


class CanonicalEntityRegistry:
    """Async registry for canonical entity resolution.

    Args:
        session: SQLAlchemy sync session for Postgres CRUD.
        ollama_base_url: Ollama endpoint for embedding generation.
        embedding_model: Model name for embeddings (default nomic-embed-text).
        similarity_threshold: Cosine similarity threshold for tier-2 resolution.
    """

    def __init__(
        self,
        session: Session,
        ollama_base_url: str,
        embedding_model: str = "nomic-embed-text",
        similarity_threshold: float = 0.85,
    ) -> None:
        self._session = session
        self._ollama_base_url = ollama_base_url
        self._embedding_model = embedding_model
        self._similarity_threshold = similarity_threshold

    async def register_canonical(
        self, entity: CanonicalEntity
    ) -> CanonicalEntity:
        """Register a canonical entity with computed embedding.

        Computes embedding via ``embed_texts`` and stores as JSONB array.
        """
        embedding = await embed_texts(
            [entity.canonical_name],
            base_url=self._ollama_base_url,
            model=self._embedding_model,
        )
        entity_id = uuid4()
        grace_id = entity.canonical_grace_id
        now = datetime.now(UTC)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            partial(
                self._insert_row,
                entity_id,
                grace_id,
                entity.canonical_name,
                entity.canonical_type,
                entity.aliases,
                embedding[0] if embedding else None,
                entity.namespace_source,
                now,
            ),
        )

        entity.id = entity_id
        entity.embedding_vector = embedding[0] if embedding else None
        entity.created_at = now
        entity.updated_at = now
        return entity

    def _insert_row(
        self,
        row_id: UUID,
        canonical_grace_id: UUID,
        canonical_name: str,
        canonical_type: str,
        aliases: dict,
        embedding_vector: list[float] | None,
        namespace_source: str | None,
        now: datetime,
    ) -> None:
        """Sync insert into entity_resolution_registry."""
        import json

        self._session.execute(
            text("""
                INSERT INTO entity_resolution_registry
                    (id, canonical_grace_id, canonical_name, canonical_type,
                     aliases, embedding_vector, namespace_source,
                     created_at, updated_at)
                VALUES
                    (:id, :grace_id, :name, :type,
                     :aliases::jsonb, :embedding::jsonb, :ns,
                     :created, :updated)
            """),
            {
                "id": row_id,
                "grace_id": canonical_grace_id,
                "name": canonical_name,
                "type": canonical_type,
                "aliases": json.dumps(aliases),
                "embedding": json.dumps(embedding_vector) if embedding_vector else None,
                "ns": namespace_source,
                "created": now,
                "updated": now,
            },
        )
        self._session.commit()

    async def resolve(
        self,
        name: str,
        entity_type: str,
        namespace: str | None = None,
    ) -> tuple[CanonicalEntity | None, str]:
        """Resolve a name to a canonical entity using three-tier strategy.

        Returns:
            Tuple of (entity_or_none, resolution_method).
            resolution_method is one of: exact, embedding, llm, unresolved.
        """
        # Tier 1: exact name+type match.
        loop = asyncio.get_running_loop()
        exact = await loop.run_in_executor(
            None, partial(self._exact_match, name, entity_type)
        )
        if exact:
            return exact, "exact"

        # Tier 2: embedding similarity.
        try:
            query_embedding = await embed_texts(
                [name],
                base_url=self._ollama_base_url,
                model=self._embedding_model,
            )
            if query_embedding:
                candidates = await loop.run_in_executor(
                    None, partial(self._get_candidates_with_embeddings, entity_type)
                )
                if candidates:
                    query_vec = np.array(query_embedding[0])
                    embeddings_matrix = np.array(
                        [c["embedding"] for c in candidates]
                    )
                    similarities = cosine_similarity(query_vec, embeddings_matrix)
                    best_idx = int(np.argmax(similarities))
                    if similarities[best_idx] >= self._similarity_threshold:
                        return self._row_to_entity(candidates[best_idx]), "embedding"
        except Exception:
            logger.warning("federation.embedding_resolution_failed", name=name)

        # Tier 3: LLM disambiguation (placeholder — returns unresolved for now).
        # Full LLM tier deferred to future chunk; infrastructure is wired.
        return None, "unresolved"

    def _exact_match(self, name: str, entity_type: str) -> CanonicalEntity | None:
        """Sync exact-match lookup."""
        row = self._session.execute(
            text("""
                SELECT id, canonical_grace_id, canonical_name, canonical_type,
                       aliases, embedding_vector, namespace_source,
                       created_at, updated_at
                FROM entity_resolution_registry
                WHERE canonical_name = :name AND canonical_type = :type
                LIMIT 1
            """),
            {"name": name, "type": entity_type},
        ).mappings().first()
        return self._row_to_entity(dict(row)) if row else None

    def _get_candidates_with_embeddings(
        self, entity_type: str
    ) -> list[dict]:
        """Fetch all registry entries of a type that have embeddings."""
        rows = self._session.execute(
            text("""
                SELECT id, canonical_grace_id, canonical_name, canonical_type,
                       aliases, embedding_vector, namespace_source,
                       created_at, updated_at
                FROM entity_resolution_registry
                WHERE canonical_type = :type
                  AND embedding_vector IS NOT NULL
            """),
            {"type": entity_type},
        ).mappings().all()
        return [
            {**dict(r), "embedding": r["embedding_vector"]}
            for r in rows
        ]

    @staticmethod
    def _row_to_entity(row: dict) -> CanonicalEntity:
        return CanonicalEntity(
            id=row["id"],
            canonical_grace_id=row["canonical_grace_id"],
            canonical_name=row["canonical_name"],
            canonical_type=row["canonical_type"],
            aliases=row.get("aliases") or {},
            embedding_vector=row.get("embedding_vector"),
            namespace_source=row.get("namespace_source"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    async def list_canonicals(
        self, type_filter: str | None = None
    ) -> list[CanonicalEntity]:
        """List canonical entities, optionally filtered by type."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._list_rows, type_filter)
        )

    def _list_rows(self, type_filter: str | None) -> list[CanonicalEntity]:
        if type_filter:
            rows = self._session.execute(
                text("""
                    SELECT id, canonical_grace_id, canonical_name, canonical_type,
                           aliases, embedding_vector, namespace_source,
                           created_at, updated_at
                    FROM entity_resolution_registry
                    WHERE canonical_type = :type
                    ORDER BY created_at DESC
                """),
                {"type": type_filter},
            ).mappings().all()
        else:
            rows = self._session.execute(
                text("""
                    SELECT id, canonical_grace_id, canonical_name, canonical_type,
                           aliases, embedding_vector, namespace_source,
                           created_at, updated_at
                    FROM entity_resolution_registry
                    ORDER BY created_at DESC
                """),
            ).mappings().all()
        return [self._row_to_entity(dict(r)) for r in rows]

    async def get_by_grace_id(self, grace_id: UUID) -> CanonicalEntity | None:
        """Get a canonical entity by its grace_id."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._get_by_grace_id, grace_id)
        )

    def _get_by_grace_id(self, grace_id: UUID) -> CanonicalEntity | None:
        row = self._session.execute(
            text("""
                SELECT id, canonical_grace_id, canonical_name, canonical_type,
                       aliases, embedding_vector, namespace_source,
                       created_at, updated_at
                FROM entity_resolution_registry
                WHERE canonical_grace_id = :gid
                LIMIT 1
            """),
            {"gid": grace_id},
        ).mappings().first()
        return self._row_to_entity(dict(row)) if row else None
