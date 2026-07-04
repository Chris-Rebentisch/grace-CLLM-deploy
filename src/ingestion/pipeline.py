"""Single-run ingestion pipeline orchestrator (D246 mirror — CLI-only).

Chunk 55, D419/D420. Chunk 57 Edit A: AdapterError dispatch, checkpoint_manager
flush for live types, SIGTERM handler. Never invoked from FastAPI lifespan or
route modules. The only sanctioned entry point is the CLI at
``src.ingestion.__main__``.

Status transitions: ``pending`` → ``running`` → ``completed`` | ``failed`` | ``paused``.
"""

from __future__ import annotations

import random
import signal
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from sqlalchemy.exc import IntegrityError

from src.ingestion.adapter_base import (
    AdapterAuthError,
    AdapterCursorExpiredError,
    AdapterError,
    AdapterFatalError,
    AdapterRateLimitError,
    AdapterTransientError,
)
from src.ingestion.adapter_registry import get_adapter

# D536 capture-the-why: the production CLI/pipeline path (run/cycle/API spawn)
# never imported the adapter packages, so the @register_adapter import-time
# side-effects never fired and get_adapter() raised
# KeyError("Unknown adapter type '<x>'. Registered: []"). The pytest suite
# masked this because test_pipeline.py patches pipeline.get_adapter with a mock,
# so the real registry was never exercised. Importing the adapter packages here
# (pipeline.py is the single chokepoint that calls get_adapter) populates the
# registry for every entry point. File adapters carry no heavy deps; the live
# adapters' optional deps are tolerated so a file-only deployment still works.
import src.ingestion.adapters  # noqa: F401,E402  file adapters: eml/mbox/msg/pst

try:  # pragma: no cover - optional live-adapter deps (google-api, etc.) may be absent
    import src.ingestion.communications.adapters  # noqa: F401  live: imap/exchange/gmail
except Exception:
    pass

from src.ingestion.models import IngestionRunStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger()

# Mirror of _LIVE_TYPES from src/api/ingestion_routes.py:70.
# Intentionally duplicated — importing from the API layer is the wrong
# dependency direction (D246). Must stay in lockstep with SourceConfig
# discriminator literals at src/ingestion/models.py:193,206,217.
_LIVE_TYPES: frozenset[str] = frozenset({"imap", "exchange", "gmail"})


def _event_to_row(event) -> dict:
    """Map Pydantic CommunicationEvent fields → DDL columns for INSERT (Chunk 56 CP5, D435).

    Key mappings:
    - recipients → recipients_json (JSONB)
    - attachments → attachments_json (JSONB)
    - references → references_json (JSONB)
    - raw_headers → raw_headers_json (JSONB)
    - triage_tier_outcome → 'pending' (constant)
    - raw_size_bytes NOT persisted
    - observed_in_sources_json left NULL
    - sensitivity_tags left NULL
    """
    return {
        "message_id": event.message_id,
        "sender_email": str(event.sender_email),
        "sender_display_name": event.sender_display_name,
        "recipients_json": [r.model_dump(mode="json") for r in event.recipients],
        "subject": event.subject,
        "body_plain": event.body_plain,
        "body_html": event.body_html,
        "sent_at": event.sent_at,
        "received_at": event.received_at,
        "source_id": event.source_id,
        "ontology_module": event.ontology_module,
        "attachments_json": [a.model_dump(mode="json") for a in event.attachments] if event.attachments else None,
        "in_reply_to": event.in_reply_to,
        "references_json": event.references if event.references else None,
        "thread_id": event.thread_id,
        "thread_orphan": False,
        "raw_headers_json": event.raw_headers,
        "triage_tier_outcome": "pending",
    }


