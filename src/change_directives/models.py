"""Change Directive Pydantic v2 discriminated-union models (D291).

Two tiers — ``Operational_Adjustment`` and ``Strategic_Initiative`` —
share a common base of ``_CommonDirectiveFields`` and discriminate on
``tier`` per the canonical Pydantic v2 pattern. Three extensibility
hooks are wired in: ``extension_metadata`` (untyped JSONB),
``superseded_by_directive_id`` (typed self-reference), and the
five-state ``DirectiveStatus`` enum referenced by the state machine.

All models use ``ConfigDict(extra="forbid")`` for ingress validation.

The separate ``ChangeDirectivePatchBody`` model is the body shape for
``PATCH /api/change-directives/{directive_id}`` and uses ``extra="forbid"``
over a strict allowlist; ``status``, ``visibility``, ``authored_by``,
``authored_at``, ``directive_id``, ``superseded_by_directive_id`` and the
visibility-modifier fields are all forbidden — preserving the two-writer
property for ``status`` (D292) and the post-INSERT visibility immutability
(Q11 architect lock 2026-05-07).

``CoveringDirective`` is the trimmed wire shape returned via
``find_covering_directives`` (D297) on Gap Report and Divergence Map
responses.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DirectiveStatus(str, Enum):
    """Five-state lifecycle for a Change Directive (D292).

    ``DRAFT`` is set on INSERT; ``ACTIVE``/``REALIZED``/``ABANDONED``/
    ``SUPERSEDED`` are reachable only via ``repository.transition()``.
    Terminal states (``REALIZED``, ``ABANDONED``, ``SUPERSEDED``) carry
    no outgoing transitions.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    REALIZED = "realized"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"


VisibilityMode = Literal[
    "permission_matrix_default",
    "private_to_self",
    "private_to_named_list",
    "scoped_to_role_cluster",
]


class EvidenceCriterion(BaseModel):
    """One evidence criterion attached to a Strategic_Initiative.

    ``natural_language`` is the author-provided NL statement; the LLM
    compile orchestrator (D293) proposes ``compiled_query`` and the
    author finalizes via ``approve``/``edit``/``manual_override`` on
    ``PATCH /{id}/criteria/{criterion_id}``.
    """

    model_config = ConfigDict(extra="forbid")

    criterion_id: UUID
    directive_id: UUID
    natural_language: str
    measurement_kind: str | None = None
    target_value: str | None = None
    target_satisfied_when: str | None = None
    compiled_query: str | None = None
    compilation_status: Literal[
        "proposed", "approved", "manually_authored"
    ] = "proposed"
    error_detail: str | None = None
    created_at: datetime
    updated_at: datetime


class _CommonDirectiveFields(BaseModel):
    """Fields shared by both Change Directive tiers (D291).

    ``visibility``, ``visibility_named_list`` and ``visibility_role_cluster``
    are committed at POST and are immutable post-INSERT (Q11 lock).
    ``superseded_by_directive_id`` is set transactionally when
    ``transition()`` writes ``to_state == SUPERSEDED`` (ADR §11.1 #1).
    """

    model_config = ConfigDict(extra="forbid")

    directive_id: UUID
    title: str = Field(max_length=200)
    description: str
    authored_by: UUID
    authored_at: datetime
    status: DirectiveStatus
    status_updated_at: datetime
    visibility: VisibilityMode = "permission_matrix_default"
    visibility_named_list: list[str] | None = None
    visibility_role_cluster: str | None = None
    affected_segments: list[str] = Field(default_factory=list)
    extension_metadata: dict[str, Any] | None = None
    superseded_by_directive_id: UUID | None = None


class OperationalAdjustment(_CommonDirectiveFields):
    """Tactical, near-term policy or process change.

    Distinguished from ``Strategic_Initiative`` by the ``tier``
    discriminator and the lack of evidence criteria.
    """

    tier: Literal["Operational_Adjustment"]
    effective_date: date | None = None


class StrategicInitiative(_CommonDirectiveFields):
    """Multi-quarter directional change, evidence-anchored (ADR §2).

    Tightens ``affected_segments`` to required (asymmetric required-fields)
    and requires at least one ``EvidenceCriterion`` at authoring time.
    """

    tier: Literal["Strategic_Initiative"]
    target_state_description: str
    realization_horizon: str | None = None
    evidence_criteria: list[EvidenceCriterion] = Field(min_length=1)
    responsible_executive: str | None = None
    affected_segments: list[str] = Field(min_length=1)


ChangeDirective = Annotated[
    OperationalAdjustment | StrategicInitiative,
    Field(discriminator="tier"),
]
"""Discriminated-union Change Directive (D291)."""


