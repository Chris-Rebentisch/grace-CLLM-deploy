"""Triage pipeline orchestrator — hybrid streaming/batch (Chunk 56, D434).

Chunk 57 Edit B: Tier 4 integration, --tiers default all four, SIGTERM → paused.
CLI-only (D246 mirror). Never invoked from FastAPI lifespan or route modules.
The only sanctioned entry point is the CLI at ``src.ingestion.__main__``.
"""

from __future__ import annotations

import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
import yaml

from src.ingestion.communications.triage.config import load_triage_config
from src.ingestion.communications.triage.tier1_noise import run_tier1
from src.ingestion.communications.triage.tier2_entities import run_tier2
from src.ingestion.communications.triage.tier3_ontology import (
    build_ontology_embedding_matrix,
    run_tier3_batch,
)
from src.ingestion.models import (
    CommunicationEvent,
    CommunicationEventRow,
    IngestionRun,
    IngestionRunStatus,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger()


class TriagePipeline:
    """Orchestrates triage evaluation for pending communication events."""

    def __init__(self, db_session: Session) -> None:
        self.db = db_session
        self._shutdown_requested = False

    async def run(
        self,
        source_id: UUID,
        *,
        run_id: UUID | None = None,
        tiers: tuple[int, ...] = (1, 2, 3, 4),
        dry_run: bool = False,
        limit: int | None = None,
    ) -> int:
        """Execute triage pipeline for pending events of a source.

        Returns total events processed.
        """
        # SIGTERM handler: flush checkpoint + exit 0
        original_handler = signal.getsignal(signal.SIGTERM)

        def _sigterm_handler(signum, frame):
            self._shutdown_requested = True

        signal.signal(signal.SIGTERM, _sigterm_handler)

        try:
            return await self._run_inner(source_id, run_id=run_id, tiers=tiers, dry_run=dry_run, limit=limit)
        finally:
            signal.signal(signal.SIGTERM, original_handler)

    async def _run_inner(
        self,
        source_id: UUID,
        *,
        run_id: UUID | None,
        tiers: tuple[int, ...],
        dry_run: bool,
        limit: int | None,
    ) -> int:
        # IngestionRun lifecycle (mirror IngestionPipeline at pipeline.py:57-125)
        if run_id is not None:
            run = self.db.query(IngestionRun).filter_by(id=run_id).first()
            if run is None:
                raise ValueError(f"IngestionRun not found: {run_id}")
        else:
            run_id = uuid4()
            run = IngestionRun(
                id=run_id,
                source_id=source_id,
                status=IngestionRunStatus.pending.value,
            )
            self.db.add(run)
            self.db.commit()

        run.status = IngestionRunStatus.running.value
        self.db.commit()

        logger.info(
            "triage_pipeline_started",
            run_id=str(run_id),
            source_id=str(source_id),
            tiers=tiers,
            dry_run=dry_run,
        )

        # Load config
        config_path = Path(__file__).resolve().parents[4] / "config" / "triage_rules.yaml"
        config = load_triage_config(config_path)

        # Load LLM provider for Tier 4 if needed
        tier4_provider = None
        if 4 in tiers:
            from src.shared.llm_provider import get_provider
            tier4_provider = get_provider()

        # Load ontology embeddings once (Tier 3)
        ontology_embeddings = None
        ollama_base_url = None
        if 3 in tiers:
            # Get ollama base URL from discovery.yaml
            discovery_path = Path(__file__).resolve().parents[4] / "config" / "discovery.yaml"
            with open(discovery_path) as f:
                disc_config = yaml.safe_load(f) or {}
            import os
            ollama_base_url = disc_config.get("llm", {}).get("base_url", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))

            from src.shared.database import get_session_factory
            ont_session = get_session_factory()()
            try:
                ontology_embeddings = await build_ontology_embedding_matrix(
                    ont_session, ollama_base_url
                )
            finally:
                ont_session.close()

        # Query pending events
        from sqlalchemy import text as sa_text

        query = sa_text(
            "SELECT id FROM communication_events "
            "WHERE source_id = :source_id AND triage_tier_outcome = 'pending' "
            "ORDER BY id"
        )
        rows = self.db.execute(query, {"source_id": str(source_id)}).fetchall()
        event_ids = [row[0] for row in rows]

        if limit is not None:
            event_ids = event_ids[:limit]

        # Checkpoint state
        seen_ids: set[str] = set()
        tier1_filtered = 0
        tier2_filtered = 0
        tier3_filtered = 0
        tier3_passed = 0
        tier4_filtered = 0
        tier4_passed = 0
        tier3_batch: list[tuple[UUID, CommunicationEvent]] = []
        processed = 0

        try:
            for event_id in event_ids:
                if self._shutdown_requested:
                    logger.info("triage_pipeline_sigterm", processed=processed)
                    # Chunk 57: set run to paused on SIGTERM
                    run.status = IngestionRunStatus.paused.value
                    run.checkpoint_json = {
                        "last_processed_id": str(event_id),
                        "last_processed_index": processed,
                        "tier1_filtered": tier1_filtered,
                        "tier2_filtered": tier2_filtered,
                        "tier3_filtered": tier3_filtered,
                        "tier3_passed": tier3_passed,
                        "tier4_filtered": tier4_filtered,
                        "tier4_passed": tier4_passed,
                    }
                    self.db.commit()
                    break

                # Load full event row
                event_row = self.db.query(CommunicationEventRow).filter_by(id=event_id).first()
                if event_row is None:
                    continue

                # Reconstruct CommunicationEvent for tier evaluation
                event = self._row_to_event(event_row)

                outcome: str | None = None

                # Tier 1 (streamed)
                if 1 in tiers and outcome is None:
                    _t1_start = time.perf_counter()
                    outcome = run_tier1(event, config.tier1, seen_ids=seen_ids)
                    # D428: per-tier duration metric.
                    from src.analytics.metrics import record_ingestion_triage_duration
                    record_ingestion_triage_duration(tier="1", duration_seconds=time.perf_counter() - _t1_start)
                    if outcome:
                        tier1_filtered += 1

                # Tier 2 (streamed)
                if 2 in tiers and outcome is None:
                    _t2_start = time.perf_counter()
                    # D538 capture-the-why: bare ArcadeClient() uses ArcadeConfig()
                    # whose `database` default is hardcoded "grace" — it ignores the
                    # ARCADE_DATABASE setting/env, so Tier 2 always queried the GOLD
                    # graph (sandbox-isolation hazard + why a non-GOLD registry never
                    # matched). get_arcade_client() builds from settings and honors it.
                    from src.graph.arcade_client import get_arcade_client
                    arcade = get_arcade_client()
                    outcome = await run_tier2(event, arcade, config=config.tier2)
                    from src.analytics.metrics import record_ingestion_triage_duration
                    record_ingestion_triage_duration(tier="2", duration_seconds=time.perf_counter() - _t2_start)
                    if outcome:
                        tier2_filtered += 1

                # Tier 3 (batched)
                if 3 in tiers and outcome is None and ontology_embeddings is not None:
                    tier3_batch.append((event_id, event))

                    if len(tier3_batch) >= config.tier3.batch_size:
                        await self._flush_tier3_batch(
                            tier3_batch, ontology_embeddings, config, ollama_base_url, dry_run,
                            tiers=tiers, tier4_provider=tier4_provider,
                        )
                        tier3_batch = []
                elif outcome is None and 3 not in tiers:
                    # Skip to Tier 4 or mark as passed_to_t4
                    if 4 in tiers and tier4_provider is not None:
                        _t4s_start = time.perf_counter()
                        from src.ingestion.communications.triage.tier4_llm import run_tier4
                        t4_result = await run_tier4(event, tier4_provider, config)
                        from src.analytics.metrics import record_ingestion_triage_duration
                        record_ingestion_triage_duration(tier="4", duration_seconds=time.perf_counter() - _t4s_start)
                        if t4_result is None:
                            outcome = "passed_to_extraction"
                            tier4_passed += 1
                        else:
                            outcome = t4_result
                            tier4_filtered += 1
                    else:
                        outcome = "passed_to_t4"

                # Tier 4 for per-event path (event passed T1-T3 individually)
                if outcome is None and 4 in tiers and tier4_provider is not None:
                    _t4_start = time.perf_counter()
                    from src.ingestion.communications.triage.tier4_llm import run_tier4
                    t4_result = await run_tier4(event, tier4_provider, config)
                    from src.analytics.metrics import record_ingestion_triage_duration
                    record_ingestion_triage_duration(tier="4", duration_seconds=time.perf_counter() - _t4_start)
                    if t4_result is None:
                        outcome = "passed_to_extraction"
                        tier4_passed += 1
                    else:
                        outcome = t4_result
                        tier4_filtered += 1
                elif outcome is None and 4 not in tiers:
                    outcome = "passed_to_t4"

                # Update outcome for non-batched events
                if outcome is not None and not dry_run:
                    self.db.execute(
                        sa_text(
                            "UPDATE communication_events SET triage_tier_outcome = :outcome WHERE id = :id"
                        ),
                        {"outcome": outcome, "id": str(event_id)},
                    )
                    # D428: emit terminal-outcome metric (coarse label).
                    from src.analytics.metrics import (
                        coarse_tier_outcome_for_metric,
                        record_ingestion_email_processed,
                    )
                    try:
                        coarse = coarse_tier_outcome_for_metric(outcome)
                        record_ingestion_email_processed(
                            source_name=str(source_id), tier_outcome=coarse,
                        )
                    except ValueError:
                        pass  # Non-terminal or unknown — skip metric

                seen_ids.add(event.message_id)
                processed += 1

                # Checkpoint periodically
                if processed % 100 == 0:
                    run.checkpoint_json = {
                        "last_processed_id": str(event_id),
                        "last_processed_index": processed,
                        "tier1_filtered": tier1_filtered,
                        "tier2_filtered": tier2_filtered,
                        "tier3_filtered": tier3_filtered,
                        "tier3_passed": tier3_passed,
                        "tier4_filtered": tier4_filtered,
                        "tier4_passed": tier4_passed,
                    }
                    self.db.commit()

            # Flush remaining Tier 3 batch
            if tier3_batch and ontology_embeddings is not None:
                await self._flush_tier3_batch(
                    tier3_batch, ontology_embeddings, config, ollama_base_url, dry_run,
                    tiers=tiers, tier4_provider=tier4_provider,
                )
                tier3_batch = []

            # Optional end-of-run drain: query rows still at passed_to_t4
            if 4 in tiers and tier4_provider is not None and not dry_run:
                drain_query = sa_text(
                    "SELECT id FROM communication_events "
                    "WHERE source_id = :source_id AND triage_tier_outcome = 'passed_to_t4' "
                    "ORDER BY id"
                )
                drain_rows = self.db.execute(drain_query, {"source_id": str(source_id)}).fetchall()
                for (drain_event_id,) in drain_rows:
                    drain_row = self.db.query(CommunicationEventRow).filter_by(id=drain_event_id).first()
                    if drain_row is None:
                        continue
                    drain_event = self._row_to_event(drain_row)
                    from src.ingestion.communications.triage.tier4_llm import run_tier4
                    t4_result = await run_tier4(drain_event, tier4_provider, config)
                    final_outcome = "passed_to_extraction" if t4_result is None else t4_result
                    if t4_result is None:
                        tier4_passed += 1
                    else:
                        tier4_filtered += 1
                    self.db.execute(
                        sa_text("UPDATE communication_events SET triage_tier_outcome = :outcome WHERE id = :id"),
                        {"outcome": final_outcome, "id": str(drain_event_id)},
                    )
                    # D428: emit terminal-outcome metric for drain path (coarse label).
                    from src.analytics.metrics import (
                        coarse_tier_outcome_for_metric,
                        record_ingestion_email_processed,
                    )
                    try:
                        coarse = coarse_tier_outcome_for_metric(final_outcome)
                        record_ingestion_email_processed(
                            source_name=str(source_id), tier_outcome=coarse,
                        )
                    except ValueError:
                        pass
                self.db.commit()

            # Mark completed (unless SIGTERM was received)
            if not self._shutdown_requested:
                run.status = IngestionRunStatus.completed.value
                run.completed_at = datetime.now(timezone.utc)
            run.triage_tier_counts_json = {
                "tier1_filtered": tier1_filtered,
                "tier2_filtered": tier2_filtered,
                "tier3_filtered": tier3_filtered,
                "tier3_passed": tier3_passed,
                "tier4_filtered": tier4_filtered,
                "tier4_passed": tier4_passed,
                "total_processed": processed,
            }
            run.checkpoint_json = {
                "last_processed_id": str(event_ids[-1]) if event_ids else None,
                "last_processed_index": processed,
                "tier1_filtered": tier1_filtered,
                "tier2_filtered": tier2_filtered,
                "tier3_filtered": tier3_filtered,
                "tier3_passed": tier3_passed,
                "tier4_filtered": tier4_filtered,
                "tier4_passed": tier4_passed,
            }
            self.db.commit()

            logger.info(
                "triage_pipeline_completed",
                run_id=str(run_id),
                processed=processed,
                tier1_filtered=tier1_filtered,
                tier2_filtered=tier2_filtered,
                tier3_filtered=tier3_filtered,
                tier4_filtered=tier4_filtered,
                tier4_passed=tier4_passed,
            )

        except Exception as exc:
            run.status = IngestionRunStatus.failed.value
            run.error_text = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            run.triage_tier_counts_json = None
            self.db.commit()
            logger.error("triage_pipeline_failed", run_id=str(run_id), error=str(exc))
            raise

        return processed

    async def _flush_tier3_batch(
        self, batch, ontology_embeddings, config, ollama_base_url, dry_run,
        *, tiers=(1, 2, 3, 4), tier4_provider=None,
    ):
        """Flush a Tier 3 batch through embedding evaluation.

        Chunk 57: when 4 in tiers, run Tier 4 on events that would end at passed_to_t4.
        Do NOT leave batch-flushed events at passed_to_t4 (spec Edit B item b).
        """
        events = [ev for _, ev in batch]
        results = await run_tier3_batch(events, ontology_embeddings, config.tier3, ollama_base_url)

        from sqlalchemy import text as sa_text

        for i, (event_id, ev) in enumerate(batch):
            outcome = results[i]
            if outcome is None:
                # Event passed T3 — run T4 if enabled
                if 4 in tiers and tier4_provider is not None:
                    from src.ingestion.communications.triage.tier4_llm import run_tier4
                    t4_result = await run_tier4(ev, tier4_provider, config)
                    outcome = "passed_to_extraction" if t4_result is None else t4_result
                else:
                    outcome = "passed_to_t4"
            if not dry_run:
                self.db.execute(
                    sa_text(
                        "UPDATE communication_events SET triage_tier_outcome = :outcome WHERE id = :id"
                    ),
                    {"outcome": outcome, "id": str(event_id)},
                )
        self.db.commit()
        return results

    def _row_to_event(self, row: CommunicationEventRow) -> CommunicationEvent:
        """Reconstruct CommunicationEvent from ORM row for tier evaluation."""
        return CommunicationEvent(
            event_id=row.id,
            source_id=row.source_id,
            message_id=row.message_id,
            sender_email=row.sender_email,
            sender_display_name=row.sender_display_name,
            recipients=row.recipients_json or [],
            subject=row.subject,
            body_plain=row.body_plain,
            body_html=row.body_html,
            sent_at=row.sent_at,
            received_at=row.received_at,
            attachments=row.attachments_json or [],
            in_reply_to=row.in_reply_to,
            references=row.references_json or [],
            thread_id=row.thread_id,
            triage_tier_outcome=row.triage_tier_outcome,
            ontology_module=row.ontology_module,
            raw_headers=row.raw_headers_json,
            source_type="mbox",  # Default; actual source_type not stored in communication_events
        )
