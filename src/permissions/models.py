"""Pydantic v2 models for the Guided Permissions engine (Chunk 42, D331).

Source-of-truth models for:

* ``PermissionMatrix`` — operator-ratified policy artifact persisted in
  ``permission_matrices`` (D331). Hash-chained governance.
* ``RoleCluster`` / ``RoleClusterMember`` / ``AccessRule`` /
  ``VisibilityRule`` / ``SensitivityTag`` — nested matrix components.
* ``EvidenceBundle`` / ``EvidenceSection`` — six-source evidence
  aggregation output (D332).
* ``RoleClusterHypothesisSet`` (discriminated union of
  ``SegmentedHypothesis`` / ``NullHypothesis``) — Leiden + LLM
  hypothesis output (D333).
* ``AllowDeny`` (discriminated union of ``Allow`` / ``Deny``) +
  ``EnforcementReason`` — enforcer return type (D334).
* ``DriftClassification`` / ``DriftBand`` — drift detector output
  (D337).
* ``HypothesisConfidenceBand`` — band labels on hypotheses (D333).

All models ``ConfigDict(extra='forbid')``. Three-layer schema discipline:
Pydantic = source of truth → JSON Schema via ``model_json_schema()`` →
YAML via ``pydantic_yaml.to_yaml_str()`` (D325 pattern).

``SensitivityTag`` is included on ``RoleCluster`` so Chunk 43's
Sensitivity Gate re-renders without forking the data model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ----- Band Literals (D333, D337) -----------------------------------

HypothesisConfidenceBand = Literal["strong", "moderate", "weak"]
"""Band labels surfaced on each ``SegmentedHypothesis``. Bands only;
no numerics in DOM (D120/D217)."""

DriftBand = Literal["high", "medium", "low"]
"""Band labels surfaced on drift classifications. Deliberately a
separate label-space from ``HypothesisConfidenceBand`` (D337)."""


VisibilityMode = Literal[
    "permission_matrix_default",
    "private_to_self",
    "private_to_named_list",
    "scoped_to_role_cluster",
]
"""On-row visibility enum literals. Byte-identical to Chunk 38 stub
(D285 forward-guarantee held)."""


# ----- Sensitivity tag (D331; Chunk 43 re-render carrier) -----------


ComplianceFramework = Literal[
    "iso_27001_2022",
    "fhir_security_label",
    "nist_cui",
    "gdpr_art_9",
    "hipaa_phi",
    "custom",
]
"""Six compliance frameworks supported by the Sensitivity Gate (D342).
The ``custom`` literal is an explicit escape hatch — operators map to
locally-defined codes without forcing a new framework into the union.
"""


class FrameworkMapping(BaseModel):
    """One framework code attached to a ``SensitivityTag`` (D342).

    Carries the framework identifier and a free-form ``code`` string
    (e.g. ISO 27001 control id, FHIR security label code). Pure carrier
    — no admission semantics (D270).
    """

    model_config = ConfigDict(extra="forbid")

    framework: ComplianceFramework = Field(
        description="Compliance framework this code belongs to."
    )
    code: str = Field(
        min_length=1,
        description="Framework-specific code (e.g. 'A.5.13', 'R').",
    )


class SensitivityTag(BaseModel):
    """Tag describing a compliance/sensitivity facet attached to a
    ``RoleCluster`` or ``AccessRule``. Carrier-only at v1 — Chunk 43
    consumes this for the Sensitivity Gate compliance surface.

    ``framework_mappings`` (D342) defaults to an empty list to preserve
    canonical-JSON serialization stability for matrices ratified before
    Chunk 43 (R9 mitigation — empty-list default does not perturb
    existing ``payload_hash`` values).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Tag name, e.g. 'pii', 'finance_restricted'.")
    description: str | None = Field(
        default=None, description="Free-text description of the tag."
    )
    framework_mappings: list[FrameworkMapping] = Field(
        default_factory=list,
        description=(
            "Compliance-framework code mappings (D342). Empty list is the "
            "v1 default and preserves pre-Chunk-43 hash identity."
        ),
    )


