"""Pydantic models for CQ-driven schema extraction pass output."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


# --- Stage 1: Lightweight type summaries (no properties, no evidence) ---


class Stage1TypeSummary(BaseModel):
    """A lightweight entity type summary from Stage 1 extraction."""

    name: str = Field(
        description="Type name in PascalCase_With_Underscores"
    )
    parent_type: str | None = Field(
        default=None, description="Immediate parent type name"
    )
    description: str = Field(
        description="One-line description of what this type represents"
    )
    display_label: str = Field(
        default="",
        description=(
            "Plain-English, non-technical plural label for a business reader "
            "(e.g. 'Companies & Organizations' for Legal_Entity). No underscores, "
            "no jargon."
        ),
    )
    plain_description: str = Field(
        default="",
        description=(
            "One sentence a non-technical business owner would understand, "
            "explaining what this kind of thing is in their world. No graph or "
            "ontology terminology."
        ),
    )
    example_snippet: str | None = Field(
        default=None,
        description=(
            "A short verbatim quote (<=160 chars) from the supplied documents "
            "showing a real instance of this type. Must be copied from the "
            "document text, not invented."
        ),
    )
    evidence_documents: list[str] = Field(
        default_factory=list,
        description=(
            "Filenames (from the '--- Document: <name> ---' headers in the "
            "supplied text) where this type appears. Up to 5. Used to show the "
            "reviewer how common this type is."
        ),
    )
    domain: str = Field(default="other", description="GrACE domain category")
    answerable_cqs: list[str] = Field(
        default_factory=list, description="CQ IDs this type helps answer"
    )
    seed_alignment: str | None = Field(
        default=None, description="Seed entity type name this aligns with"
    )


class Stage1RelSummary(BaseModel):
    """A lightweight relationship summary from Stage 1 extraction."""

    name: str = Field(description="Relationship name in snake_case")
    source_type: str = Field(description="Source entity type name")
    target_type: str = Field(description="Target entity type name")
    description: str = Field(description="One-line description")
    display_label: str = Field(
        default="",
        description=(
            "Plain-English label for a business reader (e.g. 'is a party to' "
            "for party_to). No snake_case, no jargon."
        ),
    )
    plain_description: str = Field(
        default="",
        description="One non-technical sentence explaining this connection.",
    )
    example_snippet: str | None = Field(
        default=None,
        description="A short verbatim quote (<=160 chars) from the documents showing this connection.",
    )
    answerable_cqs: list[str] = Field(
        default_factory=list, description="CQ IDs this relationship helps answer"
    )
    seed_alignment: str | None = Field(default=None)


class Stage1Output(BaseModel):
    """Complete Stage 1 output from a single pass on a single domain."""

    entity_types: list[Stage1TypeSummary] = Field(default_factory=list)
    relationships: list[Stage1RelSummary] = Field(default_factory=list)


# --- Stage 2 LLM response model (D444) ---


class Stage2Output(BaseModel):
    """Stage 2 extraction detail response — per-entity-type properties and relationships."""
    properties: list[dict] = Field(
        default_factory=list,
        description="Property definitions for the entity type"
    )
    relationships_from_this_type: list[dict] = Field(
        default_factory=list,
        description="Outgoing relationship declarations"
    )
    evidence_documents: list[str] = Field(
        default_factory=list,
        description="Source document identifiers supporting this extraction"
    )


class Stage2BatchTypeDetail(BaseModel):
    """One entity type's detail within a batched Stage-2 response.

    Same payload as Stage2Output but carries ``name`` so several types can be
    detailed in a single LLM call and matched back to the requested types.
    """
    name: str = Field(
        description="Entity type name being detailed (must match a requested type)"
    )
    properties: list[dict] = Field(
        default_factory=list, description="Property definitions for the entity type"
    )
    relationships_from_this_type: list[dict] = Field(
        default_factory=list, description="Outgoing relationship declarations"
    )
    evidence_documents: list[str] = Field(
        default_factory=list,
        description="Source document identifiers supporting this extraction"
    )


class Stage2BatchOutput(BaseModel):
    """Batched Stage-2 response detailing multiple entity types in one LLM call."""
    types: list[Stage2BatchTypeDetail] = Field(
        default_factory=list,
        description="Per-type detail objects, one per requested entity type"
    )


# --- Stage 2 / Full detail models ---


class ProposedProperty(BaseModel):
    """A property proposed for an entity type during schema extraction."""

    name: str = Field(description="Property name in snake_case, e.g. 'effective_date'")
    data_type: str = Field(
        description="Data type: string, datetime, float, boolean, integer, reference"
    )
    description: str = Field(default="", description="What this property represents")
    required: bool = Field(
        default=False, description="Whether this property is required on every instance"
    )
    answerable_cqs: list[str] = Field(
        default_factory=list, description="CQ IDs this property helps answer"
    )


class ProposedEntityType(BaseModel):
    """An entity type proposed by a single extraction pass."""

    name: str = Field(
        description="Type name in PascalCase_With_Underscores, e.g. 'Legal_Entity'"
    )
    parent_type: str | None = Field(
        default=None, description="Immediate parent type name, if hierarchical"
    )
    description: str = Field(
        description="What this entity type represents in the domain"
    )
    display_label: str = Field(
        default="", description="Plain-English plural label for a business reader"
    )
    plain_description: str = Field(
        default="", description="Non-technical one-sentence explanation for a business reader"
    )
    example_snippet: str | None = Field(
        default=None, description="Verbatim example quote from a source document"
    )
    domain: str = Field(
        default="other", description="GrACE domain category from discovery.yaml"
    )
    properties: list[ProposedProperty] = Field(
        default_factory=list, description="Properties of this type"
    )
    answerable_cqs: list[str] = Field(
        default_factory=list, description="CQ IDs this type helps answer"
    )
    evidence_documents: list[str] = Field(
        default_factory=list,
        description="Document filenames where evidence was found",
    )
    seed_alignment: str | None = Field(
        default=None,
        description="Seed entity type name this aligns with, if any",
    )


class ProposedRelationship(BaseModel):
    """A relationship type proposed by a single extraction pass."""

    name: str = Field(
        description="Relationship name in snake_case, e.g. 'covers', 'owned_by'"
    )
    source_type: str = Field(description="Source entity type name (domain)")
    target_type: str = Field(description="Target entity type name (range)")
    description: str = Field(description="What this relationship represents")
    display_label: str = Field(
        default="", description="Plain-English label for a business reader"
    )
    plain_description: str = Field(
        default="", description="Non-technical one-sentence explanation"
    )
    example_snippet: str | None = Field(
        default=None, description="Verbatim example quote from a source document"
    )
    richness_hint: str = Field(
        default="simple",
        description="Preliminary classification: simple, attributed, reified",
    )
    edge_properties: list[ProposedProperty] = Field(
        default_factory=list,
        description="Properties on the edge itself (for attributed/reified relationships)",
    )
    answerable_cqs: list[str] = Field(
        default_factory=list,
        description="CQ IDs this relationship helps answer",
    )
    evidence_documents: list[str] = Field(
        default_factory=list, description="Document filenames with evidence"
    )
    seed_alignment: str | None = Field(
        default=None,
        description="Seed relationship name this aligns with, if any",
    )


class PassOutput(BaseModel):
    """Complete output from a single schema extraction pass on a single domain."""

    pass_name: str = Field(description="Which pass: top_down, bottom_up, middle_out")
    domain: str = Field(description="Which domain this pass ran on")
    entity_types: list[ProposedEntityType] = Field(default_factory=list)
    relationships: list[ProposedRelationship] = Field(default_factory=list)
    total_cq_coverage: float = Field(
        default=0.0,
        description="Percentage of input CQs addressed by at least one proposed type (0.0-1.0)",
    )
    model: str = Field(default="", description="LLM model used")
    duration_ms: int = Field(default=0, description="LLM call duration")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    success: bool = Field(default=True)
    error_message: str = Field(default="")
    raw_response: str = Field(default="", description="Raw LLM output for debugging")


class SchemaExtractionRun(BaseModel):
    """Complete result of running all three schema extraction passes."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    status: str = Field(default="running", description="running, completed, failed")
    model: str = Field(default="")
    provider: str = Field(default="")
    pass_outputs: list[PassOutput] = Field(default_factory=list)
    total_entity_types: int = Field(
        default=0, description="Sum across all passes before merge"
    )
    total_relationships: int = Field(default=0)
    total_duration_ms: int = Field(default=0)
    domains_processed: list[str] = Field(default_factory=list)
    cqs_used: int = Field(
        default=0, description="Number of CQs provided as constraints"
    )
    seed_reference_used: bool = Field(default=False)
    error_message: str = Field(default="")
