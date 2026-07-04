"""Bootstrap pipe — email-seeded Discovery ingestion (D518, D246 mirror).

Composes curated email subsets into processed_documents rows for Discovery
consumption. CLI-only — MUST NOT import fastapi or apscheduler.

D356 capture-the-why: D518 — email-seeded Discovery bootstrap; D246 mirror.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog

logger = structlog.get_logger()


def _build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (D476 contract-testable)."""
    parser = argparse.ArgumentParser(
        prog="bootstrap_pipe",
        description="Bootstrap pipe — email-seeded Discovery ingestion (D518).",
    )
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Run bootstrap pipeline")
    run_parser.add_argument(
        "--subset-id",
        type=str,
        required=True,
        help="UUID of the curated_email_subsets row to consume",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compose without writing to processed_documents",
    )
    return parser


def run_bootstrap(
    subset_id: UUID,
    *,
    dry_run: bool = False,
) -> int:
    """Execute bootstrap pipeline for a curated email subset.

    1. Read curated_email_subsets row by subset_id (must have sentinel_status='ready').
    2. Pull communication_events bodies for selected_message_ids.
    3. Compose via email_composer.compose_extraction_document().
    4. INSERT into processed_documents with origin='curated_email'.
    5. Update sentinel_status to 'consumed'.

    Returns count of rows inserted.
    """
    from sqlalchemy import text

    from src.extraction.email_composer import CommunicationEventRow, compose_extraction_document
    from src.ingestion.models import CuratedEmailSubsetRow
    from src.shared.database import get_session_factory

    session_factory = get_session_factory()
    db = session_factory()

    try:
        # Step 1: Read subset row
        subset = (
            db.query(CuratedEmailSubsetRow)
            .filter(CuratedEmailSubsetRow.id == subset_id)
            .first()
        )
        if subset is None:
            logger.error("bootstrap.subset_not_found", subset_id=str(subset_id))
            return 0

        if subset.sentinel_status != "ready":
            logger.error(
                "bootstrap.subset_not_ready",
                subset_id=str(subset_id),
                status=subset.sentinel_status,
            )
            return 0

        # Step 2: Pull communication_events for selected message_ids
        message_ids = subset.selected_message_ids or []
        if not message_ids:
            logger.warning("bootstrap.no_messages", subset_id=str(subset_id))
            return 0

        # Query communication_events by message_id
        placeholders = ", ".join(f":mid_{i}" for i in range(len(message_ids)))
        params = {f"mid_{i}": mid for i, mid in enumerate(message_ids)}
        rows = db.execute(
            text(
                f"SELECT message_id, sender_display_name, sender_email, "
                f"subject, sent_at, body_plain "
                f"FROM communication_events "
                f"WHERE message_id IN ({placeholders})"
            ),
            params,
        ).fetchall()

        if not rows:
            logger.warning("bootstrap.no_events_found", subset_id=str(subset_id))
            return 0

        # Step 3 & 4: Compose and insert into processed_documents
        inserted = 0
        source_id = str(subset.source_id)

        for row in rows:
            event = CommunicationEventRow(
                message_id=row.message_id,
                sender_display_name=row.sender_display_name,
                sender_email=row.sender_email,
                subject=row.subject,
                sent_at=row.sent_at,
                body_plain=row.body_plain,
            )

            composed_text = compose_extraction_document(event)
            # Synthetic file_path — UNIQUE-safe, deterministic, no real path created (D518)
            synthetic_path = f"email://{source_id}/{row.message_id}"

            if dry_run:
                logger.info(
                    "bootstrap.dry_run_compose",
                    message_id=row.message_id,
                    file_path=synthetic_path,
                    text_length=len(composed_text),
                )
                inserted += 1
                continue

            # Check for existing row (UNIQUE on file_path)
            existing = db.execute(
                text("SELECT id FROM processed_documents WHERE file_path = :fp"),
                {"fp": synthetic_path},
            ).first()
            if existing:
                logger.debug("bootstrap.skip_existing", file_path=synthetic_path)
                continue

            doc_id = str(uuid4())
            now = datetime.now(UTC)
            db.execute(
                text(
                    "INSERT INTO processed_documents "
                    "(id, file_path, file_name, file_type, file_size_bytes, "
                    "processed_at, domain, extracted_text, word_count, status, "
                    "origin, source_type) "
                    "VALUES (:id, :fp, :fn, :ft, :fs, :pa, :domain, :et, :wc, :status, "
                    ":origin, :source_type)"
                ),
                {
                    "id": doc_id,
                    "fp": synthetic_path,
                    "fn": f"email_{row.message_id}",
                    "ft": "EMAIL",
                    "fs": len(composed_text.encode("utf-8")),
                    "pa": now,
                    "domain": "other",
                    "et": composed_text,
                    "wc": len(composed_text.split()),
                    "status": "COMPLETE",
                    "origin": "curated_email",
                    "source_type": "curated_email",
                },
            )
            inserted += 1

        # Step 5: Update sentinel_status
        if not dry_run and inserted > 0:
            subset.sentinel_status = "consumed"

        db.commit()
        logger.info(
            "bootstrap.complete",
            subset_id=str(subset_id),
            inserted=inserted,
            dry_run=dry_run,
        )
        return inserted

    except Exception:
        db.rollback()
        logger.exception("bootstrap.failed", subset_id=str(subset_id))
        raise
    finally:
        db.close()


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for bootstrap pipe."""
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = _build_argparser()
    args = parser.parse_args(argv)

    if args.command != "run":
        parser.print_help()
        sys.exit(0)

    try:
        subset_id = UUID(args.subset_id)
    except ValueError:
        logger.error("bootstrap.invalid_uuid", subset_id=args.subset_id)
        sys.exit(1)

    logger.info(
        "bootstrap.run_start",
        subset_id=str(subset_id),
        dry_run=args.dry_run,
    )

    try:
        count = run_bootstrap(subset_id, dry_run=args.dry_run)
        logger.info("bootstrap.run_complete", inserted=count)
    except Exception:
        logger.exception("bootstrap.run_failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