# ----- Sensitivity Gate (Chunk 43, D343/D344) -----------------------


class TaggedSubset(BaseModel):
    """Render-only projection of a ``PermissionMatrix``'s cluster
    decisions filtered to those carrying at least one ``SensitivityTag``
    (D343).

    Returned by ``project_tagged_subset`` — never persisted, never used
    for admission. The Sensitivity Gate is a render surface over the
    Chunk 42 Permission Matrix engine (D270 single-engine invariant).
    """

    model_config = ConfigDict(extra="forbid")

    matrix_schema_version: str = Field(
        description="Schema version of the source matrix (passthrough)."
    )
    cluster_decisions: list["TaggedClusterDecision"] = Field(
        default_factory=list,
        description="Closed-list subset of source matrix cluster decisions.",
    )


class TaggedClusterDecision(BaseModel):
    """One row in a ``TaggedSubset`` — a single (cluster, access-rule)
    pair surfaced because the rule carries non-empty
    ``sensitivity_tags``.
    """

    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    cluster_display_name: str
    resource_kind: Literal[
        "ontology_module",
        "segment",
        "change_directive",
        "graph_entity",
        "retrieval_query_event",
    ]
    resource_label: str
    action: Literal["view", "edit", "ratify"]
    decision: Literal["allow", "deny"]
    sensitivity_tags: list[SensitivityTag]


CoverageBand = Literal["high", "medium", "low"]
"""Three-band coverage label for a Sensitivity Classification Report
(D344). Bands only on the wire — ``coverage_score`` is server-side only
per D120/D217.
"""


class TagInventoryEntry(BaseModel):
    """One row of the Sensitivity Classification Report's tag
    inventory: how often a given tag appears across the active matrix's
    rules.
    """

    model_config = ConfigDict(extra="forbid")

    tag_name: str
    rule_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    framework_codes: list[FrameworkMapping] = Field(default_factory=list)


class CoverageBreakdownEntry(BaseModel):
    """One row of the coverage breakdown — share of a (resource_kind,
    action) cell that carries at least one tag.
    """

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal[
        "ontology_module",
        "segment",
        "change_directive",
        "graph_entity",
        "retrieval_query_event",
    ]
    action: Literal["view", "edit", "ratify"]
    total_rule_count: int = Field(ge=0)
    tagged_rule_count: int = Field(ge=0)


class UntaggedRuleEntry(BaseModel):
    """One untagged rule surfaced for operator triage. Capped at 1000
    entries per report (``truncated`` flag on the parent).
    """

    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    cluster_display_name: str
    resource_kind: Literal[
        "ontology_module",
        "segment",
        "change_directive",
        "graph_entity",
        "retrieval_query_event",
    ]
    resource_label: str
    action: Literal["view", "edit", "ratify"]


class TagHygieneFinding(BaseModel):
    """Levenshtein <= 2 near-duplicate-tag finding (D344) — surfaced so
    operators can deduplicate tag names before the next ratification.
    """

    model_config = ConfigDict(extra="forbid")

    tag_name: str
    similar_to: str
    distance: int = Field(ge=0, le=2)


class SensitivityClassificationReport(BaseModel):
    """Render-only report describing the tag coverage of a
    ``PermissionMatrix`` (D344).

    ``coverage_score`` is a server-side band-derivation input that MUST
    NOT appear in any API response body (D120/D217). Read-path response
    models exclude it; the field is retained on this Pydantic class so
    repository code can persist it for future analytics.
    """

    model_config = ConfigDict(extra="forbid")

    report_id: UUID = Field(default_factory=uuid4)
    permission_matrix_id: UUID
    generated_at: datetime
    tag_inventory: list[TagInventoryEntry] = Field(default_factory=list)
    coverage_breakdown: list[CoverageBreakdownEntry] = Field(default_factory=list)
    untagged_rules: list[UntaggedRuleEntry] = Field(default_factory=list)
    truncated: bool = Field(
        default=False,
        description="True when ``untagged_rules`` was capped at 1000 entries.",
    )
    coverage_band: CoverageBand | None = Field(
        default=None,
        description=(
            "Three-band coverage label. None when corpus is below the "
            "tag floor (no tags anywhere on the matrix)."
        ),
    )
    coverage_score: float | None = Field(
        default=None,
        description=(
            "Server-side only — derives ``coverage_band``. Never returned "
            "in API responses (D120/D217)."
        ),
    )
    corpus_below_floor: bool = Field(
        default=False,
        description=(
            "True when the matrix carries zero ``SensitivityTag`` entries "
            "(report still persists, mirrors Chunk 37 DR carve-out)."
        ),
    )
    tag_hygiene_findings: list[TagHygieneFinding] = Field(default_factory=list)


