"""Ontology Management module Pydantic models and enums."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field, field_validator

from src.ontology.evidence_bundle import EvidenceBundle

_proposal_type_log = structlog.get_logger("ontology.models.proposal_type")


class VersionSource(str, Enum):
    """How this ontology version was created."""

    DISCOVERY = "discovery"
    GUIDED_REVIEW = "guided_review"
    ADAPTIVE_EVOLUTION = "adaptive_evolution"
    MANUAL = "manual"
    # F-44 (validation run, 2026-07-01): connector sync ratifies a child
    # schema whose true provenance is a connector sync, but the enum lacked the
    # value so it was mislabeled 'manual' (the sync_pipeline workaround). This
    # value restores accurate provenance for connector-originated versions.
    CONNECTOR_SYNC = "connector_sync"


# D477: rate-limiter for legacy alias warnings (one per unique value per process).
_warned_aliases: set[str] = set()


class ProposalType(str, Enum):
    """Type of schema change being proposed."""

    ADD_ENTITY_TYPE = "add_entity_type"
    ADD_RELATIONSHIP = "add_relationship"
    ADD_PROPERTY = "add_property"
    SPLIT_TYPE = "split_type"
    MERGE_TYPES = "merge_types"
    DEPRECATE_TYPE = "deprecate_type"
    MOVE_HIERARCHY = "move_hierarchy"
    ADD_SYNONYM = "add_synonym"
    MODIFY_PROPERTY = "modify_property"
    CHANGE_DOMAIN_RANGE = "change_domain_range"

    @classmethod
    def _missing_(cls, value: object) -> ProposalType | None:
        """Resolve legacy enum values to canonical members.

        D477: legacy alias tolerance. Lossy mapping — Chunk 76+ DDL migration
        reclassifies definitively. Authorization: D477.

        Existing ``schema_proposals`` rows carrying ``schema_evolution``
        would otherwise 500 on deserialization. This override maps known legacy
        values to canonical members with rate-limited warning + OTel counter.
        """
        if not isinstance(value, str):
            return None
        canonical = _PROPOSAL_TYPE_LEGACY_ALIASES.get(value)
        if canonical is None:
            return None

        # Resolve string to actual member
        resolved = cls(canonical)

        # Rate-limited structlog warning (one per unique value per process)
        if value not in _warned_aliases:
            _warned_aliases.add(value)
            _proposal_type_log.warning(
                "proposal_type.legacy_alias_resolved",
                legacy_value=value,
                resolved_to=resolved.value,
            )

        # OTel counter (best-effort)
        try:
            from src.analytics.metrics import grace_proposal_type_legacy_alias_resolved_total
            grace_proposal_type_legacy_alias_resolved_total.add(1, {"legacy_value": value})
        except Exception:  # noqa: BLE001
            pass

        return resolved


# D477: legacy alias tolerance. Lossy mapping — Chunk 76+ DDL migration
# reclassifies definitively. Authorization: D477.
_PROPOSAL_TYPE_LEGACY_ALIASES: dict[str, str] = {
    "schema_evolution": "modify_property",
}


class ProposalStatus(str, Enum):
    """Current status of a schema proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    APPLIED = "applied"
    COOLING = "cooling"
    REVERTED = "reverted"


class ProposalPriority(str, Enum):
    """Priority level for a schema proposal."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class HumanDecision(str, Enum):
    """Human reviewer's decision on a proposal."""

    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    DEFERRED = "deferred"


class SignalType(str, Enum):
    """Which Adaptive Evolution signal triggered a proposal."""

    SIGNAL_A = "signal_a"  # Extraction failures
    SIGNAL_B = "signal_b"  # Relationship gaps
    SIGNAL_C = "signal_c"  # Type drift
    SIGNAL_D = "signal_d"  # Deprecation signals
    SIGNAL_E = "signal_e"  # Misplaced properties
    SIGNAL_F = "signal_f"  # CQ-driven gaps
    HUMAN_INITIATED = "human_initiated"


