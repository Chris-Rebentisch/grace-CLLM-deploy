"""CLI entry point for ingestion pipeline (D246 mirror — CLI-only).

Usage::

    python -m src.ingestion run --source-id <UUID> [--dry-run] [--limit N]
    python -m src.ingestion triage --source-id <UUID> [--tiers 1,2,3,4] [--dry-run] [--limit N]
    python -m src.ingestion cycle --source-id <UUID> [--tiers 1,2,3,4] [--dry-run] [--limit N]

Never invoked from FastAPI lifespan or route modules.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

import structlog

logger = structlog.get_logger()


def main() -> None:
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = argparse.ArgumentParser(
        prog="python -m src.ingestion",
        description="GrACE Communication Ingestion Pipeline (D246 mirror — CLI-only)",
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run ingestion for a single source")
    run_parser.add_argument(
        "--source-id", type=str, required=True, help="UUID of the ingestion source"
    )
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Skip persistence, log only"
    )
    run_parser.add_argument(
        "--limit", type=int, default=None, help="Cap message iteration"
    )

    triage_parser = sub.add_parser("triage", help="Run triage pipeline for a single source (D434)")
    triage_parser.add_argument(
        "--source-id", type=str, required=True, help="UUID of the ingestion source"
    )
    triage_parser.add_argument(
        "--run-id", type=str, default=None,
        help="UUID of pre-created IngestionRun (required when spawned from API route)",
    )
    triage_parser.add_argument(
        "--tiers", type=str, default="1,2,3,4",
        help="Comma-separated tier numbers to run (default: 1,2,3,4)",
    )
    triage_parser.add_argument(
        "--dry-run", action="store_true", help="Skip persistence, log only"
    )
    triage_parser.add_argument(
        "--limit", type=int, default=None, help="Cap event iteration"
    )
    triage_parser.add_argument(
        "--checkpoint-interval", type=int, default=None,
        help="Override checkpoint interval from triage_rules.yaml",
    )

    cycle_parser = sub.add_parser("cycle", help="Chain adapter pull + triage in one process (Chunk 57)")
    cycle_parser.add_argument(
        "--source-id", type=str, required=True, help="UUID of the ingestion source"
    )
    cycle_parser.add_argument(
        "--dry-run", action="store_true", help="Skip persistence, log only"
    )
    cycle_parser.add_argument(
        "--limit", type=int, default=None, help="Cap message/event iteration"
    )
    cycle_parser.add_argument(
        "--tiers", type=str, default="1,2,3,4",
        help="Comma-separated tier numbers for triage (default: 1,2,3,4)",
    )
    cycle_parser.add_argument(
        "--checkpoint-interval", type=int, default=None,
        help="Override checkpoint interval from triage_rules.yaml",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "run":
        from src.shared.database import get_session_factory
        from src.ingestion.pipeline import IngestionPipeline

        session_factory = get_session_factory()
        db = session_factory()

        try:
            pipeline = IngestionPipeline(db)
            run_id = asyncio.run(
                pipeline.run(
                    UUID(args.source_id),
                    dry_run=args.dry_run,
                    limit=args.limit,
                )
            )
            logger.info("ingestion_cli_complete", run_id=str(run_id))
        finally:
            db.close()

    elif args.command == "triage":
        # Parse tiers — Chunk 57: tier 4 no longer rejected
        tier_strs = [t.strip() for t in args.tiers.split(",")]
        tiers_int: list[int] = []
        for t in tier_strs:
            try:
                tier_val = int(t)
            except ValueError:
                logger.error("triage_invalid_tier", tier=t)
                sys.exit(2)
            tiers_int.append(tier_val)

        from src.shared.database import get_session_factory
        from src.ingestion.communications.triage.pipeline import TriagePipeline

        session_factory = get_session_factory()
        db = session_factory()

        try:
            pipeline = TriagePipeline(db)
            run_id_arg = UUID(args.run_id) if args.run_id else None
            processed = asyncio.run(
                pipeline.run(
                    UUID(args.source_id),
                    run_id=run_id_arg,
                    tiers=tuple(tiers_int),
                    dry_run=args.dry_run,
                    limit=args.limit,
                )
            )
            logger.info("triage_cli_complete", processed=processed)
        finally:
            db.close()

    elif args.command == "cycle":
        asyncio.run(_run_cycle(args))


async def _run_cycle(args) -> None:
    """Chain adapter pull → triage in one asyncio.run() call (Chunk 57, D424).

    Exit codes: 0 (success / graceful SIGTERM); 1 (unrecoverable failure).
    """
    import yaml
    from src.shared.database import get_session_factory
    from src.ingestion.models import IngestionSource

    source_id = UUID(args.source_id)
    session_factory = get_session_factory()
    db = session_factory()

    try:
        # Load source
        source = db.query(IngestionSource).filter_by(id=source_id).first()
        if source is None:
            logger.error("cycle_source_not_found", source_id=str(source_id))
            sys.exit(1)

        # (1) Readiness gate (D274)
        config_path = Path(__file__).resolve().parents[2] / "config" / "discovery.yaml"
        with open(config_path) as f:
            disc_config = yaml.safe_load(f) or {}
        ingestion_config = disc_config.get("ingestion", {})
        deployment_path = ingestion_config.get("deployment_path")

        # F-0030d / ISS-0047 (deferral closed 2026-07-03): the old
        # ``.get("deployment_path", "A")`` read meant a MISSING key silently
        # defaulted to "A" while the shipped default (``deployment_path:
        # null``) flowed through as None — the cycle proceeded on a path the
        # operator never chose. Mirror the route-side validation on
        # ``GET /api/ingestion/readiness``: missing/null and invalid values
        # exit with operator guidance instead of silently assuming "A".
        _DEPLOYMENT_PATH_GUIDANCE = (
            "set ingestion.deployment_path to A, B, or C in "
            "config/discovery.yaml — see PATCH "
            "/api/ingestion/config/deployment-path"
        )
        if deployment_path is None:
            logger.error("cycle_deployment_path_not_configured")
            print(
                "Error: ingestion.deployment_path is not configured — "
                + _DEPLOYMENT_PATH_GUIDANCE,
                file=sys.stderr,
            )
            sys.exit(2)
        if deployment_path not in ("A", "B", "C"):
            logger.error(
                "cycle_deployment_path_invalid",
                deployment_path=str(deployment_path),
            )
            print(
                f"Error: ingestion.deployment_path {deployment_path!r} in "
                "config/discovery.yaml is invalid — "
                + _DEPLOYMENT_PATH_GUIDANCE,
                file=sys.stderr,
            )
            sys.exit(2)

        segments = ingestion_config.get("segments", [source.segment])

        from src.graph.arcade_client import get_arcade_client
        arcade_client = get_arcade_client()

        from src.ingestion.readiness import check_readiness
        from src.ingestion.models import ReadinessThresholds, CuratedEmailSubsetRow

        readiness_config = ingestion_config.get("readiness", {})
        thresholds = ReadinessThresholds(
            cq_mention_threshold=readiness_config.get("cq_mention_threshold", 3),
            confidence_threshold=readiness_config.get("confidence_threshold", 0.85),
        )

        # Path B bootstrap check
        bootstrap_complete = True
        if deployment_path == "B":
            ready_subset = (
                db.query(CuratedEmailSubsetRow)
                .filter_by(source_id=source_id, sentinel_status="ready")
                .first()
            )
            bootstrap_complete = ready_subset is not None

        result = await check_readiness(
            deployment_path,
            segments,
            arcade_client,
            db,
            thresholds=thresholds,
            bootstrap_complete=bootstrap_complete,
        )

        if not result.overall_ready:
            logger.error("cycle_readiness_gate_failed", source_id=str(source_id), segments=segments)
            print(f"Error: Readiness gate failed for source {source_id}", file=sys.stderr)
            sys.exit(1)

        # (2) Adapter pull
        from src.ingestion.pipeline import IngestionPipeline
        from src.ingestion.adapter_base import AdapterAuthError, AdapterFatalError

        pipeline = IngestionPipeline(db)
        skip_triage = False
        run_id = None

        try:
            run_id = await pipeline.run(
                source_id,
                dry_run=args.dry_run,
                limit=args.limit,
            )
            logger.info("cycle_pull_complete", run_id=str(run_id))
        except (AdapterAuthError, AdapterFatalError) as exc:
            logger.error("cycle_pull_failed_skip_triage", error=str(exc))
            skip_triage = True
        except Exception as exc:
            logger.error("cycle_pull_failed", error=str(exc))
            sys.exit(1)

        # Check if pipeline set paused (SIGTERM during pull)
        if pipeline._shutdown_requested:
            logger.info("cycle_sigterm_during_pull")
            return

        # (3) Triage pipeline (skipped on auth/fatal adapter errors)
        if not skip_triage:
            tier_strs = [t.strip() for t in args.tiers.split(",")]
            tiers_int = tuple(int(t) for t in tier_strs)

            from src.ingestion.communications.triage.pipeline import TriagePipeline
            triage = TriagePipeline(db)
            processed = await triage.run(
                source_id,
                run_id=run_id,
                tiers=tiers_int,
                dry_run=args.dry_run,
                limit=args.limit,
            )
            logger.info("cycle_triage_complete", processed=processed)

    except SystemExit:
        raise
    except Exception as exc:
        logger.error("cycle_failed", error=str(exc))
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
