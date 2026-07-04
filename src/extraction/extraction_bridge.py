"""Email Extraction Bridge — CLI-only (D508, D246 mirror).

Selects emails from ``communication_events`` with
``triage_tier_outcome='passed_to_extraction'`` AND
``extraction_status='pending'``, composes clean extraction documents,
feeds them through the existing extraction pipeline, and tracks lifecycle
on ``communication_events.extraction_status``.

D356 capture-the-why: invariant = D246 CLI-only extraction pattern;
carve-out = CLI module spawned from route; authorization = D508.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from uuid import UUID

import structlog
import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.extraction.document_chunker import DocumentChunker
from src.extraction.email_composer import (
    CommunicationEventRow,
    compose_extraction_document,
)
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_pipeline import ExtractionPipeline
from src.extraction.instructor_client import ExtractionLLMClient
from src.extraction.ontology_router import OntologyRouter
from src.graph.arcade_client import ArcadeClient, get_arcade_client
from src.graph.cypher_utils import escape_cypher_string
from src.shared.database import get_engine

logger = structlog.get_logger()


def _build_pipeline(arcade_client: ArcadeClient | None) -> ExtractionPipeline:
    """Construct a fully-wired ExtractionPipeline for the bridge.

    D544 — capture-the-why: the prior bridge body called ``ExtractionPipeline()``
    with no arguments, but ``ExtractionPipeline.__init__`` requires
    ``config, chunker, router, client`` (interface drift identical in class to
    D539/D543 — the email front door was dead-on-arrival: every ``run`` invocation
    raised ``TypeError: missing 4 required positional arguments`` before any email
    was processed). This helper builds the four dependencies the way the working
    ``eval_checkpoint`` reference does, but with the *production* ``OntologyRouter``
    (live ``/api/ontology/active`` over ``MCP_GRACE_BASE_URL``) rather than the
    server-less ``FileSchemaRouter``, preserving the D246 client posture
    (the bridge is the sanctioned CLI; it talks to the API as a client).
    """
    config = ExtractionSettings()
    chunker = DocumentChunker(config)
    base_url = os.environ.get("MCP_GRACE_BASE_URL", "http://localhost:8000")
    router = OntologyRouter(base_url=base_url)
    client = ExtractionLLMClient(config)
    return ExtractionPipeline(
        config=config,
        chunker=chunker,
        router=router,
        client=client,
        arcade_client=arcade_client,
    )


def _build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the extraction bridge."""
    parser = argparse.ArgumentParser(
        prog="python -m src.extraction.extraction_bridge",
        description=(
            "GrACE Email Extraction Bridge (D508). Selects triaged emails "
            "and feeds them through the extraction pipeline."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Run the extraction bridge.")
    run_parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Filter to a specific ingestion source UUID.",
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of emails to process (default 100).",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log count of pending emails without processing.",
    )
    run_parser.add_argument(
        "--observation-time",
        type=str,
        default=None,
        help="ISO 8601 observation time (informational, for logging).",
    )
    run_parser.add_argument(
        "--skip-privileged",
        action="store_true",
        help="Skip emails with |privileged| sensitivity tag.",
    )
    run_parser.add_argument(
        "--module",
        type=str,
        default=None,
        help=(
            "Ontology module to extract against (e.g. 'legal'). Required on "
            "multi-module deployments — the OntologyRouter cannot disambiguate "
            "otherwise (D545). Omit on single-module deployments."
        ),
    )
    # D520 — retag backfill for pre-Chunk-81 email-derived vertices; R2 mitigation.
    retag_parser = sub.add_parser(
        "retag",
        help="Backfill sensitivity_tags on pre-existing email-derived vertices.",
    )
    retag_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log count of vertices needing retag without modifying.",
    )
    retag_parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Vertices per UPDATE batch (default 100).",
    )
    return parser


def _load_skip_privileged_config() -> bool:
    """Load skip_privileged_extraction from config/discovery.yaml.

    F-0047b / ISS-0055 Layer 0 (2026-07-03) — capture-the-why: the default
    (missing key AND unreadable config) flipped ``False`` -> ``True``.
    GrACE-Product §8 makes extraction-time exclusion the structural
    commitment — privileged content should not enter the graph by default;
    extracting privileged email is an explicit opt-in
    (``ingestion.skip_privileged_extraction: false`` set deliberately).
    Fail-safe: a broken/missing config must not silently start extracting
    privileged content.
    """
    try:
        with open("config/discovery.yaml") as f:
            config = yaml.safe_load(f) or {}
        ingestion_cfg = config.get("ingestion", {})
        return bool(ingestion_cfg.get("skip_privileged_extraction", True))
    except Exception:
        return True


