"""Pydantic response models for the Reconciliation Layer surface (Chunk 36).

Wire-format aliases (D279):
  * Storage column ``erd_score`` → wire field ``evidence_grounding_score``.
  * Storage column ``erd_threshold_n`` → wire field ``evidence_grounding_threshold``.

User-facing surfaces use the ``evidence_grounding`` vocabulary. Internal code,
storage columns, and Prometheus instruments retain the ``erd_*`` prefix for
grep-locality. EC-8 enforces the split with a forbidden-vocabulary scan over
``recon-types.ts`` and ``recon_routes.py`` / ``recon_models.py``.

Mirrored in ``frontend/lib/api/recon-types.ts`` (D283 fourth ``check-api-contract.sh``
target).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.change_directives.models import CoveringDirective


class EmphasizedWithEvidenceItem(BaseModel):
    """Element approved with strong evidence (>=N evidence items viewed)."""

    model_config = ConfigDict(extra="forbid")

    element_name: str
    element_type: str
    instance_count: int
    top_evidence_extraction_event_ids: list[str]


class EmphasizedWithoutEvidenceItem(BaseModel):
    """Element approved but lacking strong evidence in the graph."""

    model_config = ConfigDict(extra="forbid")

    element_name: str
    element_type: str
    instance_count: int
    suggested_actions: list[str]


class UnemphasizedInEvidenceItem(BaseModel):
    """Type present in the graph but not emphasized in the review session."""

    model_config = ConfigDict(extra="forbid")

    element_name: str
    element_type: str
    instance_count: int
    decision_status: str


# Discriminated section union — see CP2 in chunk-36-spec-v1-FINAL.md.
GapReportSection = (
    EmphasizedWithEvidenceItem
    | EmphasizedWithoutEvidenceItem
    | UnemphasizedInEvidenceItem
)


class SourceTypeBreakdown(BaseModel):
    """Document/communication/mixed source-type counts for Gap Reports
    (Chunk 59, D426 — CP7).  Nullable on ``GapReportResponse`` for
    backward-compatible hydration of pre-c59a ``report_json`` rows."""

    model_config = ConfigDict(extra="forbid")

    document: int = 0
    communication: int = 0
    mixed: int = 0


class GapReportResponse(BaseModel):
    """Perception-Evidence Gap Report wire shape (D280).

    User-facing aliasing (D279): the storage columns ``erd_score`` and
    ``erd_threshold_n`` are exposed on the wire as ``evidence_grounding_score``
    and ``evidence_grounding_threshold``. ``populate_by_name=True`` allows
    construction either from the storage names or the wire names.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    session_id: UUID
    reviewer: str
    generated_at: datetime
    evidence_grounding_score: float | None = Field(
        default=None,
        validation_alias="erd_score",
    )
    evidence_grounding_threshold: int = Field(
        validation_alias="erd_threshold_n",
    )
    graph_population_floor_breach: str | None = None
    emphasized_with_evidence: list[EmphasizedWithEvidenceItem] = Field(
        default_factory=list
    )
    emphasized_without_evidence: list[EmphasizedWithoutEvidenceItem] = Field(
        default_factory=list
    )
    unemphasized_in_evidence: list[UnemphasizedInEvidenceItem] = Field(
        default_factory=list
    )
    # Chunk 38 (D297) — Reconciliation Bridge integration. Always
    # present; empty list when no covering directives match. Populated
    # by ``src.api.change_directive_coverage.find_covering_directives``.
    covering_directives: list["CoveringDirective"] = Field(default_factory=list)
    # Chunk 59 (D426 — CP7) — source-type breakdown. Nullable for
    # backward-compatible hydration of pre-c59a report_json rows.
    source_type_breakdown: SourceTypeBreakdown | None = None


