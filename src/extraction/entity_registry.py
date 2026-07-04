"""In-memory batch-scoped entity resolution cache.

Batch-scoped (D87): created at batch start, cleared after batch.
Prevents redundant embedding calls and resolution lookups for
entities with the same normalized name + type within one document.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.extraction.name_utils import DEFAULT_STRIP_SUFFIXES, normalize_entity_name

if TYPE_CHECKING:
    from src.extraction.entity_resolver import EntityResolutionResult


class EntityRegistry:
    """In-memory cache of entity resolution results within a batch.

    Batch-scoped (D87): created at batch start, cleared after batch.
    Prevents redundant embedding calls and resolution lookups for
    entities with the same normalized name + type within one document.
    """

    def __init__(self, strip_suffixes: list[str] | None = None) -> None:
        self._strip_suffixes = strip_suffixes
        self._cache: dict[str, EntityResolutionResult] = {}
        self._embeddings: dict[str, list[float]] = {}
        self._hits = 0
        self._misses = 0

    def cache_key(self, name: str, entity_type: str) -> str:
        """Same normalization as ExtractionPipeline dedup (D85, D87)."""
        norm = normalize_entity_name(name, self._strip_suffixes)
        return f"{norm}::{entity_type}"

    def get(self, name: str, entity_type: str) -> EntityResolutionResult | None:
        """Get cached resolution result. Returns None on miss."""
        key = self.cache_key(name, entity_type)
        result = self._cache.get(key)
        if result is not None:
            self._hits += 1
        else:
            self._misses += 1
        return result

    def put(self, name: str, entity_type: str, result: EntityResolutionResult) -> None:
        """Cache a resolution result."""
        key = self.cache_key(name, entity_type)
        self._cache[key] = result

    def get_embedding(self, text: str) -> list[float] | None:
        """Get cached embedding vector by text key."""
        return self._embeddings.get(text)

    def put_embedding(self, text: str, embedding: list[float]) -> None:
        """Cache an embedding vector by text key."""
        self._embeddings[text] = embedding

    def clear(self) -> None:
        """Clear all caches. Called after batch completes."""
        self._cache.clear()
        self._embeddings.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict:
        """Return cache hit/miss/size statistics."""
        return {
            "cache_size": len(self._cache),
            "embedding_cache_size": len(self._embeddings),
            "hits": self._hits,
            "misses": self._misses,
        }
