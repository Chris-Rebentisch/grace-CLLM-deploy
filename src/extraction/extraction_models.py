"""Pydantic models defining the Extraction pipeline's data contracts.

ExtractionResult is the Instructor response_model — the LLM's structured
output must conform to this schema. DocumentChunk, ExtractionRequest, and
ExtractionBatch are pipeline-internal data structures.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, field_validator


def _coerce_temporal_hints(v):
    """Coerce LLM-emitted ``temporal_hints: []`` (empty list) into None.

    Phase-4 finding: qwen2.5:7b consistently defaults the ``temporal_hints``
    field to an empty list (``[]``) when no temporal info is present, while
    the model contract is ``dict[str, str] | None``. Without this validator
    Pydantic rejects every chunk that has no temporal hints, killing the
    entire extraction. A list-typed temporal_hints is semantically
    equivalent to None (no hints), so coerce.
    """
    if isinstance(v, list):
        if not v:
            return None
        # Best-effort: convert [{"key":"start","value":"..."}, ...] to dict
        try:
            return {item["key"]: item["value"] for item in v if isinstance(item, dict) and "key" in item and "value" in item}
        except Exception:
            return None
    return v


class ExtractedEntity(BaseModel):
    """A single entity extracted from a text chunk by the LLM.

    Field descriptions are injected into the LLM prompt via JSON Schema
    when this model is used as an Instructor response_model.
    """

    # F-024 / ISS-0016: email extraction produced first-name fragments ("Diane")
    # and truncated names ("Mercer"). The description feeds the LLM via JSON
    # Schema — make the full-canonical-name requirement explicit here too.
    name: str = Field(
        description=(
            "The canonical name of the entity, using the fullest form that "
            "appears anywhere in the text. For a Person, use the full name "
            "(e.g. 'Diane Mercer', never just 'Diane') whenever the full "
            "name appears anywhere in the document or email, including the "
            "From/sender line. Do not truncate names."
        )
    )
    entity_type: str = Field(
        description="The ontology entity type this entity belongs to"
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain properties extracted for this entity, matching the ontology schema",
    )
    source_sentence_indices: list[int] = Field(
        default_factory=list,
        description="Zero-based indices of sentences in the chunk that mention this entity",
    )
    temporal_hints: dict[str, str] | None = Field(
        default=None,
        description=(
            "Raw temporal strings from text, e.g. "
            "{'start': 'January 2024', 'end': 'March 2025'}"
        ),
    )
    _coerce_th = field_validator("temporal_hints", mode="before")(_coerce_temporal_hints)
    chunk_source_map: list[tuple[str, int]] = Field(
        default_factory=list,
        description="List of (chunk_id, sentence_index) pairs preserving which "
                    "chunk each sentence reference came from. Populated during "
                    "extraction; concatenated during cross-chunk dedup merge.",
    )
    resolved_grace_id: str | None = Field(
        default=None,
        description="grace_id of matched existing entity after resolution. "
                    "None = new entity or resolution not yet run.",
    )
    resolution_tier: str | None = Field(
        default=None,
        description="Resolution tier that produced the match: exact, embedding, llm, new. "
                    "None = resolution not yet run.",
    )


class ExtractedRelationship(BaseModel):
    """A single relationship extracted from a text chunk by the LLM.

    Subject and object are identified by name + type. Entity resolution
    (mapping names to grace_id) happens downstream in Chunk 20.

    NOTE: Relationship endpoint resolution (subject_name -> grace_id,
    object_name -> grace_id) is performed in Chunk 21 by the graph writer,
    after entity resolution has assigned grace_ids to all entities.
    """

    subject_name: str = Field(description="Name of the source entity")
    subject_type: str = Field(description="Ontology type of the source entity")
    predicate: str = Field(
        description="The ontology relationship type connecting subject to object"
    )
    object_name: str = Field(description="Name of the target entity")
    object_type: str = Field(description="Ontology type of the target entity")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Edge properties from the ontology schema",
    )
    source_sentence_indices: list[int] = Field(
        default_factory=list,
        description="Zero-based sentence indices where this relationship is stated or implied",
    )
    temporal_hints: dict[str, str] | None = Field(
        default=None,
        description="Raw temporal strings for this relationship",
    )
    _coerce_th = field_validator("temporal_hints", mode="before")(_coerce_temporal_hints)
    chunk_source_map: list[tuple[str, int]] = Field(
        default_factory=list,
        description="List of (chunk_id, sentence_index) pairs preserving which "
                    "chunk each sentence reference came from. Populated during "
                    "extraction; concatenated during cross-chunk dedup merge.",
    )


class ExtractionResult(BaseModel):
    """Structured output from a single chunk extraction.

    This is the Instructor response_model. The LLM must produce output
    conforming to this schema. Instructor handles JSON Schema injection,
    grammar-constrained decoding (via Ollama), Pydantic validation, and
    retry-with-feedback on validation failure.

    Kept flat — max 3 levels deep — for 7B model output quality.
    """

    entities: list[ExtractedEntity] = Field(
        default_factory=list,
        description="All entities identified in the text chunk",
    )
    # F-009 / ISS-0016: weaker models (claude-haiku) returned zero relationships
    # on relationship-dense documents; default_factory=list means an empty list
    # validates silently with no retry pressure. Make the field description
    # imperative with a concrete example — it is injected into the TOOLS/JSON
    # schema the LLM reads.
    # F-0014 (validation run, 2026-07-03): the F-009 description hardening
    # was not enough — in Anthropic TOOLS mode claude-haiku omitted the
    # `relationships` KEY entirely (raw tool_use input had only `entities`;
    # same prompts in plain-text mode produced correct relationships). With
    # default_factory=list the key is optional in the injected JSON schema, so
    # the omission validates silently and instructor never pushes back. Drop
    # the default: a missing key now fails validation and instructor's
    # retry-with-feedback forces the model to emit it (an explicit [] remains
    # valid for genuinely relationship-free text).
    relationships: list[ExtractedRelationship] = Field(
        description=(
            "REQUIRED. All relationships identified between entities in the "
            "text chunk. Explicitly stated relationships (e.g. owns, manages, "
            "employs, leases, party-to-agreement) MUST be captured whenever a "
            "matching relationship type exists in the schema. An empty list is "
            "ONLY acceptable when the text truly contains no relationships "
            "between the extracted entities. Example: 'Acme Holdings LLC owns "
            "Cedar Grove' yields subject_name='Acme Holdings LLC', "
            "predicate='owns', object_name='Cedar Grove'."
        ),
    )


class DocumentChunk(BaseModel):
    """A segment of a processed document ready for extraction.

    Created by the document chunker (Chunk 17). Defined here so the
    type contract is available to all downstream consumers.
    """

    chunk_id: str = Field(
        description="Deterministic ID: hash of doc_id + sequential index"
    )
    text: str = Field(description="The chunk text content")
    char_start: int = Field(description="Character offset start in original document")
    char_end: int = Field(description="Character offset end in original document")
    section_id: str | None = Field(
        default=None, description="Docling section ID if available"
    )
    sentence_offsets: list[tuple[int, int]] = Field(
        default_factory=list,
        description="List of (start, end) character positions of sentences within chunk",
    )
    token_count_estimate: int = Field(
        default=0, description="Approximate token count"
    )
    overlap_char_count: int = Field(
        default=0,
        description="Number of leading characters in text that are overlap from "
                    "previous chunk. Non-overlap content starts at text[overlap_char_count:].",
    )

    @computed_field
    @property
    def is_overlap(self) -> bool:
        """True when chunk has overlap content from previous chunk."""
        return self.overlap_char_count > 0


class ExtractionRequest(BaseModel):
    """API request to extract from a document."""

    document_text: str = Field(
        description="Full document text (post-Docling or plain text)"
    )
    document_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Source document identifier",
    )
    module_name: str | None = Field(
        default=None,
        description="Ontology module name for schema injection. None = use full "
                    "active schema when fewer than two modules registered; pipeline "
                    "errors when two or more modules exist and module_name is None. "
                    "Auto-detect deferred to Chunk 17b.",
    )
    provider_override: str | None = Field(
        default=None,
        description="Override extraction provider for this request only",
    )
    model_override: str | None = Field(
        default=None,
        description="Override extraction model for this request only",
    )


class ExtractionBatch(BaseModel):
    """Aggregated extraction results across all chunks of a document."""

    batch_id: str = Field(default_factory=lambda: str(uuid4()))
    document_id: str = Field(description="Source document ID")
    module_name: str | None = Field(
        default=None, description="Ontology module used"
    )
    schema_version: int | None = Field(
        default=None, description="Active ontology version at extraction time"
    )
    chunks_total: int = Field(default=0)
    chunks_succeeded: int = Field(default=0)
    chunks_failed: int = Field(default=0)
    chunk_extraction_succeeded: list[bool] = Field(
        default_factory=list,
        description="Per chunk: True if extract_chunk succeeded with a validated result",
    )
    chunk_entity_counts: list[int] = Field(
        default_factory=list,
        description=(
            "Per chunk: entity count after overlap filter (0 if chunk failed). "
            "Aligned with chunk order; len equals chunks_total when populated"
        ),
    )
    chunk_relationship_counts: list[int] = Field(
        default_factory=list,
        description="Per chunk: relationship count after overlap filter (0 if chunk failed)",
    )
    chunk_latency_ms: list[float] = Field(
        default_factory=list,
        description="Per chunk: extract_chunk wall time in ms (success or failure)",
    )
    entities_pre_dedup_count: int = Field(
        default=0,
        description="Total entities extracted across all chunks before cross-chunk dedup",
    )
    relationships_pre_dedup_count: int = Field(
        default=0,
        description="Total relationships extracted across all chunks before cross-chunk dedup",
    )
    entities: list[ExtractedEntity] = Field(
        default_factory=list,
        description="All entities across all chunks (post cross-chunk dedup within document)",
    )
    relationships: list[ExtractedRelationship] = Field(
        default_factory=list,
        description="All relationships across all chunks (post cross-chunk dedup within document)",
    )
    provider_used: str = Field(default="", description="Provider that handled extraction")
    model_used: str = Field(default="", description="Model that performed extraction")
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)

    # Chunk 19: verification + claim fields
    chunks: list[DocumentChunk] = Field(
        default_factory=list,
        description="Chunks produced for this document in pipeline order. "
                    "Passed to verify_batch internally when verify=True.",
    )
    claims: list[Any] = Field(
        default_factory=list,
        description="Claims (Claim objects) after verification when verify=True; "
                    "empty when verify=False. Typed as Any to avoid circular import.",
    )
    claims_accepted: int = Field(
        default=0,
        description="Count of claims with status AUTO_ACCEPTED after verification.",
    )
    claims_quarantined: int = Field(
        default=0,
        description="Count of claims with status QUARANTINED after verification.",
    )
    avg_claim_confidence: float | None = Field(
        default=None,
        description="Mean confidence over claims with non-null confidence; None "
                    "if no claims.",
    )
    verification_failure_count: int = Field(
        default=0,
        description="Count of items where verification raised ExtractionLLMError "
                    "(was_failure=True).",
    )
    er_stats: dict | None = Field(
        default=None,
        description="Entity resolution statistics: counts by tier, "
                    "new vs matched ratio. None if resolution not run.",
    )
    write_stats: dict | None = Field(
        default=None,
        description="Graph write statistics from Chunk 21: entities_created, "
                    "entities_matched, relationships_created, aliases_appended, "
                    "produced_by_edges_created, errors. None if write not run.",
    )


class DocumentChunkVertex(BaseModel):
    """ArcadeDB vertex model for a Document_Chunk (provenance-layer type).

    Distinct from ``DocumentChunk`` (L145) which is the extraction-pipeline's
    in-memory chunk representation. This model mirrors the 9-property
    META_ENTITY_TYPES entry registered by D463.

    Named ``DocumentChunkVertex`` (not ``DocumentChunk``) to avoid collision
    with the existing class — see spec §18.1.
    """

    grace_id: str = Field(
        description="UUID4 primary external identifier"
    )
    source_document_id: str = Field(
        description="FK to processed_documents row"
    )
    chunk_index: int = Field(
        description="Zero-based sequential index within document"
    )
    text: str = Field(
        description="Chunk text content"
    )
    chunk_token_count: int = Field(
        default=0,
        description="Token count for the chunk",
    )
    embedding: list[float] = Field(
        default_factory=list,
        description="768-dim nomic-embed-text embedding vector (ArcadeDB property: _embedding)",
    )
    extracted_at: datetime | None = Field(
        default=None,
        description="Timestamp of chunk creation",
    )
    sensitivity_tags: str = Field(
        default="",
        description="Bar-form sensitivity tags per D344/D440, e.g. '|privileged|pii_dense|'",
    )
    deprecated: bool = Field(
        default=False,
        description="Lifecycle parity with domain vertex deprecation semantics (ArcadeDB property: _deprecated)",
    )


class PhotoObservation(BaseModel):
    """Structured visual description from vision-LLM analysis of a photo.

    D501: severity_band is Literal enum, NOT float (D120/D217).
    D500: produced by generate_vision with response_model=PhotoObservation.
    """

    damage_type: str = Field(description="Type of damage observed (e.g. 'dent', 'crack', 'water_damage')")
    affected_component: str = Field(description="Component affected (e.g. 'hood', 'roof', 'foundation')")
    severity_band: Literal["minor", "moderate", "severe", "total_loss"] = Field(
        description="Band-only severity classification (D120/D217 — no float)"
    )
    visible_text: str | None = Field(default=None, description="Any text visible in the image (OCR or signage)")
    confidence_band: Literal["high", "medium", "low"] = Field(
        description="Band-only confidence in the observation (D120/D217 — no float)"
    )
    # F-011 / ISS-0018: the original schema was insurance/damage-shaped only —
    # scene semantics ("4 numbered lots, access road, north arrow") had nowhere
    # to land and damage_type='none' was the only scene statement.
    # scene_summary/key_elements give scene-class images (site plans, maps,
    # layouts) a home while damage-class images keep the fields above.
    # Defaults keep previously persisted vision_description_json parseable
    # (backward compatible).
    scene_summary: str = Field(
        default="",
        description=(
            "1-3 sentence description of what the image shows overall (scene, "
            "layout, setting, subject). Populate for EVERY image, including "
            "non-damage images where damage_type is 'none'."
        ),
    )
    key_elements: list[str] = Field(
        default_factory=list,
        description=(
            "Salient objects, labels, or features visible in the image "
            "(e.g. 'access road', 'north arrow', 'lot 4 marker', 'two entry doors')"
        ),
    )


class ImageAsset(BaseModel):
    """Graph vertex shape for Image_Asset provenance-layer type.

    D501: mirrors the META_ENTITY_TYPES['Image_Asset'] property list.
    No numeric severity field (D120/D217).
    """

    grace_id: str = Field(default_factory=lambda: str(uuid4()), description="UUID4 external identifier")
    source_path: str = Field(description="Filesystem path to the source image")
    content_sha256: str = Field(description="SHA-256 hash of raw image bytes (dedup key)")
    media_type: str = Field(description="MIME type (e.g. 'image/jpeg', 'image/png')")
    image_class: str = Field(description="Classification: 'document' | 'photo' | 'unknown'")
    ocr_text: str | None = Field(default=None, description="OCR-extracted text (if any)")
    vision_description_json: str | None = Field(
        default=None, description="JSON-serialized PhotoObservation (if photo-class)"
    )
    sensitivity_tags: str = Field(default="", description="Bar-form sensitivity tags (e.g. '|pii_dense|privileged|')")
    extracted_at: datetime | None = Field(default=None, description="Timestamp of ingestion")