# --- Tier classification ---

TIER_MAP: dict[ProposalType, int] = {
    ProposalType.ADD_PROPERTY: 1,
    ProposalType.ADD_SYNONYM: 1,
    ProposalType.ADD_ENTITY_TYPE: 2,
    ProposalType.ADD_RELATIONSHIP: 2,
    ProposalType.MODIFY_PROPERTY: 2,
    ProposalType.SPLIT_TYPE: 3,
    ProposalType.MERGE_TYPES: 3,
    ProposalType.DEPRECATE_TYPE: 3,
    ProposalType.MOVE_HIERARCHY: 3,
    ProposalType.CHANGE_DOMAIN_RANGE: 3,
}


def classify_tier(proposal_type: ProposalType) -> int:
    """Return the change tier (1, 2, or 3) for a given proposal type."""
    return TIER_MAP.get(proposal_type, 2)


# --- Pydantic models ---


class OntologyVersion(BaseModel):
    """A ratified, immutable ontology schema version."""

    id: UUID = Field(default_factory=uuid4)
    version_number: int = Field(description="Auto-incrementing human-readable version (1, 2, 3...)")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="ISO-8601 timestamp",
    )
    schema_json: dict = Field(
        description="Full JSON Schema snapshot generated from Pydantic model_json_schema()"
    )
    schema_modules: dict = Field(
        description="Per-module partition. Keys=module names, values=module JSON Schema subset"
    )
    patch_json: list[dict] | None = Field(
        default=None,
        description="RFC 6902 JSON Patch from previous version. NULL for v1.",
    )
    diff_summary: dict | None = Field(
        default=None,
        description="OM4OV-style: {remain: [...], add: [...], update: [...], delete: [...]}",
    )
    previous_version_id: UUID | None = Field(
        default=None, description="FK to predecessor. NULL for v1."
    )
    hash_chain: str = Field(
        description="SHA-256 of (schema_json + previous_hash). Tamper evidence."
    )
    source: VersionSource = Field(description="How this version was created")
    proposal_id: UUID | None = Field(
        default=None,
        description="FK to schema_proposals. NULL for Discovery-originated.",
    )
    reviewer: str | None = Field(
        default=None, description="Who approved. NULL for autonomous changes."
    )
    changelog: str | None = Field(
        default=None, description="Human-readable description of changes"
    )
    kgcl_commands: list[str] | None = Field(
        default=None, description="KGCL command strings describing changes"
    )
    cq_coverage_snapshot: dict | None = Field(
        default=None,
        description="{total_cqs, passing, failing, out_of_scope}",
    )
    entity_type_count: int | None = Field(
        default=None, description="Denormalized count of entity types"
    )
    relationship_type_count: int | None = Field(
        default=None, description="Denormalized count of relationship types"
    )
    promotion_gate_passed: bool | None = Field(
        default=None,
        description="Whether non-regression gate passed. NULL for v1.",
    )
    promotion_gate_details: dict | None = Field(
        default=None,
        description="{cq_pass_rate, mine1_retention, gate_passed}",
    )
    is_active: bool = Field(
        default=True, description="Whether this is the current production version"
    )
    metadata_extra: dict = Field(
        default_factory=dict, description="Extensibility field"
    )


