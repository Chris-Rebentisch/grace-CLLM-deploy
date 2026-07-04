"""D274 hybrid Postgres+ArcadeDB readiness gate for Communication Ingestion.

Chunk 55. Entity counts from ArcadeDB (``Person``, ``Organization`` vertices
with ``ontology_module`` and ``extraction_confidence`` properties — verified at
``src/extraction/graph_writer.py:189–193``). ACCEPTED CQ counts from Postgres
``competency_questions`` table (``domain`` column L136, ``status`` column L141,
``'ACCEPTED'`` literal at ``src/discovery/cq_models.py:38``).

Pure function — no config-file I/O. Thresholds passed as keyword arg by the
route handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from src.ingestion.models import ReadinessResult, ReadinessThresholds, SegmentReadiness

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger()


async def check_readiness(
    deployment_path: str,
    segments: list[str],
    arcade_client,
    db_session: Session,
    *,
    thresholds: ReadinessThresholds,
    bootstrap_complete: bool = True,
) -> ReadinessResult:
    """Run the D274 hybrid readiness check.

    Args:
        deployment_path: One of "A", "B", "C".
        segments: List of ontology module / segment names.
        arcade_client: ArcadeDB httpx client (``src.graph.arcade_client``).
        db_session: SQLAlchemy session for Postgres CQ reads.
        thresholds: Readiness thresholds.
        bootstrap_complete: For Path B — False skips all DB queries.

    Returns:
        ReadinessResult with per-segment breakdown.
    """
    # Path B bootstrap short-circuit
    if deployment_path == "B" and not bootstrap_complete:
        return ReadinessResult(
            deployment_path=deployment_path,
            segments=[],
            overall_ready=False,
            bootstrap_pending=True,
            thresholds=thresholds,
        )

    if not segments:
        return ReadinessResult(
            deployment_path=deployment_path,
            segments=[],
            overall_ready=True,
            bootstrap_pending=False,
            thresholds=thresholds,
        )

    segment_results: list[SegmentReadiness] = []
    all_ready = True

    for segment in segments:
        # ArcadeDB: entity counts per segment
        person_count = await _count_entities(
            arcade_client, "Person", segment, thresholds.confidence_threshold
        )
        # F-0030 (validation run, 2026-07-03): "Organization" was hardcoded,
        # but ontologies routinely model companies as Legal_Entity only (the
        # triage tier-2 config defaults to ["Person","Organization","Legal_Entity"]
        # for exactly this reason — see CLAUDE.md). With a Legal_Entity ontology,
        # org_count was permanently 0 and the readiness gate could never open.
        # Mirror the tier-2 convention: count the max across both labels.
        org_count = 0
        for _org_label in ("Organization", "Legal_Entity"):
            org_count = max(org_count, await _count_entities(
                arcade_client, _org_label, segment, thresholds.confidence_threshold
            ))

        # Postgres: accepted CQ count per domain = segment
        accepted_cq_count = _count_accepted_cqs(db_session, segment)

        ready = (
            person_count > 0
            and org_count > 0
            and accepted_cq_count >= thresholds.cq_mention_threshold
        )

        guidance = ""
        if not ready:
            parts = []
            if person_count == 0:
                parts.append("No Person entities above confidence threshold")
            if org_count == 0:
                parts.append("No Organization entities above confidence threshold")
            if accepted_cq_count < thresholds.cq_mention_threshold:
                parts.append(
                    f"Only {accepted_cq_count} ACCEPTED CQs (need {thresholds.cq_mention_threshold})"
                )
            guidance = "; ".join(parts)

        if not ready:
            all_ready = False

        segment_results.append(
            SegmentReadiness(
                segment=segment,
                ready=ready,
                person_count=person_count,
                organization_count=org_count,
                accepted_cq_count=accepted_cq_count,
                guidance=guidance,
            )
        )

    return ReadinessResult(
        deployment_path=deployment_path,
        segments=segment_results,
        overall_ready=all_ready,
        bootstrap_pending=False,
        thresholds=thresholds,
    )


async def _count_entities(
    arcade_client,
    entity_label: str,
    segment: str,
    confidence_threshold: float,
) -> int:
    """Count entities of a given type in ArcadeDB per segment.

    Uses property names ``ontology_module`` and ``extraction_confidence``
    verified at ``src/extraction/graph_writer.py:189–193``.
    """
    query = (
        f"MATCH (e:{entity_label}) "
        f"WHERE e.ontology_module = $segment "
        f"AND e.extraction_confidence >= $confidence_threshold "
        f"RETURN count(e) AS entity_count"
    )
    try:
        # Fix: .query does not exist on ArcadeClient — use execute_cypher (Chunk 56 CP5).
        result = await arcade_client.execute_cypher(
            query,
            params={
                "segment": segment,
                "confidence_threshold": confidence_threshold,
            },
        )
        # execute_cypher returns dict with "result" key (graph_read_ops.py:230 pattern)
        rows = result.get("result", []) if isinstance(result, dict) else result or []
        if rows and len(rows) > 0:
            row = rows[0]
            if isinstance(row, dict):
                return row.get("entity_count", 0)
            return 0
        return 0
    except Exception:
        logger.warning(
            "readiness_arcade_query_failed",
            entity_label=entity_label,
            segment=segment,
        )
        return 0


def _count_accepted_cqs(db_session: Session, segment: str) -> int:
    """Count ACCEPTED competency questions for a segment/domain.

    Uses ``CompetencyQuestionRow`` ORM from ``src/discovery/cq_database.py:127``.
    The ``domain`` column (L136) maps to segment. The ``status`` column (L141)
    stores ``CQStatus`` values; ``'ACCEPTED'`` verified at
    ``src/discovery/cq_models.py:38``. Read-only import — no modifications
    to ``src/discovery/``.
    """
    from sqlalchemy import func, text

    result = db_session.execute(
        text("SELECT count(*) FROM competency_questions WHERE domain = :segment AND status = 'ACCEPTED'"),
        {"segment": segment},
    )
    row = result.scalar()
    return row or 0
