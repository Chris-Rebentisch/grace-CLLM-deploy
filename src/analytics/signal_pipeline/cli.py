"""CLI entry for the signal pipeline (D246).

The CLI is the only invocation path. There is no APScheduler / FastAPI
lifespan integration; operators run this on a host cron / launchd.

Usage:
    python -m src.analytics.signal_pipeline run-all [options]

Exit codes:
- 0 — orchestrator status == "success".
- 1 — orchestrator status == "partial_failure".
- 2 — orchestrator status == "error" or unrecoverable error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analytics.signal_pipeline.config import (
    SignalPipelineConfig,
    load_config,
)
from src.analytics.signal_pipeline.orchestrator import (
    make_default_context,
    run_all,
)
from src.analytics.signal_pipeline.signals.signal_a import SignalADetector
from src.analytics.signal_pipeline.signals.signal_b import SignalBDetector
from src.analytics.signal_pipeline.signals.signal_c import SignalCDetector
from src.analytics.signal_pipeline.signals.signal_d import SignalDDetector
from src.analytics.signal_pipeline.signals.signal_e import SignalEDetector
from src.analytics.signal_pipeline.signals.signal_f import SignalFDetector

ALL_DETECTORS = {
    "A": SignalADetector,
    "B": SignalBDetector,
    "C": SignalCDetector,
    "D": SignalDDetector,
    "E": SignalEDetector,
    "F": SignalFDetector,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grace-signal-pipeline",
        description="GrACE signal computation pipeline (Chunk 32).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run-all", help="Run every detector once.")
    p_run.add_argument(
        "--signal", action="append", choices=list(ALL_DETECTORS),
        help="Restrict to one or more signals (default: all six).",
    )
    p_run.add_argument(
        "--ontology-module", action="append", default=None,
        help="Restrict to one or more ontology modules.",
    )
    p_run.add_argument(
        "--config", default=None,
        help="Path to YAML config (default: config/signal_pipeline.yaml).",
    )
    p_run.add_argument(
        "--dry-run", action="store_true",
        help="Compute records but do not persist.",
    )
    p_run.add_argument(
        "--verbose", action="store_true",
        help="Verbose logging.",
    )
    return parser


def _make_session_factory():
    """Build a SQLAlchemy sessionmaker from GraceSettings.database_url."""
    from src.shared.config import get_settings
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


async def _run(args: argparse.Namespace) -> int:
    config: SignalPipelineConfig = load_config(args.config)
    requested = args.signal or list(ALL_DETECTORS)
    detectors = [ALL_DETECTORS[s]() for s in requested]
    session_factory = _make_session_factory()
    ctx = make_default_context(
        config=config,
        session_factory=session_factory,
        target_ontology_modules=args.ontology_module,
    )
    run, records = await run_all(
        detectors,
        context=ctx,
        session_factory=session_factory,
        dry_run=args.dry_run,
    )
    summary = {
        "run_id": str(run.id),
        "status": run.status,
        "records": len(records),
        "dry_run": bool(args.dry_run),
        "signals": sorted({r.signal_type for r in records}),
        # Detectors that no-op'd on missing Prometheus history/baseline — a
        # DISTINCT status from "ran, found nothing" (C1 follow-up: silent no-ops
        # were indistinguishable from a healthy quiet corpus).
        "prerequisites_not_met": ctx.diagnostics.get("prerequisites_not_met", {}),
    }
    print(json.dumps(summary))
    if run.status == "success":
        return 0
    if run.status == "partial_failure":
        return 1
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from src.shared._logging_utils import clamp_http_client_logs
    clamp_http_client_logs()

    if args.command == "run-all":
        try:
            return asyncio.run(_run(args))
        except Exception as exc:  # noqa: BLE001
            logging.exception("signal_pipeline_unrecoverable_error: %s", exc)
            return 2
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