class ChangeDirectivePatchBody(BaseModel):
    """Body for ``PATCH /api/change-directives/{directive_id}`` (draft-only).

    ``extra="forbid"`` rejects any non-allowlisted field at the Pydantic
    layer — in particular ``status``, ``visibility``, ``visibility_named_list``,
    ``visibility_role_cluster``, ``authored_by``, ``authored_at``,
    ``directive_id`` and ``superseded_by_directive_id`` (D292 / Q11 lock).
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    affected_segments: list[str] | None = None
    extension_metadata: dict[str, Any] | None = None
    # OA-specific
    effective_date: date | None = None
    # SI-specific
    target_state_description: str | None = None
    realization_horizon: str | None = None
    responsible_executive: str | None = None


VelocityBand = Literal[
    "accelerating", "steady", "slowing", "stalled"
]


class CoveringDirective(BaseModel):
    """Trimmed wire shape returned by ``find_covering_directives`` (D297).

    Optional realization fields (D305) populate when a latest snapshot
    exists for the directive.
    """

    model_config = ConfigDict(extra="forbid")

    directive_id: UUID
    tier: str
    title: str
    status: DirectiveStatus
    authored_at: datetime
    affected_segments: list[str]
    progress_percentage: float | None = None
    velocity_band: VelocityBand | None = None
    is_stalled: bool = False


class CriterionCounterEvidence(BaseModel):
    """OA counter-evidence envelope nested under criterion results (D302)."""

    model_config = ConfigDict(extra="forbid")

    first_seen_at: str | None = None
    last_seen_at: str | None = None
    sample_grace_ids: list[str] = Field(default_factory=list)


class CriterionEvidenceResult(BaseModel):
    """One criterion row inside ``criteria_results`` JSONB (D302)."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: UUID
    satisfied: bool
    measured_value: float | int | None = None
    query_executed_at: datetime
    result_hash: str
    sample_grace_ids: list[str] = Field(default_factory=list)
    counter_evidence: CriterionCounterEvidence | None = None

    @field_validator("query_executed_at", mode="before")
    @classmethod
    def _parse_executed(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            s = v[:-1] + "+00:00" if v.endswith("Z") else v
            return datetime.fromisoformat(s)
        raise TypeError(type(v))


class RealizationSnapshotPayload(BaseModel):
    """Wire shape for snapshot GET routes + ``latest_snapshot`` (Chunk 39).

    Raw ``velocity`` is intentionally omitted from the wire (D305); only
    ``velocity_band`` crosses the API boundary alongside ``progress_percentage``.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    directive_id: UUID
    snapshot_at: datetime
    criteria_results: list[CriterionEvidenceResult]
    progress_percentage: float | None = None
    evidence_count_consistent: int | None = None
    evidence_count_counter: int | None = None
    first_evidence_seen_at: datetime | None = None
    last_counter_evidence_seen_at: datetime | None = None
    criteria_all_satisfied: bool | None = None
    created_at: datetime
    is_stalled: bool = False
    velocity_band: VelocityBand | None = None


class TransitionRequest(BaseModel):
    """Body for ``POST /api/change-directives/{id}/transition`` (D292)."""

    model_config = ConfigDict(extra="forbid")

    to_state: DirectiveStatus
    reason: str | None = None
    superseded_by_directive_id: UUID | None = None


class CriterionCreateRequest(BaseModel):
    """Body for ``POST /api/change-directives/{id}/criteria`` (D293)."""

    model_config = ConfigDict(extra="forbid")

    natural_language: str
    measurement_kind: str | None = None
    target_value: str | None = None
    target_satisfied_when: str | None = None


class CriterionPatchRequest(BaseModel):
    """Body for ``PATCH /{id}/criteria/{criterion_id}`` (D293).

    ``approve`` flips ``compilation_status`` to ``approved``; ``edit`` and
    ``manual_override`` carry a new ``compiled_query`` and flip to
    ``manually_authored``.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "edit", "manual_override"]
    compiled_query: str | None = None


class ChangeDirectiveCreateRequest(BaseModel):
    """Body for ``POST /api/change-directives`` (D295).

    Slim ingress shape: server fills ``directive_id``, ``status``,
    ``authored_at`` and ``status_updated_at``; the optional flagged-from-
    review pointers trigger the dual-emit path (spec §18 #10).
    """

    model_config = ConfigDict(extra="forbid")

    tier: Literal["Operational_Adjustment", "Strategic_Initiative"]
    title: str = Field(max_length=200)
    description: str
    affected_segments: list[str] = Field(default_factory=list)
    visibility: VisibilityMode = "permission_matrix_default"
    visibility_named_list: list[str] | None = None
    visibility_role_cluster: str | None = None
    extension_metadata: dict[str, Any] | None = None
    # OA-specific
    effective_date: date | None = None
    # SI-specific
    target_state_description: str | None = None
    realization_horizon: str | None = None
    responsible_executive: str | None = None
    initial_evidence_criteria: list[str] | None = None
    # Optional flagged-from-review pointers (dual-emit path)
    flagged_from_session_id: UUID | None = None
    flagged_from_element_name: str | None = None
