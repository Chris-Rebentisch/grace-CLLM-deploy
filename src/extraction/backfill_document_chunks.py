"""Backfill CLI: re-chunk existing corpus and INSERT Document_Chunk vertices.

D466 (Chunk 71 CP5). D246 mirror — CLI-only, never invoked from FastAPI
lifespan or route modules.

Reads ``processed_documents.extracted_text`` (verified at
``mine_sampler.py:393`` — ``source_text = doc_row.extracted_text or ""``).
Uses extracted_text only — never any alternative column.

Re-chunks via existing ``DocumentChunker`` (``document_chunker.py:23``),
embeds via ``embed_texts()`` (D265), computes sensitivity tags via
rule-based tagger (D441), INSERTs ``Document_Chunk`` vertices via
canonical lookup. Idempotent — re-runs produce no new vertices.

No ``derives_from`` edges for backfilled chunks — pre-Chunk-71 entities
lack chunk reference.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from uuid import uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.discovery.database import ProcessedDocumentRow
from src.extraction.document_chunker import DocumentChunker
from src.extraction.graph_writer import (
    _compute_chunk_sensitivity_tags,
    _insert_document_chunk_vertex,
    _lookup_document_chunk,
)
from src.graph.arcade_client import ArcadeClient, get_arcade_client
from src.shared.database import get_session_factory
from src.shared.embeddings import embed_texts

log = structlog.get_logger()


async def backfill_document_chunks(
    session: Session,
    client: ArcadeClient,
    source_dir: str | None = None,
    ollama_base_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
) -> dict:
    """Backfill Document_Chunk vertices from processed_documents corpus.

    Args:
        session: SQLAlchemy session.
        client: ArcadeDB client.
        source_dir: Optional directory filter — only process rows whose
            file_path starts with this prefix (matching batch_runner.py convention).
        ollama_base_url: Ollama API base URL.
        embedding_model: Embedding model name.

    Returns:
        Summary dict with counts.
    """
    query = select(ProcessedDocumentRow).where(
        ProcessedDocumentRow.status == "COMPLETED",
    )
    if source_dir:
        query = query.where(
            ProcessedDocumentRow.file_path.startswith(source_dir),
        )

    rows = session.execute(query).scalars().all()
    log.info("backfill.corpus_loaded", doc_count=len(rows))

    chunker = DocumentChunker()
    stats = {
        "documents_processed": 0,
        "chunks_created": 0,
        "chunks_skipped": 0,
        "errors": 0,
    }

    for row in rows:
        # Read extracted_text (spec §6 CP5, mine_sampler.py:393)
        source_text = row.extracted_text or ""
        if not source_text.strip():
            continue

        doc_id = str(row.id)
        chunks = chunker.chunk_text(source_text, document_id=doc_id)
        stats["documents_processed"] += 1

        for idx, chunk in enumerate(chunks):
            try:
                # Canonical lookup — idempotent
                existing = await _lookup_document_chunk(client, doc_id, idx)
                if existing:
                    stats["chunks_skipped"] += 1
                    continue

                # Embed chunk text
                embedding = (await embed_texts(
                    [chunk.text],
                    base_url=ollama_base_url,
                    model=embedding_model,
                ))[0]

                # Compute sensitivity tags
                sensitivity_tags = _compute_chunk_sensitivity_tags(chunk.text)

                # INSERT vertex
                await _insert_document_chunk_vertex(
                    client=client,
                    grace_id=str(uuid4()),
                    source_document_id=doc_id,
                    chunk_index=idx,
                    text=chunk.text,
                    chunk_token_count=chunk.token_count_estimate,
                    embedding=embedding,
                    sensitivity_tags=sensitivity_tags,
                )
                stats["chunks_created"] += 1
            except Exception as exc:
                stats["errors"] += 1
                log.warning(
                    "backfill.chunk_failed",
                    doc_id=doc_id,
                    chunk_index=idx,
                    error=str(exc),
                )

    log.info("backfill.complete", **stats)
    return stats


def main():
    """CLI entry point for backfill_document_chunks."""
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = argparse.ArgumentParser(
        description=(
            "Backfill Document_Chunk vertices from existing processed_documents corpus. "
            "Idempotent — re-runs produce no new vertices. "
            "No derives_from edges — pre-Chunk-71 entities lack chunk reference."
        ),
    )
    parser.add_argument(
        "--source-dir",
        type=str,
        default=None,
        help="Directory to filter corpus path (matching batch_runner.py convention). "
             "When omitted, backfills all COMPLETED rows.",
    )
    parser.add_argument(
        "--ollama-base-url",
        type=str,
        default="http://localhost:11434",
        help="Ollama API base URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default="nomic-embed-text",
        help="Embedding model name (default: nomic-embed-text)",
    )
    args = parser.parse_args()

    session = get_session_factory()()
    client = get_arcade_client()

    try:
        stats = asyncio.run(backfill_document_chunks(
            session=session,
            client=client,
            source_dir=args.source_dir,
            ollama_base_url=args.ollama_base_url,
            embedding_model=args.embedding_model,
        ))
        print(f"Backfill complete: {stats}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
