"""Chunk-semantic retrieval strategy — ANN search over Document_Chunk._embedding.

D466 (Chunk 71 CP4): runs vectorNeighbors() SQL on Document_Chunk._embedding,
returns top-K chunks adapted to RetrievalCandidate shape for RRF merge with the
existing 4-strategy pipeline.

CF3 5th allowlist entry (PERMANENT, D466).
"""

from __future__ import annotations

import structlog

from src.graph.arcade_client import ArcadeClient
from src.retrieval.retrieval_models import RetrievalCandidate
from src.shared.embeddings import embed_texts

logger = structlog.get_logger()


async def chunk_semantic_search(
    client: ArcadeClient,
    query_text: str,
    top_k: int = 20,
    embedding_model: str = "nomic-embed-text",
    ollama_base_url: str = "http://localhost:11434",
) -> list[RetrievalCandidate]:
    """Run ANN search over Document_Chunk._embedding via vectorNeighbors() SQL.

    Args:
        client: ArcadeDB client.
        query_text: Natural language query to embed and search.
        top_k: Maximum number of chunk results to return.
        embedding_model: Ollama embedding model name.
        ollama_base_url: Ollama API base URL.

    Returns:
        List of RetrievalCandidate objects adapted from Document_Chunk vertices.
    """
    # Embed the query
    try:
        query_embedding = (await embed_texts(
            [query_text],
            base_url=ollama_base_url,
            model=embedding_model,
        ))[0]
    except Exception as exc:
        logger.warning(
            "chunk_semantic.embed_failed",
            error=str(exc),
        )
        return []

    # Build vectorNeighbors() SQL query
    embedding_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"
    sql = (
        f"SELECT grace_id, text, source_document_id, chunk_index, "
        f"chunk_token_count, sensitivity_tags, _deprecated "
        f"FROM ("
        f"  SELECT *, vectorNeighbors('Document_Chunk[_embedding]', "
        f"{embedding_literal}, {top_k}) AS score "
        f"  FROM Document_Chunk"
        f") WHERE _deprecated = false "
        f"ORDER BY score DESC LIMIT {top_k}"
    )

    try:
        result = await client.execute_sql(sql)
        rows = result.get("result", [])
    except Exception as exc:
        logger.warning(
            "chunk_semantic.query_failed",
            error=str(exc),
        )
        return []

    # Adapt to RetrievalCandidate shape
    candidates: list[RetrievalCandidate] = []
    for rank, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        grace_id = row.get("grace_id", "")
        text = row.get("text", "")
        source_doc = row.get("source_document_id", "")
        chunk_idx = row.get("chunk_index", 0)

        candidates.append(RetrievalCandidate(
            grace_id=grace_id,
            entity_type="Document_Chunk",
            name=f"Chunk {chunk_idx} of {source_doc}",
            properties={
                "text": text,
                "source_document_id": source_doc,
                "chunk_index": chunk_idx,
                "chunk_token_count": row.get("chunk_token_count", 0),
                "sensitivity_tags": row.get("sensitivity_tags", ""),
            },
            score=row.get("score", 0.0),
            strategy="chunk_semantic",
            rank=rank,
        ))

    logger.info(
        "chunk_semantic.search_complete",
        results=len(candidates),
        top_k=top_k,
    )
    return candidates