class SchemaProposal(BaseModel):
    """A proposed schema change from Adaptive Evolution or human initiation."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    proposal_type: ProposalType = Field(description="Type of schema change")
    change_tier: int = Field(
        ge=1, le=3,
        description="1=low risk, 2=medium, 3=high. Auto-classified from proposal_type.",
    )
    kgcl_command: str = Field(description="KGCL syntax describing the proposed change")
    proposed_diff: dict = Field(
        description="JSON diff that would be applied if approved"
    )
    evidence: "EvidenceBundle" = Field(
        description="Typed evidence bundle (D388): signal provenance, affected types, optional NL summary",
    )
    signal_type: SignalType | None = Field(
        default=None, description="Which signal triggered this"
    )
    # F-0042 / ISS-0053 deferral closure (2026-07-03): was `float` with the
    # documented "Human-initiated=1.0" sentinel — a fabricated numeric
    # confidence forbidden by D120/D217 discipline. Human-initiated /
    # signal-less proposals now carry None (migration r4a_raw_confidence_nullable).
    raw_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Agent confidence for signal-backed proposals. None for "
            "human-initiated / signal-less proposals — never fabricated "
            "(D120/D217, F-0042/ISS-0053)."
        ),
    )
    priority: ProposalPriority = Field(default=ProposalPriority.MEDIUM)
    status: ProposalStatus = Field(default=ProposalStatus.PENDING)
    current_schema_version_id: UUID = Field(
        description="FK to ontology_versions. Schema this proposal is against."
    )
    reviewed_at: datetime | None = Field(default=None)
    reviewer: str | None = Field(
        default=None, description="Who reviewed. 'system:autonomy' for autonomous."
    )
    human_decision: HumanDecision | None = Field(default=None)
    modification_distance: float | None = Field(
        default=None, ge=0.0, le=1.0, description="0.0=as-is, 1.0=rewrite"
    )
    modified_diff: dict | None = Field(
        default=None,
        description="Actual diff applied if human modified the proposal",
    )
    applied_autonomously: bool = Field(default=False)
    autonomy_confidence_at_time: float | None = Field(
        default=None, description="Autonomy threshold at application time"
    )
    trust_score_at_time: float | None = Field(
        default=None, description="Rolling trust score at application time"
    )
    resulting_version_id: UUID | None = Field(
        default=None,
        description="FK to ontology_versions created by this proposal",
    )
    cooling_period_expires_at: datetime | None = Field(
        default=None,
        description="48-hour cooling period end for autonomous changes",
    )
    cooling_period_reverted: bool | None = Field(default=None)
    # Chunk 50 (D399) — cooling-period state fields.
    cooling_outcome: str | None = Field(
        default=None,
        description="Cooling resolution: confirmed, auto_finalized, or reverted",
    )
    reverted_at: datetime | None = Field(default=None)
    reverted_by: str | None = Field(default=None)
    reverted_proposal_id: str | None = Field(
        default=None, description="UUID of the inverse proposal that reverted this one",
    )
    metadata_extra: dict = Field(default_factory=dict)
    # Chunk 47 (D387) — additive columns for signal→proposal pipeline.
    ontology_module: str | None = Field(
        default=None, description="Ontology module scope for this proposal",
    )
    dedup_hash: str | None = Field(
        default=None, description="SHA-256 of kgcl_command + ontology_module for dedup",
    )
    overflow: bool = Field(
        default=False, description="Queue depth exceeded soft cap at generation time",
    )
    generated_at: datetime | None = Field(
        default=None, description="Timestamp of generator run",
    )


class CalibrationRecord(BaseModel):
    """Periodic calibration snapshot for earned autonomy tracking."""

    id: UUID = Field(default_factory=uuid4)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    change_tier: int = Field(ge=1, le=3)
    confidence_band_low: float = Field(ge=0.0, le=1.0)
    confidence_band_high: float = Field(ge=0.0, le=1.0)
    approval_rate: float = Field(
        ge=0.0, le=1.0, description="Percentage approved in this band"
    )
    sample_count: int = Field(ge=0, description="Number of reviewed proposals in this band")
    trust_score: float = Field(
        ge=0.0, le=1.0,
        description="Rolling agreement rate over last N proposals",
    )
    autonomy_threshold: float = Field(
        ge=0.0, le=1.0,
        description="Current min confidence for autonomous application",
    )
    autonomy_enabled: bool = Field(default=False)
    window_size: int = Field(
        default=50, description="Number of recent proposals in rolling window"
    )
    risk_tolerance: float = Field(
        default=0.95, description="Human-configured acceptable approval rate"
    )


class SchemaPromotionEvent(BaseModel):
    """Non-regression quality gate result before schema promotion."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    proposal_id: UUID = Field(description="FK to schema_proposals")
    schema_version_before_id: UUID = Field(
        description="FK to current production version at gate time"
    )
    proposed_schema_json: dict = Field(
        description="The proposed schema being tested"
    )
    cq_pass_rate: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Target >= 0.90"
    )
    cq_total: int | None = Field(default=None)
    cq_passing: int | None = Field(default=None)
    mine1_retention: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Target >= 0.70"
    )
    mine1_sample_size: int | None = Field(default=None)
    gate_passed: bool = Field(
        description="Whether both thresholds met"
    )
    gate_details: dict | None = Field(
        default=None,
        description="Full results: per-CQ, per-doc scores, failure reasons",
    )