# ----- Matrix building blocks (D331) --------------------------------


class RoleClusterMember(BaseModel):
    """One person assigned to a role-cluster.

    LLM may NOT invent members; members are sourced from Leiden cluster
    output (D333). The ``person_grace_id`` is the graph identifier.
    """

    model_config = ConfigDict(extra="forbid")

    person_grace_id: str = Field(min_length=1)
    display_name: str | None = None
    membership_kind: Literal["primary", "secondary"] = "primary"


class AccessRule(BaseModel):
    """One default-access entry for a (resource_kind, action) pair.

    Resource-kind / action label-space is intentionally narrow at v1 —
    extending it requires a new D-number.

    ``resource_label`` matches at three specificity tiers (F-031 /
    ISS-0013): an exact label match beats a class-level match (label
    equals the resource *kind*, e.g. ``"graph_entity"`` matches any
    graph-entity grace_id) which beats the literal wildcard ``"*"``.
    Class-level and wildcard forms exist so a ratifiable rule can allow
    per-entity resources (retrieval results) under default-deny.
    """

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal[
        "ontology_module",
        "segment",
        "change_directive",
        "graph_entity",
        "retrieval_query_event",
    ]
    resource_label: str = Field(
        description=(
            "Resource identifier (e.g. ontology_module name, segment name, "
            "graph-entity grace_id). Also accepts the class-level form — the "
            "resource_kind itself (matches every instance of that kind) — and "
            "the literal wildcard '*'. Exact > class-level > wildcard "
            "(F-031 / ISS-0013)."
        )
    )
    action: Literal["view", "edit", "ratify"]
    decision: Literal["allow", "deny"]
    sensitivity_tags: list[SensitivityTag] = Field(default_factory=list)


class VisibilityRule(BaseModel):
    """Visibility default applied to artifacts authored by members of a
    role-cluster. Maps to the on-row ``visibility`` enum (D285)."""

    model_config = ConfigDict(extra="forbid")

    artifact_kind: Literal["change_directive"] = "change_directive"
    default_mode: VisibilityMode


class RoleCluster(BaseModel):
    """One role-cluster with members + default access defaults."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(min_length=1)
    display_name: str
    description: str | None = None
    members: list[RoleClusterMember] = Field(default_factory=list)
    access_rules: list[AccessRule] = Field(default_factory=list)
    visibility_rules: list[VisibilityRule] = Field(default_factory=list)
    sensitivity_tags: list[SensitivityTag] = Field(default_factory=list)


class PermissionMatrix(BaseModel):
    """Operator-ratified policy artifact. One ratified matrix is active
    per organization at a time; older matrices remain queryable through
    the hash chain.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0", description="Matrix schema version.")
    role_clusters: list[RoleCluster] = Field(default_factory=list)
    default_decision: Literal["allow", "deny"] = Field(
        default="deny",
        description=(
            "Decision applied when no explicit AccessRule matches. "
            "Default-deny per OWASP A01 / D334."
        ),
    )
    # F-0047b / ISS-0055 Layer 2 (2026-07-03) — capture-the-why: additive
    # OPTIONAL knob (hash-chain-safe: old JSONB payloads lack the key and
    # validate to the default). "deny" preserves today's behavior exactly:
    # any vertex whose tag union carries a forbidden tag is dropped.
    # "evidence_scoped" lets the CP5 post-fetch filter serve vertices whose
    # forbidden tag is INHERITED-partial (clean evidence exists per
    # sensitivity_tag_sources / sensitivity_source_total) with
    # privileged-contributed properties scrubbed; universally-tagged and
    # provenance-less vertices still drop. Anonymous/unresolvable
    # principals ALWAYS get "deny" behavior regardless of this knob
    # (sensitivity_resolver most-restrictive fallback discipline).
    inherited_tag_posture: Literal["deny", "evidence_scoped"] = Field(
        default="deny",
        description=(
            "Enforcement posture for vertices whose forbidden sensitivity "
            "tags are partially inherited (ISS-0055 Layer 2). 'deny' drops "
            "them (legacy behavior); 'evidence_scoped' serves them with "
            "privileged-contributed properties scrubbed."
        ),
    )
    notes: str | None = None