def _select_pending_emails(
    session: Session,
    limit: int,
    source_id: str | None = None,
) -> list[dict]:
    """Select pending emails eligible for extraction."""
    query = (
        "SELECT id, message_id, sender_display_name, sender_email, subject, "
        "sent_at, received_at, ingested_at, body_plain, sensitivity_tags "
        "FROM communication_events "
        "WHERE triage_tier_outcome = 'passed_to_extraction' "
        "AND extraction_status = 'pending' "
    )
    params: dict = {"limit": limit}

    if source_id:
        query += "AND source_id = :source_id "
        params["source_id"] = source_id

    query += "ORDER BY sent_at ASC NULLS LAST LIMIT :limit"

    rows = session.execute(text(query), params).fetchall()
    columns = [
        "id", "message_id", "sender_display_name", "sender_email", "subject",
        "sent_at", "received_at", "ingested_at", "body_plain", "sensitivity_tags",
    ]
    return [dict(zip(columns, row)) for row in rows]


def _is_privileged(sensitivity_tags: str | None) -> bool:
    """Check if the email has the |privileged| sensitivity tag."""
    if not sensitivity_tags:
        return False
    return "|privileged|" in sensitivity_tags


def _get_temporal_fallback(row: dict) -> datetime | None:
    """D511 fallback chain: sent_at → received_at → ingested_at."""
    return row.get("sent_at") or row.get("received_at") or row.get("ingested_at")


def _build_temporal_stamp_cypher(grace_ids: list[str], temporal_value: datetime) -> str:
    """Build the D511 fallback temporal-stamp Cypher statement.

    Every interpolated value goes through ``escape_cypher_string`` (same
    pattern as ``src/graph/entity_ops.py``) so a quote or backslash in a
    grace_id or timestamp can never break out of the string literal.
    """
    id_list = ", ".join(f"'{escape_cypher_string(gid)}'" for gid in grace_ids)
    escaped_ts = escape_cypher_string(temporal_value.isoformat())
    return (
        f"MATCH (v) WHERE v.grace_id IN [{id_list}] "
        f"AND v.valid_from IS NULL "
        f"SET v.valid_from = '{escaped_ts}' "
        f"RETURN count(v)"
    )


async def _process_email(
    row: dict,
    pipeline: ExtractionPipeline,
    session: Session,
    arcade_client: ArcadeClient | None,
    skip_privileged: bool,
    module_name: str | None = None,
) -> str:
    """Process a single email. Returns outcome: success|error|skipped."""
    message_id = row["message_id"]
    sensitivity_tags = row.get("sensitivity_tags")

    # Privileged-email gate
    if skip_privileged and _is_privileged(sensitivity_tags):
        session.execute(
            text(
                "UPDATE communication_events "
                "SET extraction_status = 'skipped' "
                "WHERE message_id = :msg_id"
            ),
            {"msg_id": message_id},
        )
        session.commit()
        logger.info(
            "email_extraction_skipped_privileged",
            message_id=message_id,
        )
        return "skipped"

    # Compose extraction document
    event_row = CommunicationEventRow(
        message_id=message_id,
        sender_display_name=row.get("sender_display_name"),
        sender_email=row["sender_email"],
        subject=row.get("subject"),
        sent_at=row.get("sent_at"),
        body_plain=row.get("body_plain"),
    )
    composed_text = compose_extraction_document(event_row)
    doc_id = f"email:{message_id}"

    # D520 — propagate source-email sensitivity to domain vertices via extraction bridge.
    email_sensitivity = sensitivity_tags or ""

    # Extract. D545 — module_name disambiguates the OntologyRouter on
    # multi-module deployments (e.g. legal + intent); None preserves the
    # single-module active-schema path.
    batch = await pipeline.extract_document(
        document_text=composed_text,
        document_id=doc_id,
        module_name=module_name,
        evidence_origin="communication",
        session=session,
        sensitivity_tags=email_sensitivity,
    )

    # Collect grace_ids for D511 temporal stamp
    grace_ids = [
        e.resolved_grace_id
        for e in batch.entities
        if e.resolved_grace_id
    ]

    # Post-extract lookup: find extraction_event_id (UUID)
    event_id_row = session.execute(
        text(
            "SELECT event_id FROM extraction_events_pg "
            "WHERE source_document_id = :doc_id "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"doc_id": doc_id},
    ).fetchone()

    extraction_event_id = str(event_id_row[0]) if event_id_row else None

    if not event_id_row:
        logger.warning(
            "email_extraction_no_event_row",
            message_id=message_id,
            doc_id=doc_id,
        )

    # Update communication_events
    session.execute(
        text(
            "UPDATE communication_events "
            "SET extraction_status = 'extracted', "
            "    extraction_event_id = :event_id, "
            "    extracted_at = :ts "
            "WHERE message_id = :msg_id"
        ),
        {
            "msg_id": message_id,
            "event_id": extraction_event_id,
            "ts": datetime.now(timezone.utc),
        },
    )
    session.commit()

    # D511: fallback temporal stamp on vertices
    temporal_value = _get_temporal_fallback(row)
    if temporal_value and grace_ids and arcade_client:
        try:
            # Post-write fallback stamp — does not overwrite existing valid_from.
            # Values are escaped via escape_cypher_string inside the builder.
            cypher = _build_temporal_stamp_cypher(grace_ids, temporal_value)
            # D550 — ArcadeClient has no `command` method (that call always raised
            # AttributeError, caught below, so valid_from was never stamped — Finding
            # #16). execute_cypher is the DML/DQL entry point (language hardcoded to
            # opencypher, never 'cypher').
            await arcade_client.execute_cypher(cypher)
        except Exception as e:
            logger.warning(
                "email_extraction_temporal_stamp_failed",
                message_id=message_id,
                error=str(e),
            )

    return "success"


