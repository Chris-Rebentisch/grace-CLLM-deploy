"""Canonical home for embedding helpers (D265 Strangler Fig).

Chunk 35a CP2 (cutover): the function bodies live here after migration
from ``src/retrieval/semantic_strategy.py:14-41``. ``semantic_strategy``
retains a thin re-export shim for backward compatibility with retrieval-
internal callers.

Direction of imports is one-way: ``semantic_strategy`` imports from
``src.shared.embeddings``, never the reverse. This avoids circular
imports.

The CF3 retrieval lock (``scripts/check-retrieval-unchanged.sh``) gains
a scoped allowlist for the ``semantic_strategy.py`` shim only.
"""

from __future__ import annotations

import os

import httpx
import numpy as np

__all__ = ["cosine_similarity", "embed_texts"]

# Default HTTP timeout for the Ollama /api/embed call. Overridable per call
# via the `timeout` parameter, or globally via GRACE_EMBED_TIMEOUT_SECONDS.
_DEFAULT_EMBED_TIMEOUT_SECONDS = 120.0


def _default_embed_timeout() -> float:
    """Resolve the default embed timeout (env override, else 120s)."""
    raw = os.environ.get("GRACE_EMBED_TIMEOUT_SECONDS")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_EMBED_TIMEOUT_SECONDS


# Phase-5 fix: nomic-embed-text returns HTTP 400 "input length exceeds the
# context length" on items above its 8K-token window. Defensively truncate
# per-item at the call site so every caller (corpus builder, query embedder,
# entity-resolver) is robust to upstream text bloat. 6000 chars leaves
# headroom even for token-dense inputs.
_EMBED_MAX_CHARS_PER_ITEM = 6000


async def embed_texts(
    texts: list[str],
    base_url: str,
    model: str = "nomic-embed-text",
    timeout: float | None = None,
) -> list[list[float]]:
    """Call Ollama /api/embed endpoint. Returns embedding vectors.

    ``timeout`` is the HTTP timeout in seconds; defaults to 120 (or the
    GRACE_EMBED_TIMEOUT_SECONDS env var when set).
    """
    if timeout is None:
        timeout = _default_embed_timeout()
    # F-006 (validation-run ledger, 2026-07-02): the local Ollama 0.24.0
    # nomic-embed-text build masks capitalized/titlecase tokens — every
    # proper-name-only input ("Edward Whitfield", "John Smith") returns the
    # IDENTICAL vector (cosine 1.0), which false-merged 21 distinct entities
    # during import and poisons every ANN index built from names. Lowercased
    # input restores full discrimination (verified: same pairs drop to
    # 0.28-0.49). Lowercasing is similarity-preserving for every caller
    # (dedup, coverage, retrieval), so normalize here at the single choke
    # point rather than per caller. Remove once the Ollama regression is
    # fixed upstream.
    truncated = [
        (t[:_EMBED_MAX_CHARS_PER_ITEM].lower() if isinstance(t, str) else "")
        for t in texts
    ]
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/api/embed",
            json={"model": model, "input": truncated},
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]


def cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between query vector and all rows in matrix."""
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return np.zeros(matrix.shape[0])
    normed_query = query_vec / query_norm

    row_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # Avoid division by zero
    row_norms = np.where(row_norms == 0, 1, row_norms)
    normed_matrix = matrix / row_norms

    return normed_matrix @ normed_query