class PermissionMatrixVersion(BaseModel):
    """Persistence envelope returned by the ratify route.

    Mirrors the ``permission_matrices`` row shape, with ``payload_hash``
    and ``previous_hash`` exposed so callers can verify the chain.
    """

    model_config = ConfigDict(extra="forbid")

    permission_matrix_id: UUID
    payload: PermissionMatrix
    payload_hash: str = Field(min_length=64, max_length=64)
    previous_hash: str | None = Field(default=None)
    created_at: datetime
    created_by: str | None = None
    version_label: str | None = None


# ----- Evidence bundle (D332) ---------------------------------------


EvidenceSourceName = Literal[
    "document_authorship",
    "segment_ownership",
    "graph_person_role",
    "change_directive_authorship",
    "signal_combination",
    "communications",
]


class EvidenceSection(BaseModel):
    """One evidence section produced by one of the six D332 sources."""

    model_config = ConfigDict(extra="forbid")

    source: EvidenceSourceName
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Source-shaped rows. The ``communications`` source returns "
            "an empty list at v1 (Phase 7 / D274)."
        ),
    )
    is_empty_placeholder: bool = Field(
        default=False,
        description="True iff this source is a typed-but-empty placeholder.",
    )


class EvidenceBundle(BaseModel):
    """Six-source aggregator output. Re-running is cheap (no LLM in the
    default path)."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID = Field(default_factory=uuid4)
    sections: list[EvidenceSection]


# ----- Hypothesis result (D333) -------------------------------------


class SegmentedHypothesis(BaseModel):
    """One LLM-narrated role-cluster hypothesis.

    LLM only narrates and proposes access defaults; LLM does not pick
    members. Members come from Leiden community detection (D333).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["segmented"] = "segmented"
    cluster: RoleCluster
    confidence_band: HypothesisConfidenceBand
    rationale: str | None = None


class NullHypothesis(BaseModel):
    """Mandatory null hypothesis: 'the data does not support discrete
    role-clusters'. Always present in every ``RoleClusterHypothesisSet``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["null"] = "null"
    rationale: str = Field(
        description=(
            "Why the operator might prefer no-segmentation over the "
            "proposed clusters."
        )
    )


HypothesisItem = Annotated[
    SegmentedHypothesis | NullHypothesis,
    Field(discriminator="kind"),
]


class RoleClusterHypothesisSet(BaseModel):
    """Top-level hypothesis result — one or more ``SegmentedHypothesis``
    plus exactly one mandatory ``NullHypothesis``."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    evidence_id: UUID
    hypotheses: list[HypothesisItem]


# ----- Enforcer return type (D334) ----------------------------------


class EnforcementReason(BaseModel):
    """Structured reason returned with a ``Deny``."""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "no_active_matrix",
        "no_matching_rule",
        "explicit_deny",
        "visibility_private_to_self",
        "visibility_named_list_miss",
        "scope_intersection_empty",
        "default_deny",
    ]
    detail: str | None = None


class Allow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["allow"] = "allow"


class Deny(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["deny"] = "deny"
    reason: EnforcementReason


AllowDeny = Annotated[Allow | Deny, Field(discriminator="decision")]


# ----- Drift detector output (D337) ---------------------------------


class DriftClassification(BaseModel):
    """One drift detector output row."""

    model_config = ConfigDict(extra="forbid")

    person_grace_id: str
    proposed_cluster_id: str | None
    drift_band: DriftBand
    rationale: str | None = None
