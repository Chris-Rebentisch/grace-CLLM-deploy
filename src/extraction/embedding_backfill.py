"""One-time embedding backfill for pre-existing entities lacking _embedding.

Idempotent CLI module: embeds entities that have no vector and writes
the embedding via SQL UPDATE. Entities that already carry an _embedding
are skipped by the WHERE clause.

``--re-embed`` (F-006 / ISS-0007): regenerate vectors for ALL non-deprecated
entities, including those that already carry an ``_embedding``. Needed when
the embedding space changes underneath stored vectors — the F-006 lowercase
normalization moved query-time embeddings into the lowercased space while
graphs embedded pre-fix still hold capitalized-space (often degenerate)
vectors, making query-vs-stored ANN unreliable until re-embedded.

Usage:
    python3 -m src.extraction.embedding_backfill [--re-embed] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio

import structlog

from src.extraction.entity_resolver import build_embedding_text
from src.extraction.extraction_config import ExtractionSettings
from src.graph.arcade_client import ArcadeClient
from src.graph.config import ArcadeConfig
from src.shared.embeddings import embed_texts

logger = structlog.get_logger()


async def backfill_embeddings(
    client: ArcadeClient,
    schema_json: dict,
    config: ExtractionSettings | None = None,
    re_embed: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Backfill _embedding for entities lacking a vector.

    Args:
        client: ArcadeDB client.
        schema_json: Active ontology schema with entity_types key.
        config: Extraction settings for embedding model/URL.
        re_embed: When True, regenerate vectors for ALL non-deprecated
            entities (F-006 / ISS-0007 embedding-space migration), not just
            those missing one.
        dry_run: When True, count and log candidates without embedding or
            writing anything.

    Returns:
        Dict mapping entity_type -> count of entities backfilled.
    """
    settings = config or ExtractionSettings()
    ollama_url = settings.extraction_base_url or "http://localhost:11434"
    model = settings.er_embedding_model
    entity_types = schema_json.get("entity_types", {})
    counts: dict[str, int] = {}

    for type_name in entity_types:
        # Query entities lacking an embedding (or ALL of them under --re-embed)
        embedding_clause = "" if re_embed else " AND _embedding IS NULL"
        query = (
            f"SELECT grace_id, name, @type AS entity_type "
            f"FROM {type_name} "
            f"WHERE _deprecated = false{embedding_clause}"
        )
        try:
            result = await client.execute_sql(query)
        except Exception as exc:
            logger.warning(
                "backfill.query_failed",
                type_name=type_name,
                error=str(exc),
            )
            continue

        rows = result.get("result", [])
        if not rows:
            continue

        if dry_run:
            counts[type_name] = len(rows)
            logger.info(
                "backfill.dry_run_candidates",
                type_name=type_name,
                candidates=len(rows),
                re_embed=re_embed,
            )
            continue

        backfilled = 0
        for row in rows:
            grace_id = row.get("grace_id", "")
            name = row.get("name", "")
            entity_type = row.get("entity_type", type_name)
            if not grace_id:
                continue

            # Build embedding text and compute vector
            # D445.4 — backfill uses same embed path as write-time (CP3).
            text = build_embedding_text(name, entity_type, None)
            try:
                vec = (await embed_texts(
                    [text], base_url=ollama_url, model=model,
                ))[0]
            except Exception as exc:
                logger.warning(
                    "backfill.embed_failed",
                    grace_id=grace_id,
                    error=str(exc),
                )
                continue

            # Write embedding via SQL UPDATE
            embedding_literal = "[" + ",".join(str(v) for v in vec) + "]"
            update_sql = (
                f"UPDATE {type_name} SET _embedding = {embedding_literal} "
                f"WHERE grace_id = '{grace_id}'"
            )
            try:
                await client.execute_sql(update_sql)
                backfilled += 1
                logger.info(
                    "backfill.entity_embedded",
                    grace_id=grace_id,
                    type_name=type_name,
                )
            except Exception as exc:
                logger.warning(
                    "backfill.write_failed",
                    grace_id=grace_id,
                    error=str(exc),
                )

        if backfilled > 0:
            counts[type_name] = backfilled
            logger.info(
                "backfill.type_complete",
                type_name=type_name,
                backfilled=backfilled,
            )

    logger.info("backfill.complete", total=sum(counts.values()), by_type=counts)
    return counts


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="embedding_backfill",
        description="Backfill (or regenerate) entity _embedding vectors.",
    )
    ap.add_argument(
        "--re-embed",
        action="store_true",
        help=(
            "Regenerate vectors for ALL non-deprecated entities, not just "
            "those missing one (F-006/ISS-0007 embedding-space migration)."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidates per type without embedding or writing.",
    )
    return ap


async def _main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    # ISS-0007 (in passing): this imported a nonexistent
    # ``src.ontology.database.get_session`` — the CLI entrypoint had never
    # been executed (phantom-import class). Session comes from the shared
    # factory like every other CLI.
    from src.ontology.database import get_active_version
    from src.shared.database import get_session_factory

    args = _build_argparser().parse_args(argv)
    arcade_config = ArcadeConfig()
    client = ArcadeClient(arcade_config)
    session = get_session_factory()()

    logger.info(
        "backfill.start",
        database=arcade_config.database,
        re_embed=args.re_embed,
        dry_run=args.dry_run,
    )
    try:
        active = get_active_version(session)
        if active is None:
            logger.error("backfill.no_active_ontology")
            return

        counts = await backfill_embeddings(
            client,
            active.schema_json,
            re_embed=args.re_embed,
            dry_run=args.dry_run,
        )
        total = sum(counts.values())
        logger.info("backfill.finished", total_backfilled=total, dry_run=args.dry_run)
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(_main())
