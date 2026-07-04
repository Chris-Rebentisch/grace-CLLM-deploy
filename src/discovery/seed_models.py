"""Pydantic models for the dynamic seed registry, provisioning, and parsed output."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


# --- Registry Models ---


class SeedSource(BaseModel):
    """A downloadable ontology source package."""

    id: str = Field(description="Unique source identifier, e.g. 'fibo_legal_entities'")
    name: str = Field(description="Human-readable name")
    source_ontology: str = Field(
        description="Parent ontology family: fibo, lkif, schema_org, prov_o"
    )
    description: str = Field(description="What this source provides")
    download_url: str = Field(description="URL to download the seed file")
    local_path: str = Field(description="Expected path relative to project root")
    file_format: str = Field(description="Format: rdf_xml, owl_xml, turtle")
    version: str = Field(description="Source version identifier")
    domains: list[str] = Field(
        default_factory=list,
        description="GrACE domain categories this source covers",
    )
    industry_verticals: list[str] = Field(
        default_factory=list,
        description="Industry profiles this source is relevant to. Empty = universal.",
    )


class IndustryProfile(BaseModel):
    """Maps an industry to its recommended seed sources."""

    industry_id: str = Field(description="Unique profile identifier")
    name: str = Field(description="Human-readable industry name")
    description: str = Field(
        description="What kinds of organizations this profile serves"
    )
    required_seeds: list[str] = Field(
        description="Seed source IDs always included for this industry"
    )
    recommended_seeds: list[str] = Field(
        default_factory=list,
        description="Seed source IDs suggested but optional",
    )


class SeedRegistry(BaseModel):
    """The full catalog of available seeds and industry mappings."""

    version: str = Field(description="Registry version")
    universal_sources: list[str] = Field(
        description="Seed IDs included for every customer regardless of industry"
    )
    sources: list[SeedSource] = Field(description="All available seed sources")
    industry_profiles: list[IndustryProfile] = Field(
        description="Industry-to-seed mappings"
    )


# --- Parsed Output Models ---


class SeedProperty(BaseModel):
    """A property extracted from a seed ontology."""

    name: str = Field(description="Property name, e.g. 'hasJurisdiction'")
    uri: str = Field(description="Original RDF URI")
    range_type: str = Field(
        description="Property type: 'xsd:string', 'xsd:dateTime', or entity type URI"
    )
    description: str = Field(
        default="", description="rdfs:comment or rdfs:label from the source"
    )


class SeedEntityType(BaseModel):
    """An entity type (class) extracted from a seed ontology."""

    name: str = Field(description="Type name, e.g. 'LegalEntity'")
    source_ontology: str = Field(
        description="Which ontology family: fibo, lkif, schema_org, prov_o"
    )
    source_uri: str = Field(description="Original RDF class URI")
    parent_type: str | None = Field(
        default=None, description="Parent class name if rdfs:subClassOf exists"
    )
    description: str = Field(
        default="",
        description="Class description from rdfs:comment or skos:definition",
    )
    properties: list[SeedProperty] = Field(
        default_factory=list,
        description="Properties with this class as domain",
    )


class SeedRelationship(BaseModel):
    """A relationship (object property) extracted from a seed ontology."""

    name: str = Field(description="Relationship name, e.g. 'isOwnerOf'")
    source_ontology: str = Field(description="Which ontology family")
    source_uri: str = Field(description="Original RDF property URI")
    domain_type: str = Field(description="Source entity type name")
    range_type: str = Field(description="Target entity type name")
    description: str = Field(default="", description="Property description")


class SeedReference(BaseModel):
    """Complete parsed seed reference for LLM consumption in Chunk 7."""

    entity_types: list[SeedEntityType] = Field(
        description="All extracted entity types across all parsed seeds"
    )
    relationships: list[SeedRelationship] = Field(
        description="All extracted relationships"
    )
    source_files: list[str] = Field(description="Which seed files were parsed")
    industry_profile: str = Field(
        description="Which industry profile was selected"
    )
    registry_version: str = Field(
        description="Version of the seed registry used"
    )
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_entity_types: int = Field(
        default=0, description="Count of entity types"
    )
    total_relationships: int = Field(
        default=0, description="Count of relationships"
    )


# --- Provisioning Status Models ---


class SeedStatus(BaseModel):
    """Status of seed provisioning for a single source."""

    source_id: str
    name: str
    is_downloaded: bool = Field(description="File exists at local_path")
    is_parsed: bool = Field(description="Parsed JSON exists in cache")
    local_path: str
    file_size_bytes: int | None = None
    entity_types_count: int | None = None
    relationships_count: int | None = None


class ProvisioningResult(BaseModel):
    """Result of a seed provisioning operation."""

    industry_profile: str
    sources_downloaded: list[str]
    sources_already_present: list[str]
    sources_failed: list[str]
    total_files: int
    errors: list[str]


# --- Suggestion Models ---


class SeedSuggestion(BaseModel):
    """A single seed suggestion from the LLM."""

    source_id: str = Field(description="From the registry")
    reason: str = Field(description="Why this source is relevant")
    confidence: float = Field(description="0.0-1.0")
    relevant_domains: list[str] = Field(
        description="Which GrACE domains it would help"
    )


class SuggestionResponse(BaseModel):
    """Response from the seed suggester."""

    suggestions: list[SeedSuggestion]
