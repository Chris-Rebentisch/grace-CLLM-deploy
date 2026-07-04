"""Documented Reality Report generator (Chunk 37, D286/D287).

Two entry points:

* :func:`compute_documented_reality_aggregations` — pure-graph
  aggregation via ArcadeDB OpenCypher reads. No LLM call.
* :func:`generate_documented_reality_report` — pipeline-client report
  generator that wraps aggregations with an opportunity-framed LLM
  narrative via :class:`RegenerationPipeline`. D193 / CF3 hold —
  zero edits to ``src/regeneration/*`` or ``src/retrieval/*``; this
  module imports them as a *client* only.

**Empty-corpus carve-out (R6).** When the total vertex count is below
:attr:`DocumentedRealityConfig.corpus_floor` (default 50), the LLM
call is skipped, ``corpus_below_floor=True``, and the response
``narrative`` is ``None``. The aggregation block is still returned so
the UI can render the "below corpus floor" empty state.

**Forbidden-vocabulary discipline (EC-11).** The system prompt
``DOCUMENTED_REALITY_SYSTEM_PROMPT`` is a fixed mirror-not-accusation
template; it must not contain any
``tests/elicitation/test_ec_constraints.py:_RECON_FORBIDDEN_TOKENS``
substring. EC-11's filesystem scan covers the TS copy registry; the
Python prompt is covered transitively by EC-12 / scan over
``src/analytics/alert_copy.py`` and by manual review.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final, Literal
from uuid import uuid4

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.api.recon_models import (
    DocumentedRealityAggregations,
    DocumentedRealityReportResponse,
)
from src.graph.arcade_client import ArcadeClient

logger = structlog.get_logger()


# Mirror-not-accusation template. No `_RECON_FORBIDDEN_TOKENS` vocabulary.
# This is loaded as a `str`, fed to the regeneration pipeline as the
# system prompt; the pipeline overlays the aggregation block as
# evidence. Opportunity-framed throughout (EC-11/EC-12).
DOCUMENTED_REALITY_SYSTEM_PROMPT: Final[str] = """\
You are summarizing what the evidence in the knowledge graph documents
about the organization's activity. Your task is to mirror the corpus
back as a descriptive narrative — not to evaluate completeness or
suggest what is absent.

Ground every claim in the aggregation data provided. When the data is
sparse or uncertain, say so plainly and frame it as an opportunity to
strengthen evidence. Do not assert what should have been recorded;
describe what has been recorded.

