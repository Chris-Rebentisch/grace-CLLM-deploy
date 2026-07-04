"""Pydantic models for four-input schema merge pipeline output."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class MergedProperty(BaseModel):
    """A property on a merged entity type or relationship edge."""

    name: str = Field(description="Property name in snake_case")
    data_type: str = Field(
        description="string, datetime, float, boolean, integer, reference"
    )
    description: str = Field(default="")
    required: bool = Field(default=False)
    answerable_cqs: list[str] = Field(
        default_factory=list,
        description="Deduplicated CQ IDs from all passes",
    )
    source_passes: list[str] = Field(
        default_factory=list,
        description="Which passes proposed this property",
    )


class MergedEntityType(BaseModel):
    """A merged entity type from four-input merge (3 passes + seed)."""

    name: str = Field(description="Canonical name resolved by merge")
    alternative_names: list[str] = Field(
        default_factory=list,
        description="Names from other passes that mapped to this type",
    )
    parent_type: str | None = Field(
        default=None, description="Resolved parent in hierarchy"
    )
    description: str = Field(description="Best description from merge")
    display_label: str = Field(
        default="", description="Plain-English plural label for a business reader"
    )
    plain_description: str = Field(
        default="", description="Non-technical one-sentence explanation"
    )
    example_snippet: str | None = Field(
        default=None, description="Verbatim example quote from a source document"
    )
    domain: str = Field(default="other")
    properties: list[MergedProperty] = Field(
        default_factory=list,
        description="Union of properties from all passes",
    )
    provenance: str = Field(
        description="seed+3pass, seed+2pass, seed+1pass, seed_only, 3pass_novel, 2pass_novel, 1pass_only"
    )
    confidence: float = Field(description="0.0-1.0 from agreement scoring")
    source_passes: list[str] = Field(
        default_factory=list,
        description="Which passes proposed this type",
    )
    seed_source: str | None = Field(
        default=None,
        description="Seed ontology name if aligned (fibo, lkif, schema_org, prov_o)",
    )
    seed_type_name: str | None = Field(
        default=None,
        description="Original seed entity type name if aligned",
    )
    answerable_cqs: list[str] = Field(
        default_factory=list,
        description="Deduplicated CQ IDs from all passes",
    )
    evidence_document_count: int = Field(
        default=0, description="Count of unique evidence documents"
    )


class MergedRelationship(BaseModel):
    """A merged relationship type with Edge Property Detection classification."""

    name: str = Field(description="Canonical relationship name")
    alternative_names: list[str] = Field(default_factory=list)
    source_type: str = Field(description="Source entity type name (domain)")
    target_type: str = Field(description="Target entity type name (range)")
    description: str = Field(description="Best description from merge")
    display_label: str = Field(
        default="", description="Plain-English label for a business reader"
    )
    plain_description: str = Field(
        default="", description="Non-technical one-sentence explanation"
    )
    example_snippet: str | None = Field(
        default=None, description="Verbatim example quote from a source document"
    )
    richness_tier: str = Field(
        description="Stage D classification: simple, attributed, reified"
    )
    richness_rationale: str = Field(
        default="", description="Why this tier was assigned"
    )
    edge_properties: list[MergedProperty] = Field(
        default_factory=list,
        description="Edge properties for attributed/reified",
    )
    provenance: str = Field(
        description="Same provenance scheme as entity types"
    )
    confidence: float = Field(description="0.0-1.0")
    source_passes: list[str] = Field(default_factory=list)
    seed_source: str | None = Field(default=None)
    seed_rel_name: str | None = Field(default=None)
    answerable_cqs: list[str] = Field(default_factory=list)


class CQCoverageEntry(BaseModel):
    """Coverage status of a single CQ in the seed schema."""

    cq_id: str = Field(description="CQ UUID (first 8 chars)")
    cq_text: str = Field(description="Canonical CQ text")
    domain: str = Field(default="other")
    covered_by_types: list[str] = Field(
        default_factory=list,
        description="Entity type names that address this CQ",
    )
    covered_by_relationships: list[str] = Field(
        default_factory=list,
        description="Relationship names that address this CQ",
    )
    coverage_status: str = Field(
        description="covered (>=1 type+rel), partial (type OR rel only), uncovered (none)"
    )


class SeedSchema(BaseModel):
    """The complete seed schema output — the proposal for Guided Review."""

    entity_types: list[MergedEntityType] = Field(
        description="All merged entity types"
    )
    relationships: list[MergedRelationship] = Field(
        description="All merged relationships with richness tiers"
    )
    coverage_matrix: list[CQCoverageEntry] = Field(
        description="CQ coverage mapping"
    )
    provenance_summary: dict = Field(
        default_factory=dict,
        description="Count by provenance category",
    )
    quality_metrics: dict = Field(
        default_factory=dict,
        description="Agreement rate, coverage %, type count, etc.",
    )
    gap_report: dict = Field(
        default_factory=dict,
        description="CQs with no coverage, types with no CQ justification",
    )
    extraction_run_id: str = Field(
        default="", description="SchemaExtractionRun ID that produced the pass outputs"
    )
    industry_profile: str = Field(
        default="", description="Industry profile used for seed selection"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SchemaMergeRun(BaseModel):
    """Tracks a single execution of the schema merge pipeline."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    extraction_run_id: str = Field(
        default="", description="The SchemaExtractionRun that produced the input"
    )
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: str = Field(
        default="running", description="running, completed, failed"
    )
    model: str = Field(default="")
    provider: str = Field(default="")
    # Input counts
    input_entity_types: int = Field(
        default=0, description="Total entity types across all passes before merge"
    )
    input_relationships: int = Field(
        default=0, description="Total relationships before merge"
    )
    input_cqs: int = Field(
        default=0, description="Total CQs used for coverage"
    )
    seed_types_count: int = Field(
        default=0, description="Seed entity types available"
    )
    # Output counts
    merged_entity_types: int = Field(default=0)
    merged_relationships: int = Field(default=0)
    # Quality metrics
    cq_coverage_rate: float = Field(
        default=0.0,
        description="Percentage of CQs covered by at least one type",
    )
    cross_pass_agreement_rate: float = Field(
        default=0.0,
        description="Percentage of types from 2+ passes",
    )
    provenance_distribution: dict = Field(default_factory=dict)
    richness_distribution: dict = Field(
        default_factory=dict,
        description="Count by richness tier: simple/attributed/reified",
    )
    # Output storage
    seed_schema_json: dict | None = Field(
        default=None, description="Full SeedSchema as JSON"
    )
    duration_ms: int = Field(default=0)
    error_message: str | None = None
