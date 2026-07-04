"""Re-triage scheduler CLI — forward-walk previously-filtered communication
events through Tiers 2–4 against the maturing graph (Chunk 59, D421/D438).

CLI entry:
    python -m src.ingestion.communications.retriage run [--dry-run] [--observation-time ISO8601]

D246 mirror: this module MUST NOT import ``fastapi`` or ``apscheduler``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy import text as sa_text

logger = structlog.get_logger("ingestion.retriage")

_PID_PATH = "~/.grace/retriage-scheduler.pid"


# ---------------------------------------------------------------------------
# PID-file guard (D398 pattern, mirrors src/ontology/agent_daemon.py:60–75)
# ---------------------------------------------------------------------------

def _acquire_pid(pid_path: str) -> bool:
    """Write PID file.  Returns False if another scheduler is alive."""
    expanded = os.path.expanduser(pid_path)
    os.makedirs(os.path.dirname(expanded), exist_ok=True)
    if os.path.exists(expanded):
        try:
            with open(expanded) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            logger.warning("retriage_already_running", pid=old_pid)
            return False
        except (OSError, ValueError):
            pass  # Stale PID
    with open(expanded, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_pid(pid_path: str) -> None:
    expanded = os.path.expanduser(pid_path)
    try:
        os.remove(expanded)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Core cycle
# ---------------------------------------------------------------------------

async def _run_cycle(*, dry_run: bool = False) -> dict:
    """Execute one re-triage cycle.

    Returns a summary dict with cycle number and per-outcome counts.
    """
    from src.shared.database import get_session_factory

    Session = get_session_factory()
    session = Session()
    try:
        # --- Cycle counter ---
        row = session.execute(
            sa_text("SELECT MAX(retriage_cycle) FROM communication_events")
        ).fetchone()
        current_cycle = (row[0] or 0) + 1
        logger.info("retriage_cycle_start", cycle=current_cycle, dry_run=dry_run)

        # --- Worklist ---
        worklist = session.execute(
            sa_text(
                "SELECT id, message_id, sender_email, sender_display_name, "
                "recipients_json, subject, body_plain, body_html, "
                "sent_at, received_at, source_id, ontology_module, "
                "attachments_json, in_reply_to, references_json, "
                "thread_id, triage_tier_outcome, raw_headers_json "
                "FROM communication_events "
                "WHERE triage_tier_outcome LIKE 'filtered_%' "
                "AND retriage_state IS DISTINCT FROM 'passed' "
                "AND (retriage_cycle IS NULL OR retriage_cycle < :cycle) "
                "ORDER BY id"
            ),
            {"cycle": current_cycle},
        ).fetchall()

        if not worklist:
            logger.info("retriage_empty_worklist", cycle=current_cycle)
            return {"cycle": current_cycle, "processed": 0, "outcomes": {}}

        # --- Tier bootstrap (once per cycle) ---
        from src.ingestion.communications.triage.config import load_triage_config
        from src.ingestion.communications.triage.tier2_entities import run_tier2
        from src.ingestion.communications.triage.tier3_ontology import (
            build_ontology_embedding_matrix,
            run_tier3_batch,
        )
        from src.ingestion.communications.triage.tier4_llm import run_tier4
        from src.ingestion.models import CommunicationEvent
        from src.shared.llm_provider import get_provider

        config_path = Path(__file__).resolve().parents[3] / "config" / "triage_rules.yaml"
        config = load_triage_config(config_path)

        # Tier 4 provider
        tier4_provider = get_provider()

        # Tier 3 ontology embeddings
        import yaml
        discovery_path = Path(__file__).resolve().parents[3] / "config" / "discovery.yaml"
        disc_config: dict = {}
        if discovery_path.exists():
            with open(discovery_path) as f:
                disc_config = yaml.safe_load(f) or {}
        ollama_base_url = disc_config.get("llm", {}).get(
            "base_url",
            os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        )

        ont_session = Session()
        try:
            ontology_embeddings = await build_ontology_embedding_matrix(
                ont_session, ollama_base_url
            )
        finally:
            ont_session.close()

        # ArcadeDB client for Tier 2
        from src.graph.arcade_client import ArcadeClient
        from src.graph.config import ArcadeConfig

        arcade_config = ArcadeConfig()
        arcade_client = ArcadeClient(arcade_config)

        # --- Per-row re-evaluation ---
        outcomes: dict[str, int] = {}
        processed = 0

        for row in worklist:
            # Parse recipients_json — JSONB comes back as list[dict] or list[str]
            raw_recips = row[4] or []
            if raw_recips and isinstance(raw_recips[0], dict):
                recipients = raw_recips
            else:
                recipients = []

            event = CommunicationEvent(
                event_id=row[0],
                source_id=row[10],
                message_id=row[1],
                sender_email=row[2],
                sender_display_name=row[3],
                recipients=recipients,
                subject=row[5],
                body_plain=row[6],
                body_html=row[7],
                sent_at=row[8],
                received_at=row[9],
                ontology_module=row[11],
                attachments=row[12] or [],
                in_reply_to=row[13],
                references=row[14] or [],
                thread_id=row[15],
                triage_tier_outcome=row[16],
                raw_headers=row[17],
                source_type="mbox",
            )

            # Tier 2
            t2_result = await run_tier2(event, arcade_client)
            if t2_result is not None:
                # Still filtered
                new_state = "still_filtered"
                new_outcome = row[16]  # unchanged
            else:
                # Tier 3
                t3_results = await run_tier3_batch(
                    [event], ontology_embeddings, config.tier3, ollama_base_url
                )
                t3_result = t3_results[0] if t3_results else None

                if t3_result is not None:
                    new_state = "still_filtered"
                    new_outcome = row[16]
                else:
                    # Tier 4
                    t4_result = await run_tier4(event, tier4_provider, config)
                    if t4_result is None:
                        # Full pass
                        new_state = "passed"
                        new_outcome = "passed_to_extraction"
                    else:
                        # Partial pass (T2+T3 OK, T4 filtered)
                        new_state = "still_filtered"
                        new_outcome = row[16]

            # T2+T3 OK but T4 not run would be partial pass
            # (handled above: if T3 passes and T4 fails, still_filtered)

            if not dry_run:
                if new_state == "passed":
                    session.execute(
                        sa_text(
                            "UPDATE communication_events SET "
                            "retriage_state = :state, "
                            "retriage_cycle = :cycle, "
                            "triage_tier_outcome = :outcome "
                            "WHERE id = :id"
                        ),
                        {
                            "state": new_state,
                            "cycle": current_cycle,
                            "outcome": new_outcome,
                            "id": str(row[0]),
                        },
                    )
                else:
                    session.execute(
                        sa_text(
                            "UPDATE communication_events SET "
                            "retriage_state = :state, "
                            "retriage_cycle = :cycle "
                            "WHERE id = :id"
                        ),
                        {
                            "state": new_state,
                            "cycle": current_cycle,
                            "id": str(row[0]),
                        },
                    )

            logger.info(
                "retriage_rescue" if new_state == "passed" else "retriage_still_filtered",
                tier_outcome=new_outcome,
                cycle=current_cycle,
                event_id=str(row[0]),
            )

            outcomes[new_outcome] = outcomes.get(new_outcome, 0) + 1
            processed += 1

        if not dry_run:
            session.commit()

        logger.info(
            "retriage_cycle_complete",
            cycle=current_cycle,
            processed=processed,
            outcomes=outcomes,
        )
        return {"cycle": current_cycle, "processed": processed, "outcomes": outcomes}

    finally:
        session.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = argparse.ArgumentParser(
        description="Re-triage scheduler — forward-walk filtered emails (D421/D438)"
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Execute one re-triage cycle")
    run_parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    run_parser.add_argument(
        "--observation-time",
        type=str,
        default=None,
        help="ISO8601 observation time (informational only)",
    )

    args = parser.parse_args()
    if args.command != "run":
        parser.print_help()
        sys.exit(1)

    if not _acquire_pid(_PID_PATH):
        logger.error("retriage_pid_locked", pid_path=_PID_PATH)
        sys.exit(1)

    try:
        result = asyncio.run(_run_cycle(dry_run=args.dry_run))
        logger.info("retriage_exit", **result)
    finally:
        _release_pid(_PID_PATH)


if __name__ == "__main__":
    main()
