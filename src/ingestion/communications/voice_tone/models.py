"""Pydantic v2 models for Voice & Tone Profiling (Chunk 58, D422, D423, Lock-R4).

All band fields are ``Literal["high","medium","low"]`` — numeric scores are
server-side only (D120/D217). Category values validated against ``D422_CATEGORIES``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# D422 ten-category enum (canonical, from GrACE-Decisions.md)
# ---------------------------------------------------------------------------

D422_CATEGORIES: frozenset[str] = frozenset({
    "executive_superior",
    "direct_manager",
    "peer_same_department",
    "peer_cross_department",
    "direct_report",
    "external_vendor",
    "external_client",
    "legal_counsel",
    "new_hire_onboarding",
    "general_distribution",
})

Band = Literal["high", "medium", "low"]


# ---------------------------------------------------------------------------
# FrequentRecipient / OrgContext (D504, Chunk 78 CP2)
# ---------------------------------------------------------------------------


class FrequentRecipient(BaseModel):
    """A frequently-contacted recipient for Voice Card context (D504)."""

    email: str = Field(description="Recipient email address.")
    name: str | None = Field(default=None, description="Recipient display name.")
    category: str | None = Field(default=None, description="D422 category if classified.")
    interaction_count: int = Field(default=0, description="Number of interactions.")


class OrgContext(BaseModel):
    """Organizational context for the profiled sender (D504)."""

    department: str | None = Field(default=None, description="Department name.")
    role: str | None = Field(default=None, description="Role/title.")
    seniority_level: str | None = Field(default=None, description="Seniority level.")


# ---------------------------------------------------------------------------
# StyleSignature — 8 band fields + §8.3 restored fields + synthesis outputs
# ---------------------------------------------------------------------------


class StyleSignature(BaseModel):
    """Style signature: 8 band features + restored §8.3 list fields + synthesis outputs.

    All band values are Literal["high","medium","low"] (D120/D217).
    New fields (Chunk 78, D504) have backward-compatible defaults so existing
    JSONB rows deserialize without error.
    """

    # --- Original 8 band fields (Chunk 58) ---
    sentence_length_band: Band = Field(description="Sentence length band.")
    vocabulary_complexity_band: Band = Field(description="Vocabulary complexity band (MATTR).")
    formality_band: Band = Field(description="Formality band (Heylighen-Dewaele F-score).")
    greeting_closing_band: Band = Field(description="Greeting/closing pattern band.")
    hedging_frequency_band: Band = Field(description="Hedging frequency band.")
    directness_band: Band = Field(description="Directness band (LLM-derived).")
    response_timing_band: Band = Field(description="Response timing band.")
    thread_depth_band: Band = Field(description="Thread participation depth band.")

    # --- §8.3 restored list fields (Chunk 78, D504) ---
    greeting_patterns: list[str] = Field(
        default_factory=list, description="Observed greeting patterns."
    )
    closing_patterns: list[str] = Field(
        default_factory=list, description="Observed closing patterns."
    )
    sample_phrases: list[str] = Field(
        default_factory=list, description="Characteristic sample phrases."
    )
    frequent_recipients: list[FrequentRecipient] = Field(
        default_factory=list, description="Frequently-contacted recipients."
    )
    organizational_context: OrgContext | None = Field(
        default=None, description="Organizational context for the sender."
    )

    # --- Synthesis-output persistence fields (Chunk 78, D504) ---
    tone_summary: str | None = Field(
        default=None, description="NL synthesis tone summary."
    )
    avoid_phrases: list[str] = Field(
        default_factory=list, description="Phrases to avoid in mimicry."
    )
    contrastive_markers: list[str] = Field(
        default_factory=list, description="Distinctive function-word markers."
    )


# ---------------------------------------------------------------------------
# StyleDelta — shift triples per band + optional overrides
# ---------------------------------------------------------------------------


class StyleDelta(BaseModel):
    """Per-recipient style delta: shift triples for each band."""

    sentence_length_shift: Band | None = Field(
        default=None, description="Shift in sentence length band for this recipient."
    )
    vocabulary_complexity_shift: Band | None = Field(
        default=None, description="Shift in vocabulary complexity band."
    )
    formality_shift: Band | None = Field(
        default=None, description="Shift in formality band."
    )
    greeting_override: str | None = Field(
        default=None, description="Recipient-specific greeting override."
    )
    closing_override: str | None = Field(
        default=None, description="Recipient-specific closing override."
    )
    hedging_shift: Band | None = Field(
        default=None, description="Shift in hedging frequency band."
    )
    directness_shift: Band | None = Field(
        default=None, description="Shift in directness band."
    )
    response_timing_shift: Band | None = Field(
        default=None, description="Shift in response timing band."
    )


# ---------------------------------------------------------------------------
# FeatureResult — per-email intermediate (bands only in output)
# ---------------------------------------------------------------------------


class FeatureResult(BaseModel):
    """Per-email feature extraction result. Band-only output (D120/D217)."""

    sentence_length_band: Band = Field(description="Sentence length band.")
    vocabulary_complexity_band: Band = Field(description="Vocabulary complexity band.")
    formality_band: Band = Field(description="Formality band.")
    greeting_closing_band: Band = Field(description="Greeting/closing pattern band.")
    hedging_frequency_band: Band = Field(description="Hedging frequency band.")
    directness_band: Band = Field(description="Directness band.")
    response_timing_band: Band = Field(description="Response timing band.")
    thread_depth_band: Band = Field(description="Thread participation depth band.")


# ---------------------------------------------------------------------------
# CommunicationStyleProfile — mirrors DDL
# ---------------------------------------------------------------------------


class CommunicationStyleProfile(BaseModel):
    """Versioned communication style profile (mirrors DDL communication_style_profiles)."""

    id: UUID | None = Field(default=None, description="Profile row ID.")
    sender_person_id: UUID | None = Field(
        default=None, description="Person entity ID for individual profiles."
    )
    aggregate_segment: str | None = Field(
        default=None,
        description="Segment name for aggregate/department profiles.",
    )
    profile_version: int = Field(description="Monotonically increasing version number.")
    style_signature: StyleSignature = Field(description="Eight-feature style signature.")
    profile_quality_band: Band = Field(description="Quality band for this profile.")
    created_at: datetime | None = Field(
        default=None, description="Profile creation timestamp."
    )

    @model_validator(mode="after")
    def _mutual_exclusion(self) -> "CommunicationStyleProfile":
        """Exactly one of sender_person_id or aggregate_segment must be set."""
        has_sender = self.sender_person_id is not None
        has_segment = self.aggregate_segment is not None
        if has_sender == has_segment:
            msg = (
                "Exactly one of sender_person_id or aggregate_segment must be set, "
                f"got sender_person_id={self.sender_person_id}, aggregate_segment={self.aggregate_segment}"
            )
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# RecipientStyleProfile — mirrors DDL
# ---------------------------------------------------------------------------


class RecipientStyleProfile(BaseModel):
    """Per-recipient style profile with D422 category validation."""

    id: UUID | None = Field(default=None, description="Recipient profile row ID.")
    profile_id: UUID = Field(description="Parent communication_style_profiles.id.")
    recipient_person_id: UUID = Field(description="Recipient Person entity ID.")
    category: str = Field(description="D422 recipient category.")
    confidence_band: Band = Field(description="Classification confidence band.")
    style_delta: StyleDelta = Field(description="Style delta relative to sender baseline.")
    created_at: datetime | None = Field(default=None, description="Row creation timestamp.")

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str) -> str:
        if v not in D422_CATEGORIES:
            raise ValueError(
                f"category must be one of {sorted(D422_CATEGORIES)}, got '{v}'"
            )
        return v


# ---------------------------------------------------------------------------
# DpiaAttestationRequest (Lock-R4)
# ---------------------------------------------------------------------------


class DpiaAttestationRequest(BaseModel):
    """DPIA attestation request per Lock-R4 (GDPR Art 35 gate for individual-mode)."""

    signed_by: str = Field(
        min_length=1, max_length=256, description="Name of the person signing the DPIA."
    )
    signed_role: str = Field(
        min_length=1, max_length=128, description="Role of the person signing."
    )
    signed_at_iso: datetime = Field(description="Signature timestamp (ISO 8601).")
    dpia_template_content_sha256: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 hex digest of the DPIA template at signing time.",
    )

    @field_validator("dpia_template_content_sha256")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        if not re.match(r"^[0-9a-fA-F]{64}$", v):
            raise ValueError("dpia_template_content_sha256 must be a 64-char hex string")
        return v.lower()


# ---------------------------------------------------------------------------
# VoiceToneConfig — Pydantic settings validating 13 YAML knobs
# ---------------------------------------------------------------------------

_ORG_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*[a-z0-9]$")


class VoiceToneConfig(BaseModel):
    """Configuration for Voice & Tone Profiling (13 operator knobs)."""

    organization_domains: list[str] = Field(
        default_factory=list,
        description="List of organization email domains for internal/external boundary.",
    )
    profile_minimum_emails_to_generate: int = Field(
        default=50,
        description="Minimum emails from a sender before generating a profile.",
    )
    retention_versions: int = Field(
        default=4,
        description="Number of profile versions to retain per identity.",
    )
    directness_batch_size: int = Field(
        default=20,
        description="Batch size for LLM directness classification.",
    )
    signature_sample_count: int = Field(
        default=3,
        description="Number of recent outgoing emails to sample for signature extraction.",
    )
    archive_minimum_new_correspondences: int = Field(
        default=100,
        description="Minimum new correspondences before triggering archive tier.",
    )
    archive_tier_enabled: bool = Field(
        default=False,
        description="Whether the archive tier is enabled.",
    )
    dpia_validity_days: int = Field(
        default=365,
        description="DPIA attestation validity in days.",
    )
    role_to_category_map: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from ArcadeDB Role.name to D422 category.",
    )
    title_to_category_map: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from signature title keyword to D422 category.",
    )
    formality_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"high": 60.0, "low": 40.0},
        description="F-score thresholds for formality bands.",
    )
    vocabulary_complexity_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"high": 0.8, "low": 0.5},
        description="MATTR thresholds for vocabulary complexity bands.",
    )
    hedging_lexicon: list[str] = Field(
        # F-33 (validation run, 2026-07-01): the lexicon missed common
        # hedges ("may", "I would generally expect", "I hesitate", ...), so a
        # densely-hedging sender banded identically to direct voices —
        # the profiles were non-discriminative. Expanded with the highest-signal
        # modal/qualifier/uncertainty hedges (multi-word phrases still match via
        # the \b..\b word-boundary regex in FeatureExtractor).
        default_factory=lambda: [
            "perhaps", "maybe", "might", "may", "could", "would", "should",
            "possibly", "probably", "presumably", "seemingly", "apparently",
            "somewhat", "rather", "fairly", "relatively",
            "it seems", "it appears", "I think", "I believe", "I suppose",
            "I would say", "I would expect", "I would generally expect",
            "I hesitate", "I'm not sure", "I am not certain",
            "arguably", "in my opinion", "to some extent", "to a degree",
            "more or less", "if I recall", "as far as I know",
            "tend to", "generally", "typically", "in general",
        ],
        description="Hedging terms for hedging frequency feature.",
    )

    # Chunk 78 (D504/D505/D506) config additions
    synthesis_provider_override: str | None = Field(
        default=None,
        description="D504: override global LLM for V&T synthesis only.",
    )
    baseline_corpus_source: str = Field(
        default="org_corpus",
        description="D504: org_corpus | bundled_english.",
    )
    export_default_dir: str = Field(
        default="data/voice-profiles",
        description="D505: default export root directory.",
    )
    redaction_enabled: bool = Field(
        default=True,
        description="D506: mandatory PII redaction for exports.",
    )
    redaction_ner_provider: str = Field(
        default="local_only",
        description="D506: never cloud for redaction NER.",
    )
    voice_card_core_word_limit: int = Field(
        default=400,
        description="D505: core card word limit.",
    )

    @field_validator("organization_domains", mode="before")
    @classmethod
    def _validate_domains(cls, v: list[str]) -> list[str]:
        validated = []
        for domain in v:
            if "://" in domain:
                raise ValueError(
                    f"organization_domains must not contain protocol prefix, got '{domain}'"
                )
            if domain != domain.lower():
                raise ValueError(
                    f"organization_domains must be lowercase, got '{domain}'"
                )
            if "/" in domain:
                raise ValueError(
                    f"organization_domains must not contain path, got '{domain}'"
                )
            validated.append(domain)
        return validated

    @field_validator("role_to_category_map", mode="before")
    @classmethod
    def _validate_role_map(cls, v: dict[str, str]) -> dict[str, str]:
        for key, val in v.items():
            if len(key) > 64:
                raise ValueError(
                    f"role_to_category_map key must be ≤64 chars, got '{key}'"
                )
            if key != key.lower():
                raise ValueError(
                    f"role_to_category_map key must be lowercase, got '{key}'"
                )
            if val not in D422_CATEGORIES:
                raise ValueError(
                    f"role_to_category_map value must be a D422 category, got '{val}'"
                )
        return v

    @field_validator("title_to_category_map", mode="before")
    @classmethod
    def _validate_title_map(cls, v: dict[str, str]) -> dict[str, str]:
        for val in v.values():
            if val not in D422_CATEGORIES:
                raise ValueError(
                    f"title_to_category_map value must be a D422 category, got '{val}'"
                )
        return v


# ---------------------------------------------------------------------------
# VoiceCardV1 — portable voice card schema (D505, Chunk 78 CP6)
# ---------------------------------------------------------------------------


class VoiceCardV1(BaseModel):
    """Portable Voice Card v1 schema for export (D505).

    Frontmatter fields + 6 required content sections.
    """

    # Frontmatter
    profile_schema: str = Field(
        default="grace.voice-card/v1", description="Card schema identifier."
    )
    subject: str = Field(description="Subject identifier (email or segment name).")
    generated: str = Field(description="Generation timestamp ISO 8601.")
    source_email_count: int = Field(description="Number of source emails analyzed.")
    consent_ref: str | None = Field(
        default=None, description="DPIA consent reference."
    )

    # Content sections
    style_summary: str = Field(description="Overall style summary.")
    characteristic_phrases: list[str] = Field(
        default_factory=list, description="Characteristic phrases."
    )
    greetings_closings: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Greeting and closing patterns.",
    )
    contrastive: list[str] = Field(
        default_factory=list,
        description="What makes this voice distinct.",
    )
    exemplars: list[str] = Field(
        default_factory=list, description="Exemplar phrases (redacted)."
    )
    avoid: list[str] = Field(
        default_factory=list, description="Phrases to avoid."
    )


# ---------------------------------------------------------------------------
# DraftGuidancePayload — enriched draft-guidance response (D504, Chunk 78 CP4)
# ---------------------------------------------------------------------------


class DraftGuidancePayload(BaseModel):
    """LLM-consumable draft-guidance payload served from persisted JSONB (D504).

    Pure DB read — no LLM call in the route handler.
    """

    greeting: str | None = Field(
        default=None, description="Suggested greeting from persisted patterns."
    )
    closing: str | None = Field(
        default=None, description="Suggested closing from persisted patterns."
    )
    sample_phrases: list[str] = Field(
        default_factory=list, description="Characteristic sample phrases."
    )
    avoid_phrases: list[str] = Field(
        default_factory=list, description="Phrases to avoid."
    )
    tone_summary: str | None = Field(
        default=None, description="NL tone summary from synthesis."
    )
    hedging: Band = Field(description="Hedging frequency band.")
    directness: Band = Field(description="Directness band.")