async def run_bridge(
    limit: int = 100,
    source_id: str | None = None,
    dry_run: bool = False,
    skip_privileged: bool = False,
    observation_time: str | None = None,
    module_name: str | None = None,
) -> dict:
    """Main bridge execution loop."""
    from sqlalchemy.orm import sessionmaker

    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        rows = _select_pending_emails(session, limit, source_id)

        if dry_run:
            logger.info(
                "email_extraction_bridge_dry_run",
                pending_count=len(rows),
                source_id=source_id,
                limit=limit,
            )
            return {"processed": 0, "pending": len(rows), "dry_run": True}

        # Load config for skip_privileged
        if not skip_privileged:
            skip_privileged = _load_skip_privileged_config()

        # Import counter lazily to allow module collection without metrics registration
        from src.analytics.metrics import grace_email_extracted_total  # noqa: F811

        # Set up arcade client first, then the fully-wired pipeline (D544).
        # D547 — use get_arcade_client() (settings-aware, honors ARCADE_DATABASE)
        # NOT bare ArcadeClient() which hardcodes ArcadeConfig().database="grace" and
        # ignores ARCADE_DATABASE — that wrote email-derived vertices into the GOLD
        # `grace` graph during sandbox testing (the D538 isolation gap, for the
        # extraction graph client). Mirrors D538's ingestion fix.
        try:
            arcade_client = get_arcade_client()
        except Exception:
            arcade_client = None
            logger.warning("email_extraction_bridge_no_arcade")

        pipeline = _build_pipeline(arcade_client)

        results = {"success": 0, "error": 0, "skipped": 0}

        for row in rows:
            try:
                outcome = await _process_email(
                    row, pipeline, session, arcade_client, skip_privileged,
                    module_name=module_name,
                )
                results[outcome] += 1
                grace_email_extracted_total.add(1, {"outcome": outcome})
            except Exception as e:
                # Per-email error isolation — continue to next
                logger.error(
                    "email_extraction_bridge_error",
                    message_id=row.get("message_id"),
                    error=str(e),
                )
                try:
                    session.execute(
                        text(
                            "UPDATE communication_events "
                            "SET extraction_status = 'failed' "
                            "WHERE message_id = :msg_id"
                        ),
                        {"msg_id": row["message_id"]},
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                results["error"] += 1
                grace_email_extracted_total.add(1, {"outcome": "error"})

        logger.info("email_extraction_bridge_complete", **results)
        return results

    finally:
        session.close()


async def run_retag(
    batch_size: int = 100,
    dry_run: bool = False,
) -> dict:
    """D520 — REQUIRED retag of pre-Chunk-81 email-derived vertices; R2 mitigation.

    Idempotent batched UPDATE of sensitivity_tags on email-derived vertices
    from current source-email tags. Re-run produces no change when tags
    are already current.

    D356 capture-the-why: D520 — REQUIRED retag of pre-Chunk-81
    email-derived vertices; R2 mitigation.
    """
    from sqlalchemy.orm import sessionmaker
    from src.ingestion.communications.sensitivity_tagger import (
        tags_from_bar_form,
        tags_to_bar_form,
    )
    from src.graph.cypher_utils import escape_cypher_string

    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        # D547 — settings-aware client honors ARCADE_DATABASE (see run_bridge note).
        arcade_client = get_arcade_client()
    except Exception:
        logger.error("retag_no_arcade_client")
        return {"error": "ArcadeDB unavailable"}

    try:
        # Find email-derived vertices by querying graph for evidence_origin='communication'
        offset = 0
        total_updated = 0
        total_skipped = 0
        total_scanned = 0

        while True:
            # Query ArcadeDB for email-derived vertices in batches
            select_query = (
                f"SELECT grace_id, sensitivity_tags "
                f"FROM (MATCH {{class: V, as: n, where: (evidence_origin = 'communication')}} "
                f"RETURN n.grace_id AS grace_id, n.sensitivity_tags AS sensitivity_tags) "
                f"LIMIT {batch_size} SKIP {offset}"
            )
            try:
                result = await arcade_client.execute_sql(select_query)
                rows = result.get("result", [])
            except Exception:
                # Fallback: try OpenCypher approach
                cypher_query = (
                    f"MATCH (n) WHERE n.evidence_origin = 'communication' "
                    f"RETURN n.grace_id AS grace_id, n.sensitivity_tags AS sensitivity_tags "
                    f"SKIP {offset} LIMIT {batch_size}"
                )
                try:
                    result = await arcade_client.execute_cypher(cypher_query)
                    rows = result.get("result", [])
                except Exception as e:
                    logger.warning("retag_query_failed", error=str(e), offset=offset)
                    break

            if not rows:
                break

            for row in rows:
                total_scanned += 1
                grace_id = row.get("grace_id")
                if not grace_id:
                    continue

                current_tags = row.get("sensitivity_tags", "") or ""

                # Look up source email sensitivity_tags from communication_events
                # via the source_document_id pattern "email:<message_id>"
                source_query = (
                    "SELECT DISTINCT ce.sensitivity_tags "
                    "FROM communication_events ce "
                    "JOIN extraction_events_pg ee ON ee.source_document_id = 'email:' || ce.message_id "
                    "WHERE ee.source_document_id IN ("
                    "  SELECT source_document_id FROM extraction_events_pg "
                    "  WHERE event_id IN ("
                    "    SELECT extraction_event_id FROM extraction_claims "
                    "    WHERE resolved_entity_grace_id = :gid"
                    "  )"
                    ")"
                )
                try:
                    source_rows = session.execute(text(source_query), {"gid": grace_id}).fetchall()
                except Exception:
                    # Simpler fallback query
                    source_rows = []

                # Union all source tags
                merged_tags: set[str] = set(tags_from_bar_form(current_tags))
                for sr in source_rows:
                    source_tags_str = sr[0] if sr[0] else ""
                    merged_tags |= set(tags_from_bar_form(source_tags_str))

                new_tags = tags_to_bar_form(sorted(merged_tags)) if merged_tags else ""

                if new_tags == current_tags:
                    total_skipped += 1
                    continue

                if dry_run:
                    logger.info(
                        "retag_would_update",
                        grace_id=grace_id,
                        old_tags=current_tags,
                        new_tags=new_tags,
                    )
                    total_updated += 1
                    continue

                # UPDATE vertex sensitivity_tags in ArcadeDB
                escaped_gid = escape_cypher_string(grace_id)
                escaped_tags = escape_cypher_string(new_tags)
                update_cypher = (
                    f"MATCH (n {{grace_id: '{escaped_gid}'}}) "
                    f"SET n.sensitivity_tags = '{escaped_tags}' "
                    f"RETURN n.grace_id"
                )
                try:
                    await arcade_client.execute_cypher(update_cypher)
                    total_updated += 1
                except Exception as e:
                    logger.warning(
                        "retag_update_failed",
                        grace_id=grace_id,
                        error=str(e),
                    )

            offset += batch_size

        logger.info(
            "retag_complete",
            scanned=total_scanned,
            updated=total_updated,
            skipped=total_skipped,
            dry_run=dry_run,
        )
        return {
            "scanned": total_scanned,
            "updated": total_updated,
            "skipped": total_skipped,
            "dry_run": dry_run,
        }

    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    # F-15: mirror this subprocess's OTel counters into the prometheus
    # multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = _build_argparser()
    args = parser.parse_args(argv)

    if args.command == "run":
        result = asyncio.run(
            run_bridge(
                limit=args.limit,
                source_id=args.source_id,
                dry_run=args.dry_run,
                skip_privileged=args.skip_privileged,
                observation_time=args.observation_time,
                module_name=args.module,
            )
        )
        if result.get("dry_run"):
            print(f"Dry run: {result['pending']} emails pending extraction.")
        else:
            print(
                f"Bridge complete: {result.get('success', 0)} extracted, "
                f"{result.get('error', 0)} failed, "
                f"{result.get('skipped', 0)} skipped."
            )
        return 0

    elif args.command == "retag":
        result = asyncio.run(
            run_retag(
                batch_size=args.batch_size,
                dry_run=args.dry_run,
            )
        )
        if result.get("dry_run"):
            print(f"Retag dry run: {result.get('updated', 0)} vertices would be updated.")
        else:
            print(
                f"Retag complete: {result.get('updated', 0)} updated, "
                f"{result.get('skipped', 0)} skipped, "
                f"{result.get('scanned', 0)} scanned."
            )
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