# --- Chunk 49 Earned Autonomy Calibration models (D394–D397) ---


class CalibrationDecision(BaseModel):
    """A single human decision on a proposal, recorded for calibration tracking."""

    proposal_id: UUID = Field(description="FK to schema_proposals")
    change_tier: int = Field(ge=1, le=3, description="Tier of the change (1=low, 2=medium, 3=high)")
    raw_confidence: float = Field(ge=0.0, le=1.0, description="Agent confidence at proposal time")
    decision: Literal["approved", "rejected"] = Field(description="Human decision outcome")
    modification_distance: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="0.0=as-is, 1.0=rewrite. Null for non-modified decisions.",
    )
    ontology_module: str | None = Field(default=None, description="Ontology module scope")
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the decision was recorded",
    )

    @field_validator("change_tier")
    @classmethod
    def _tier_range(cls, v: int) -> int:
        if v not in (1, 2, 3):
            raise ValueError("change_tier must be 1, 2, or 3")
        return v


class TrustScore(BaseModel):
    """Per-tier trust score and configuration for earned autonomy."""

    tier: int = Field(ge=1, le=3, description="Change tier (1, 2, or 3)")
    trust_score: float = Field(default=0.0, description="Rolling-window approval rate")
    autonomy_threshold: float = Field(default=0.95, description="Min confidence for autonomous application")
    autonomy_enabled: bool = Field(default=False, description="Whether autonomous application is enabled")
    window_size: int = Field(default=50, description="Rolling window size")
    min_reviews_for_calibration: int = Field(default=50, description="Minimum decisions before calibration")
    risk_tolerance: float = Field(default=0.95, description="Human-configured acceptable approval rate")
    total_decisions: int = Field(default=0, description="Total decisions recorded for this tier")
    regression_detected: bool = Field(default=False, description="Whether calibration regression was detected")
    last_computed_at: datetime | None = Field(default=None, description="Last updater run timestamp")


class CalibrationBand(BaseModel):
    """A single confidence band with its observed approval rate."""

    band_low: float = Field(ge=0.0, le=1.0, description="Lower bound of confidence band")
    band_high: float = Field(ge=0.0, le=1.0, description="Upper bound of confidence band")
    approval_rate: float = Field(ge=0.0, le=1.0, description="Observed approval rate in this band")
    sample_count: int = Field(ge=0, description="Number of decisions in this band")


class TierProgress(BaseModel):
    """Progress toward calibration threshold for a tier."""

    total_decisions: int = Field(ge=0, description="Decisions recorded so far")
    min_reviews_for_calibration: int = Field(ge=1, description="Minimum required")
    progress_label: str = Field(description="Human-readable progress string")


class TierDashboard(BaseModel):
    """Per-tier dashboard aggregation."""

    tier: int = Field(ge=1, le=3)
    bands: list[CalibrationBand] = Field(default_factory=list)
    trust_indicator: Literal["high", "building", "insufficient"] = Field(
        description="Three-band trust label",
    )
    progress: TierProgress
    trust_score_state: TrustScore


class CalibrationDashboard(BaseModel):
    """Aggregated calibration dashboard response."""

    tiers: list[TierDashboard] = Field(description="Per-tier dashboard data")
