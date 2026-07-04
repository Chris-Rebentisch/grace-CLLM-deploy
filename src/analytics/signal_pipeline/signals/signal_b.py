"""Signal B — Co-occurrence Without Edge (D241).

Extraction-time (NOT retrieval-time) co-occurrence proxy. For each
``ontology_module``:

1. Find entity pairs that appear in the same ``source_chunk_id`` in
   ``extraction_claims``. (``chunk_source_map`` lives on the in-memory
   ExtractedEntity/Relationship; the persisted-claim equivalent is
   ``source_chunk_id``.)
2. Count "orphan pairs" — pairs with no relationship claim connecting
   them within the window.
3. ``strength = orphan_pairs / total_pairs`` (0 when total_pairs == 0).

D241 explicitly chooses extraction-time co-occurrence over retrieval-time
co-retrieval; do not change this without amending D241.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

import structlog
from sqlalchemy import text

from src.analytics import metrics as grace_metrics
from src.analytics.signal_pipeline.base import (
    SignalDetector,
    SignalRecord,
    SignalRunContext,
)

log = structlog.get_logger(__name__)


_PAIRS_SQL = text(
    """
    WITH entity_in_chunk AS (
        SELECT DISTINCT
            ontology_module,
            source_chunk_id,
            subject_name AS entity_name
        FROM extraction_claims
        WHERE created_at >= :cutoff
          AND ontology_module = :module
          AND entity_type IS NOT NULL
          AND source_chunk_id IS NOT NULL
    ),
    pairs AS (
        SELECT
            a.ontology_module,
            a.entity_name AS subj,
            b.entity_name AS obj
        FROM entity_in_chunk a
        JOIN entity_in_chunk b
          ON a.source_chunk_id = b.source_chunk_id
         AND a.ontology_module = b.ontology_module
         AND a.entity_name < b.entity_name
    ),
    rels AS (
        SELECT DISTINCT subject_name, object_name
        FROM extraction_claims
        WHERE created_at >= :cutoff
          AND ontology_module = :module
          AND relationship_type IS NOT NULL
    )
    SELECT
        p.subj,
        p.obj,
        CASE WHEN r.subject_name IS NULL THEN 1 ELSE 0 END AS is_orphan
    FROM pairs p
    LEFT JOIN rels r
      ON (p.subj = r.subject_name AND p.obj = r.object_name)
      OR (p.obj = r.subject_name AND p.subj = r.object_name)
    """
)


class SignalBDetector(SignalDetector):
    signal_type: ClassVar[str] = "B"

    async def detect(self, run_context: SignalRunContext) -> list[SignalRecord]:
        cfg = run_context.config.signal_b
        if not cfg.enabled:
            return []

        cutoff = datetime.now(UTC) - timedelta(days=max(1, cfg.current_window_days))
        modules = await self._modules(run_context, cutoff)

        records: list[SignalRecord] = []
        now = datetime.now(UTC)
        for module in modules:
            if (
                run_context.target_ontology_modules
                and module not in run_context.target_ontology_modules
            ):
                continue
            session = run_context.session_factory()
            try:
                rows = session.execute(
                    _PAIRS_SQL, {"cutoff": cutoff, "module": module}
                ).all()
            except Exception as exc:  # noqa: BLE001
                log.warning("signal_b.query_failed", module=module, error=str(exc))
                rows = []
            finally:
                session.close()

            total = len(rows)
            orphans = [r for r in rows if r[2] == 1]
            orphan_count = len(orphans)
            strength = (orphan_count / total) if total else 0.0
            strength = max(0.0, min(1.0, strength))

            evidence = {
                "total_pairs": total,
                "orphan_pairs": orphan_count,
                "sample_orphan_pairs": [
                    {"subject": r[0], "object": r[1]} for r in orphans[:10]
                ],
            }

            grace_metrics.signal_b_strength.set(
                strength, attributes={"ontology_module": module}
            )

            records.append(
                SignalRecord(
                    run_id=run_context.run_id,
                    signal_type="B",
                    ontology_module=module,
                    strength=strength,
                    evidence_snapshot=evidence,
                    detected_at=now,
                )
            )

        return records

    async def _modules(
        self, run_context: SignalRunContext, cutoff: datetime
    ) -> list[str]:
        if run_context.target_ontology_modules:
            return list(run_context.target_ontology_modules)
        session = run_context.session_factory()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT DISTINCT ontology_module
                    FROM extraction_claims
                    WHERE created_at >= :cutoff
                      AND ontology_module IS NOT NULL
                    """
                ),
                {"cutoff": cutoff},
            ).all()
            return [r[0] for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("signal_b.modules_failed", error=str(exc))
            return []
        finally:
            session.close()