Tone: descriptive, opportunity-framed, evidence-grounded. Length:
follow the prompt's request. Format: prose suitable for a quarterly
review by a non-technical executive.
"""


class DocumentedRealityConfig(BaseSettings):
    """Documented Reality Report generator config (D286)."""

    model_config = SettingsConfigDict(
        env_prefix="DOCUMENTED_REALITY_",
        extra="ignore",
    )

    corpus_floor: int = 50
    """V-count below which the LLM call is skipped (R6)."""


_default_config = DocumentedRealityConfig()


async def compute_documented_reality_aggregations(
    arcade_client: ArcadeClient,
    *,
    evidence_origin: str | None = None,
) -> DocumentedRealityAggregations:
    """Pure-graph aggregation. No LLM call.

    Reads ArcadeDB via the existing ``execute_sql``/``execute_cypher``
    surface and produces a typed aggregation block. When the graph
    is empty or unmaterialized (``Type with name 'V' was not found``),
    returns a zero-filled aggregation with ``total_vertices=0`` so
    the empty-corpus carve-out fires downstream.

    Chunk 59 (D426 — CP7): ``evidence_origin`` filter scopes queries to
    vertices with a matching ``evidence_origin`` property.  ``'both'``
    short-circuits (no filter).  ``None`` = legacy default (no filter).
    """
    total_vertices = 0
    total_edges = 0
    top_entities: list[dict] = []

    # Chunk 59 (D426 — CP7): evidence_origin filter clause.
    # 'both' or None = no filter; otherwise scope via COALESCE (R6).
    origin_where = ""
    if evidence_origin and evidence_origin != "both":
        origin_where = (
            f" WHERE COALESCE(evidence_origin, 'document') = '{evidence_origin}'"
        )

    # Phase-6 fix: ArcadeDB does not expose generic ``V`` / ``E``
    # super-types in SQL (unlike OrientDB). The legacy
    # ``SELECT count(*) FROM V`` query hard-fails with
    # "Type with name 'V' was not found" on every ArcadeDB deployment,
    # which the old except-clause then silently misclassified as
    # "graph unpopulated" → ``total_vertices=0``. The DR pipeline was
    # functionally broken on ArcadeDB. Instead, enumerate types via
    # ``schema:types`` and sum per-type counts.

    async def _enumerate_types() -> tuple[list[str], list[str]]:
        try:
            schema_resp = await arcade_client.execute_sql(
                "SELECT name, type FROM schema:types"
            )
            schema_rows = (
                schema_resp.get("result", [])
                if isinstance(schema_resp, dict)
                else []
            )
            vtypes = [
                r["name"]
                for r in schema_rows
                if isinstance(r, dict)
                and r.get("type") in ("v", "vertex")
            ]
            etypes = [
                r["name"]
                for r in schema_rows
                if isinstance(r, dict)
                and r.get("type") in ("e", "edge")
            ]
            return vtypes, etypes
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "documented_reality.schema_enum_failed", error=str(exc)
            )
            return [], []

    vertex_types, edge_types = await _enumerate_types()

    # Quote identifiers so we can survive types whose name contains a
    # reserved word; ArcadeDB tolerates backticks in FROM.
    def _q(name: str) -> str:
        return f"`{name}`"

    for vtype in vertex_types:
        try:
            v_result = await arcade_client.execute_sql(
                f"SELECT count(*) AS cnt FROM {_q(vtype)}{origin_where}"
            )
            rows = (
                v_result.get("result", [])
                if isinstance(v_result, dict)
                else []
            )
            if rows and isinstance(rows[0], dict):
                cnt = int(rows[0].get("cnt", 0) or 0)
                total_vertices += cnt
                top_entities.append({"type_name": vtype, "count": cnt})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "documented_reality.v_count_failed",
                vtype=vtype,
                error=str(exc),
            )

    # Trim to top-10 by count, mirroring the legacy LIMIT 10 ordering.
    top_entities.sort(key=lambda d: d["count"], reverse=True)
    top_entities = top_entities[:10]

    for etype in edge_types:
        try:
            e_result = await arcade_client.execute_sql(
                f"SELECT count(*) AS cnt FROM {_q(etype)}{origin_where}"
            )
            rows = (
                e_result.get("result", [])
                if isinstance(e_result, dict)
                else []
            )
            if rows and isinstance(rows[0], dict):
                total_edges += int(rows[0].get("cnt", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "documented_reality.e_count_failed",
                etype=etype,
                error=str(exc),
            )

    return DocumentedRealityAggregations(
        top_entities=top_entities,
        top_relationships=[],
        legal_entities=[],
        monetary_flow={},
        participants=[],
        business_activity_signature={},
        total_vertices=total_vertices,
        total_edges=total_edges,
    )


async def generate_documented_reality_report(
    aggregations: DocumentedRealityAggregations,
    retrieval_pipeline,
    regeneration_pipeline,
    trigger: Literal["scheduled", "on_demand"],
    config: DocumentedRealityConfig | None = None,
) -> DocumentedRealityReportResponse:
    """Construct a Documented Reality Report (D286).

    When the corpus is below ``config.corpus_floor``, returns
    aggregation-only with ``corpus_below_floor=True`` and
    ``narrative=None``. Otherwise, calls the regeneration pipeline as
    a client to produce the prose narrative.

    The ``retrieval_pipeline`` and ``regeneration_pipeline`` arguments
    accept ``None`` (test-friendly) or any object exposing the relevant
    interface. When either is ``None`` and corpus is above the floor,
    the function still returns a structurally valid response with a
    minimal placeholder narrative — production callers should supply
    the real pipelines.
    """
    cfg = config or _default_config
    corpus_below_floor = aggregations.total_vertices < cfg.corpus_floor

    narrative: str | None = None
    if corpus_below_floor:
        narrative = None
    elif regeneration_pipeline is None:
        narrative = (
            "Aggregation-only summary: pipeline client not supplied. "
            "Top entity types: "
            + ", ".join(
                str(e.get("type_name")) for e in aggregations.top_entities[:5]
            )
            + "."
        )
    else:
        try:
            from src.regeneration.regeneration_models import (  # type: ignore
                RegenerationQuery,
            )

            query = RegenerationQuery(
                query_text=DOCUMENTED_REALITY_SYSTEM_PROMPT,
            )
            result = await regeneration_pipeline.query(query)
            narrative = getattr(result, "regenerated_text", None) or str(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "documented_reality.pipeline_call_failed",
                error=str(exc),
            )
            narrative = None

    return DocumentedRealityReportResponse(
        report_id=uuid4(),
        trigger=trigger,
        corpus_below_floor=corpus_below_floor,
        aggregations=aggregations,
        narrative=narrative,
        generated_at=datetime.now(timezone.utc),
    )


async def run_scheduled_report(
    arcade_client: ArcadeClient,
    retrieval_pipeline=None,
    regeneration_pipeline=None,
    config: DocumentedRealityConfig | None = None,
) -> DocumentedRealityReportResponse:
    """APScheduler entry point (D287).

    Calls the aggregator + generator with ``trigger="scheduled"`` and
    increments the documented-reality counter. Telemetry emission is
    best-effort.
    """
    from src.analytics.metrics import (
        recon_documented_reality_report_generated_total,
    )

    aggregations = await compute_documented_reality_aggregations(arcade_client)
    try:
        response = await generate_documented_reality_report(
            aggregations=aggregations,
            retrieval_pipeline=retrieval_pipeline,
            regeneration_pipeline=regeneration_pipeline,
            trigger="scheduled",
            config=config,
        )
        recon_documented_reality_report_generated_total.add(
            1, {"trigger": "scheduled", "outcome": "success"}
        )
        return response
    except Exception:
        recon_documented_reality_report_generated_total.add(
            1, {"trigger": "scheduled", "outcome": "error"}
        )
        raise


__all__ = [
    "DOCUMENTED_REALITY_SYSTEM_PROMPT",
    "DocumentedRealityConfig",
    "compute_documented_reality_aggregations",
    "generate_documented_reality_report",
    "run_scheduled_report",
]