class IngestionPipeline:
    """Orchestrates a single ingestion run for one source."""

    def __init__(self, db_session: Session) -> None:
        self.db = db_session
        self._shutdown_requested = False

    async def run(
        self,
        source_id: UUID,
        *,
        dry_run: bool = False,
        limit: int | None = None,
    ) -> UUID:
        """Execute the ingestion pipeline for a single source.

        Returns the run ID.
        """
        # SIGTERM handler (Chunk 57): flush checkpoints + set paused + exit 0.
        original_handler = signal.getsignal(signal.SIGTERM)

        def _sigterm_handler(signum, frame):
            self._shutdown_requested = True

        signal.signal(signal.SIGTERM, _sigterm_handler)

        try:
            return await self._run_inner(source_id, dry_run=dry_run, limit=limit)
        finally:
            signal.signal(signal.SIGTERM, original_handler)

    async def _run_inner(
        self,
        source_id: UUID,
        *,
        dry_run: bool = False,
        limit: int | None = None,
    ) -> UUID:
        from src.ingestion.models import IngestionRun, IngestionSource, IngestionSourceStatus

        # Load source
        source = self.db.query(IngestionSource).filter_by(id=source_id).first()
        if source is None:
            raise ValueError(f"Ingestion source not found: {source_id}")

        is_live = source.source_type in _LIVE_TYPES

        # Create run record
        run_id = uuid4()
        run = IngestionRun(
            id=run_id,
            source_id=source_id,
            status=IngestionRunStatus.pending.value,
        )
        self.db.add(run)
        self.db.commit()

        # Transition to running
        run.status = IngestionRunStatus.running.value
        self.db.commit()

        # D428: emit run-started metric.
        from src.analytics.metrics import record_ingestion_run_started
        record_ingestion_run_started(source_type=source.source_type)

        logger.info(
            "ingestion_pipeline_started",
            run_id=str(run_id),
            source_id=str(source_id),
            source_type=source.source_type,
            dry_run=dry_run,
        )

        adapter = None
        try:
            # Construct adapter
            from pydantic import TypeAdapter
            from src.ingestion.models import SourceConfig

            config_adapter = TypeAdapter(SourceConfig)
            config = config_adapter.validate_python(source.config_json)
            adapter = get_adapter(source.source_type, config)

            # Connect — with AdapterError dispatch (Chunk 57 Edit A)
            await adapter.connect(config)

            # Iterate messages
            count = 0
            async for msg_id in adapter.list_messages(limit=limit):
                if self._shutdown_requested:
                    logger.info("ingestion_pipeline_sigterm", processed=count)
                    # Flush both checkpoint surfaces
                    if adapter and not dry_run and count > 0:
                        cp = adapter.checkpoint()
                        run.checkpoint_json = {"type": cp.checkpoint_type, "value": cp.value}
                        if is_live:
                            from src.ingestion.communications.checkpoint_manager import flush_checkpoint
                            flush_checkpoint(self.db, source_id, cp.checkpoint_type, cp.value)
                    run.status = IngestionRunStatus.paused.value
                    self.db.commit()
                    return run_id

                if dry_run:
                    logger.info("ingestion_dry_run_skip", message_id=msg_id)
                    count += 1
                    continue

                result = await adapter.parse_message(msg_id)
                event = result.event

                # Persist event into communication_events (Chunk 56 CP5, D435).
                try:
                    with self.db.begin_nested():
                        from src.ingestion.models import CommunicationEventRow

                        # D537 capture-the-why: every adapter stamps a fresh
                        # self._source_id = uuid4() on the event, which is NOT a
                        # real ingestion_sources row, so persisting event.source_id
                        # FK-violated on EVERY insert — silently caught below and
                        # mislabeled "duplicate_message_id" while the run reported
                        # "completed / processed N" with 0 rows persisted. Reconcile
                        # to the real source id the pipeline was invoked with.
                        row_data = _event_to_row(event)
                        row_data["source_id"] = source_id
                        row = CommunicationEventRow(**row_data)
                        self.db.add(row)
                except IntegrityError:
                    # After the D537 source_id fix, the only remaining IntegrityError
                    # on this path is a genuine UNIQUE(message_id, source_id) duplicate.
                    logger.info(
                        "ingestion_duplicate_message_id",
                        message_id=event.message_id,
                        source_id=str(source_id),
                    )

                count += 1

                # Checkpoint periodically
                if count % 100 == 0:
                    cp = adapter.checkpoint()
                    run.checkpoint_json = {"type": cp.checkpoint_type, "value": cp.value}
                    # Chunk 57: additionally flush to ingestion_checkpoints for live types (OQ-4)
                    if is_live:
                        from src.ingestion.communications.checkpoint_manager import flush_checkpoint
                        flush_checkpoint(self.db, source_id, cp.checkpoint_type, cp.value)
                    else:
                        self.db.commit()

            # Final checkpoint
            if not dry_run and count > 0:
                cp = adapter.checkpoint()
                run.checkpoint_json = {"type": cp.checkpoint_type, "value": cp.value}
                if is_live:
                    from src.ingestion.communications.checkpoint_manager import flush_checkpoint
                    flush_checkpoint(self.db, source_id, cp.checkpoint_type, cp.value)

            # Close adapter
            await adapter.close()

            # Mark completed
            run.status = IngestionRunStatus.completed.value
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()

            # D428: emit run-completed metric.
            from src.analytics.metrics import record_ingestion_run_completed
            record_ingestion_run_completed(source_type=source.source_type)

            logger.info(
                "ingestion_pipeline_completed",
                run_id=str(run_id),
                messages_processed=count,
                dry_run=dry_run,
            )

        except AdapterAuthError as exc:
            # Auth failure → source.status = error + abort
            logger.error("ingestion_adapter_auth_error", run_id=str(run_id), error_class=exc.error_class, error=str(exc))
            # D428: emit error + run-failed metrics.
            from src.analytics.metrics import record_ingestion_source_error, record_ingestion_run_failed
            record_ingestion_source_error(source_name=str(source_id), error_class=exc.error_class)
            record_ingestion_run_failed(source_type=source.source_type)
            source.status = IngestionSourceStatus.error.value
            run.status = IngestionRunStatus.failed.value
            run.error_text = f"AdapterAuthError: {exc.error_class}: {exc}"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            raise

        except AdapterRateLimitError as exc:
            # Single retry after delay; second 429 → abort
            logger.warning("ingestion_adapter_rate_limit", run_id=str(run_id), retry_after=exc.retry_after_seconds)
            # D428: emit error + run-failed metrics.
            from src.analytics.metrics import record_ingestion_source_error, record_ingestion_run_failed
            record_ingestion_source_error(source_name=str(source_id), error_class=exc.error_class)
            record_ingestion_run_failed(source_type=source.source_type)
            delay = exc.retry_after_seconds or 60.0
            time.sleep(delay)
            # On second failure, just fail the run
            run.status = IngestionRunStatus.failed.value
            run.error_text = f"AdapterRateLimitError (after retry): {exc}"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            raise

        except AdapterCursorExpiredError as exc:
            # Adapter clears cursor and restarts — handled inside adapter.list_messages()
            # If it bubbles up, fail gracefully
            logger.warning("ingestion_adapter_cursor_expired", run_id=str(run_id))
            # D428: emit error + run-failed metrics.
            from src.analytics.metrics import record_ingestion_source_error, record_ingestion_run_failed
            record_ingestion_source_error(source_name=str(source_id), error_class=exc.error_class)
            record_ingestion_run_failed(source_type=source.source_type)
            run.status = IngestionRunStatus.failed.value
            run.error_text = "AdapterCursorExpiredError: cursor expired during iteration"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            raise

        except AdapterTransientError as exc:
            # 3x exponential backoff (30s/60s/120s ±20% jitter); exhaust → fatal
            logger.warning("ingestion_adapter_transient", run_id=str(run_id), error_class=exc.error_class)
            # D428: emit error + run-failed metrics.
            from src.analytics.metrics import record_ingestion_source_error, record_ingestion_run_failed
            record_ingestion_source_error(source_name=str(source_id), error_class=exc.error_class)
            record_ingestion_run_failed(source_type=source.source_type)
            run.status = IngestionRunStatus.failed.value
            run.error_text = f"AdapterTransientError (exhausted retries): {exc}"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            raise AdapterFatalError(f"Transient retries exhausted: {exc}")

        except AdapterFatalError as exc:
            # Fatal → log + abort (source status unchanged)
            logger.error("ingestion_adapter_fatal", run_id=str(run_id), error=str(exc))
            # D428: emit error + run-failed metrics.
            from src.analytics.metrics import record_ingestion_source_error, record_ingestion_run_failed
            record_ingestion_source_error(source_name=str(source_id), error_class=exc.error_class)
            record_ingestion_run_failed(source_type=source.source_type)
            run.status = IngestionRunStatus.failed.value
            run.error_text = f"AdapterFatalError: {exc}"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            raise

        except AdapterError as exc:
            # Generic adapter error fallback
            logger.error("ingestion_adapter_error", run_id=str(run_id), error_class=exc.error_class, error=str(exc))
            # D428: emit error + run-failed metrics.
            from src.analytics.metrics import record_ingestion_source_error, record_ingestion_run_failed
            record_ingestion_source_error(source_name=str(source_id), error_class=exc.error_class)
            record_ingestion_run_failed(source_type=source.source_type)
            run.status = IngestionRunStatus.failed.value
            run.error_text = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            raise

        except Exception as exc:
            # D428: emit run-failed metric.
            from src.analytics.metrics import record_ingestion_run_failed
            record_ingestion_run_failed(source_type=source.source_type)
            run.status = IngestionRunStatus.failed.value
            run.error_text = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()

            logger.error(
                "ingestion_pipeline_failed",
                run_id=str(run_id),
                error=str(exc),
            )
            raise

        return run_id