class GenerateGapReportRequest(BaseModel):
    """Empty placeholder body for ``POST .../generate``.

    The route accepts an empty JSON body or no body at all. ``force`` is a
    query parameter, not a body field, so this model has no required fields.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Chunk 37 amendments — Cross-Executive Divergence Map (D284 / B4 resolution)
# ---------------------------------------------------------------------------


class DivergenceMapEntry(BaseModel):
    """One per-type entry inside a Divergence Map bucket.

    ``instance_count`` joins ArcadeDB instance counts via
    ``src/ontology/recon_gap_report.py:_read_graph_counts`` (D284 reuse;
    zero edits to that helper).
    """

    model_config = ConfigDict(extra="forbid")

    element_name: str
    element_type: str
    instance_count: int
    # Chunk 59 (D426 — CP7) — evidence source origins.
    # Empty list for pre-c59a rows (backward-compatible).
    source_origins: list[Literal["document", "communication"]] = Field(
        default_factory=list
    )


class DivergenceMapBucket(BaseModel):
    """One of the four Reconciliation buckets (D284 alias dict).

    Bucket vocabulary is fixed: the OM4OV ``compute_entity_level_diff``
    output (`added` / `removed` / `modified` / `unchanged`) is aliased to
    ``additive_B`` / ``additive_A`` / ``contradictory`` / ``consensus``.
    """

    model_config = ConfigDict(extra="forbid")

    bucket_name: Literal[
        "additive_A",
        "additive_B",
        "contradictory",
        "consensus",
    ]
    entries: list[DivergenceMapEntry] = Field(default_factory=list)


class DivergenceMapResponse(BaseModel):
    """Cross-Executive Divergence Map wire shape (D284).

    Persisted in ``recon_divergence_maps`` (migration ``c37c``). The
    GET-latest route ranks by ``generated_at DESC`` for the
    ``(segment_id, reviewer_a, reviewer_b)`` triple.
    """

    model_config = ConfigDict(extra="forbid")

    map_id: UUID
    segment_id: str | None = None
    reviewer_a: str
    reviewer_b: str
    version_a_id: UUID
    version_b_id: UUID
    buckets: list[DivergenceMapBucket]
    generated_at: datetime
    # Chunk 38 (D297) — Reconciliation Bridge integration.
    covering_directives: list["CoveringDirective"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Chunk 37 amendments — Documented Reality Report (D286 / D287)
# ---------------------------------------------------------------------------


class DocumentedRealityAggregations(BaseModel):
    """Pure-graph aggregation output (no LLM).

    Computed by
    ``src.analytics.documented_reality.compute_documented_reality_aggregations``.
    All fields are typed; the regeneration pipeline consumes this model
    as evidence input.
    """

    model_config = ConfigDict(extra="forbid")

    top_entities: list[dict]
    top_relationships: list[dict]
    legal_entities: list[dict]
    monetary_flow: dict
    participants: list[dict]
    business_activity_signature: dict
    total_vertices: int
    total_edges: int


class DocumentedRealityReportResponse(BaseModel):
    """Documented Reality Report wire shape (D286).

    The narrative is ``None`` when ``corpus_below_floor`` is true — the
    empty-corpus carve-out (V count below ``corpus_floor``, default 50)
    skips the LLM call and returns aggregations only (R6 mitigation).
    """

    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    trigger: Literal["scheduled", "on_demand"]
    corpus_below_floor: bool
    aggregations: DocumentedRealityAggregations
    narrative: str | None = None
    generated_at: datetime


class DocumentedRealityScheduleResponse(BaseModel):
    """Schedule row wire shape (D287)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    cadence: Literal["quarterly", "monthly", "on_demand"]
    next_run_at: datetime | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DocumentedRealityScheduleRequest(BaseModel):
    """POST/PATCH body for schedule CRUD (D287)."""

    model_config = ConfigDict(extra="forbid")

    cadence: Literal["quarterly", "monthly", "on_demand"]
    enabled: bool = True


class DocumentedRealityScheduleUpdateRequest(BaseModel):
    """PATCH body — all fields optional (D287)."""

    model_config = ConfigDict(extra="forbid")

    cadence: Literal["quarterly", "monthly", "on_demand"] | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Chunk 37 amendments — Divergence Map request body
# ---------------------------------------------------------------------------


class DivergenceMapGenerateRequest(BaseModel):
    """POST .../divergence-map/generate body (D284)."""

    model_config = ConfigDict(extra="forbid")

    version_a_id: UUID
    version_b_id: UUID
    segment_id: str | None = None


# Resolve forward refs for the additive ``covering_directives`` fields (D297).
GapReportResponse.model_rebuild()
DivergenceMapResponse.model_rebuild()
