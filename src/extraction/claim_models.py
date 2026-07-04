"""Claim data model — the audit trail for every extracted triple.

Every entity and relationship extracted by the pipeline becomes a Claim
before it is written to the graph. Claims persist in PostgreSQL for
provenance, human review, and quality analysis.
"""

from __future__ import annotations

import enum
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ClaimVerdict(str, enum.Enum):
    """Verification verdict from the second-pass model."""

    PENDING = "PENDING"
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    INSUFFICIENT = "INSUFFICIENT"


class ClaimStatus(str, enum.Enum):
    """Lifecycle status of a claim."""

    AUTO_ACCEPTED = "auto_accepted"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ConstraintSeverity(str, enum.Enum):
    """Severity levels for pre-write constraint validation."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ConstraintViolation(BaseModel):
    """A single constraint violation found during pre-write validation (Chunk 21)."""

    severity: ConstraintSeverity = Field(description="ERROR, WARNING, or INFO")
    rule: str = Field(
        description="Constraint rule identifier, e.g. 'invalid_entity_type', 'domain_range_violation'"
    )
    message: str = Field(description="Human-readable description of the violation")


class EvidenceSpan(BaseModel):
    """A text span from the source document supporting or contradicting a claim."""

    sentence_index: int = Field(description="Zero-based sentence index in the source chunk")
    text: str = Field(description="The sentence text")
    char_start: int = Field(default=0, description="Character start position in chunk")
    char_end: int = Field(default=0, description="Character end position in chunk")


class Claim(BaseModel):
    """A single extracted triple (entity or relationship) with full provenance.

    The central audit record of the Extraction module. Every extracted
    entity and relationship passes through the Claim lifecycle:
    extraction -> verification -> constraint validation -> graph write or quarantine.
    """

    # Identity
    claim_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="External UUID identifier",
    )
    claim_fingerprint: str = Field(
        default="",
        description="SHA-256 of normalized SPO + qualifiers + evidence hash. For future dedup.",
    )
    extraction_unit_id: str = Field(
        default="",
        description="Idempotency key: hash of doc_id + chunk_id + schema_version + extractor_version",
    )

    # What was extracted
    entity_type: str | None = Field(
        default=None, description="Extracted entity type (if entity claim)"
    )
    relationship_type: str | None = Field(
        default=None, description="Extracted relationship type (if relationship claim)"
    )
    subject_name: str = Field(default="", description="Subject entity name")
    predicate: str = Field(
        default="", description="Relationship type or 'entity' for entity claims"
    )
    object_name: str | None = Field(
        default=None, description="Object entity name (null for entity claims)"
    )
    subject_type: str | None = Field(
        default=None,
        description="Ontology type of the subject entity. Populated from "
                    "ExtractedRelationship.subject_type for relationship claims. "
                    "Same as entity_type for entity claims.",
    )
    object_type: str | None = Field(
        default=None,
        description="Ontology type of the object entity. Populated from "
                    "ExtractedRelationship.object_type for relationship claims. "
                    "None for entity claims.",
    )
    properties_json: dict[str, Any] = Field(
        default_factory=dict, description="Extracted properties as dict"
    )

    # Evidence
    evidence_spans: list[EvidenceSpan] = Field(
        default_factory=list, description="Source text spans supporting this claim"
    )

    # Verification (populated by Chunk 19)
    verdict: ClaimVerdict = Field(
        default=ClaimVerdict.PENDING, description="Verification verdict"
    )
    confidence: float | None = Field(
        default=None, description="0.0-1.0 confidence score"
    )

    # Status
    status: ClaimStatus = Field(
        default=ClaimStatus.AUTO_ACCEPTED, description="Lifecycle status"
    )
    decision_source: str = Field(
        default="pipeline", description="Who set the status: pipeline, verifier, human, policy"
    )
    constraint_violations: list[ConstraintViolation] = Field(
        default_factory=list, description="Violations found during constraint validation"
    )

    # Lineage
    supersedes_claim_id: str | None = Field(
        default=None, description="claim_id of the claim this one supersedes"
    )
    source_document_id: str = Field(default="", description="Source document identifier")
    source_chunk_id: str = Field(default="", description="Source chunk identifier")
    ontology_module: str | None = Field(
        default=None, description="Which ontology module governed extraction"
    )
    schema_version: int | None = Field(
        default=None, description="Ontology version at extraction time"
    )

    # Provenance (D33: prompt/version lineage)
    prompt_template_id: str = Field(
        default="", description="e.g. 'extraction_v1', 'verification_v1'"
    )
    model_name: str = Field(default="", description="Model that produced this claim")
    model_temperature: float = Field(default=0.0, description="Temperature used")
    model_max_tokens: int = Field(default=0, description="Max tokens used")

    # Extraction event linkage
    extraction_event_id: str | None = Field(
        default=None, description="UUID of the Extraction_Event that produced this claim"
    )
    resolved_entity_grace_id: str | None = Field(
        default=None,
        description="grace_id of resolved entity (for entity claims). "
                    "Populated by Chunk 20 entity resolution.",
    )
    resolved_subject_grace_id: str | None = Field(
        default=None,
        description="grace_id of resolved subject entity (for relationship claims). "
                    "Added in Chunk 20, populated in Chunk 21 by graph writer.",
    )
    resolved_object_grace_id: str | None = Field(
        default=None,
        description="grace_id of resolved object entity (for relationship claims). "
                    "Added in Chunk 20, populated in Chunk 21 by graph writer.",
    )
    resolution_note: str | None = Field(
        default=None,
        description="Resolution metadata: 'llm_disambiguation_failed' on Tier 3 "
                    "failure. None for normal resolutions.",
    )
    verifier_model: str | None = Field(
        default=None, description="Model that verified this claim (D33 provenance)"
    )
    contradiction_reason: str = Field(
        default="",
        description="If verdict is REFUTED, the verifier's explanation of what "
                    "contradicts the claim. Empty for non-REFUTED verdicts.",
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the claim was created (timezone-aware UTC)",
    )

    @staticmethod
    def compute_fingerprint(
        subject_name: str,
        predicate: str,
        object_name: str | None,
        properties: dict[str, Any],
        evidence_texts: list[str],
    ) -> str:
        """Compute SHA-256 fingerprint for dedup.

        Deterministic: same SPO + properties + evidence always produces same hash.
        """
        normalized = json.dumps(
            {
                "s": subject_name.strip().lower(),
                "p": predicate.strip().lower(),
                "o": (object_name or "").strip().lower(),
                "props": dict(sorted(properties.items())),
                "evidence": sorted(e.strip().lower() for e in evidence_texts),
            },
            sort_keys=True,
        )
        return hashlib.sha256(normalized.encode()).hexdigest()

    @staticmethod
    def compute_extraction_unit_id(
        source_doc_id: str,
        chunk_id: str,
        schema_version: int | None,
        extractor_version: str,
    ) -> str:
        """Compute idempotency key for extraction dedup.

        If this extraction_unit_id already exists with status=completed,
        the graph writer skips the write (Chunk 21).
        """
        raw = f"{source_doc_id}:{chunk_id}:{schema_version}:{extractor_version}"
        return hashlib.sha256(raw.encode()).hexdigest()
