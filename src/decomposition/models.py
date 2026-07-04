"""Pydantic v2 models for the Decomposition pipeline (Chunk 40, D310/D314).

All models are ``ConfigDict(extra='forbid')``. ``Hypothesis`` is a
``Annotated[Union[...], Field(discriminator='hypothesis_kind')]``
type alias used as the list element in ``Layer4HypothesisSet``; this
sidesteps the Pydantic 2.9 list-of-discriminated-union regression
(R5).

``confidence_band`` is restricted to the prose-hedge literal set
``Literal['high','medium','low']`` per D120/D217 — no numeric scores
reach Pydantic models.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


# ---------- Enums ----------


class DecompositionRunStatus(str, Enum):
    """Lifecycle status for a ``decomposition_runs`` row (D310/D327).

    Chunk 41 D327 widens the enum from 4 to 7 values, adding
    ``PAUSED_PRE_LAYER5``, ``PAUSED_PRE_LAYER6``, ``PAUSED_PRE_LAYER7``
    for the Layer 5–7 lifecycle. ``PAUSED_PRE_LAYER4`` is kept (real
    failure mode with resume semantics; outline Q7 resolved).
    """

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED_PRE_LAYER4 = "paused_pre_layer4"
    PAUSED_PRE_LAYER5 = "paused_pre_layer5"
    PAUSED_PRE_LAYER6 = "paused_pre_layer6"
    PAUSED_PRE_LAYER7 = "paused_pre_layer7"


# ---------- Layer 1 — filesystem walk ----------


class Layer1FileEntry(BaseModel):
    """Per-file inventory row (D316)."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str
    size_bytes: int = Field(ge=0)
    mtime: datetime
    suffix: str
    depth: int = Field(ge=0)


class Layer1FolderSummary(BaseModel):
    """Per-folder summary row (D316). Top-2 levels visible in operator UX."""

    model_config = ConfigDict(extra="forbid")

    path: str
    doc_count: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    oldest_mtime: datetime | None = None
    newest_mtime: datetime | None = None
    suffix_distribution: dict[str, int] = Field(default_factory=dict)
    sample_titles: list[str] = Field(default_factory=list)


class Layer1Summary(BaseModel):
    """Layer 1 artifact persisted as JSONB in ``decomposition_runs``."""

    model_config = ConfigDict(extra="forbid")

    archive_root: str
    total_files: int = Field(ge=0)
    files: list[Layer1FileEntry] = Field(default_factory=list)
    folders: list[Layer1FolderSummary] = Field(default_factory=list)


# ---------- Layer 2 — clustering ----------


class UmapParams(BaseModel):
    """UMAP parameters used in Layer 2 (D277 §2.2 verbatim)."""

    model_config = ConfigDict(extra="forbid")

    n_components: int = 10
    n_neighbors: int = 15
    min_dist: float = 0.1
    metric: str = "cosine"
    random_state: int = 42


class HdbscanParams(BaseModel):
    """HDBSCAN parameters used in Layer 2."""

    model_config = ConfigDict(extra="forbid")

    min_cluster_size: int = Field(ge=2)


class GmmParams(BaseModel):
    """Gaussian mixture model parameters used as Layer 2 fallback."""

    model_config = ConfigDict(extra="forbid")

    n_components: int = Field(ge=1)
    covariance_type: Literal["full", "tied", "diag", "spherical"] = "full"


class EmbeddingProvenance(BaseModel):
    """Provenance for the per-document embedding pass."""

    model_config = ConfigDict(extra="forbid")

    model: str
    dimension: int = Field(ge=1)
    document_count: int = Field(ge=0)


class Layer2Decision(BaseModel):
    """Layer 2 artifact persisted as JSONB in ``decomposition_runs``."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["hdbscan", "gmm"]
    cluster_count: int = Field(ge=0)
    outlier_count: int = Field(ge=0)
    outlier_ratio_at_gate: float = Field(ge=0.0, le=1.0)
    outlier_ratio_gate: float = Field(ge=0.0, le=1.0)
    cluster_labels: list[int] = Field(default_factory=list)
    umap: UmapParams = Field(default_factory=UmapParams)
    hdbscan: HdbscanParams | None = None
    gmm: GmmParams | None = None
    embedding: EmbeddingProvenance


# ---------- Layer 3 — entity co-occurrence ----------


class ProperNounMention(BaseModel):
    """Single proper-noun mention extracted by Layer 3 NER (D311)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    category: Literal["person", "organization", "location", "other_proper_noun"]
    occurrence_count: int = Field(ge=1, default=1)


class ProperNounMentions(BaseModel):
    """Container for all proper-noun mentions extracted from one document.

    Used as the Instructor ``response_model`` for Layer 3 NER calls.
    """

    model_config = ConfigDict(extra="forbid")

    mentions: list[ProperNounMention] = Field(default_factory=list)


class LeidenSeedRun(BaseModel):
    """Per-seed Leiden invocation outcome (D313)."""

    model_config = ConfigDict(extra="forbid")

    seed: int
    modularity: float
    community_count: int = Field(ge=0)


class Layer3Decision(BaseModel):
    """Layer 3 artifact persisted as JSONB in ``decomposition_runs``.

    ``low_stability_flag`` fires when ``mean_pairwise_ari <
    config.layer3.ari_threshold`` (default 0.6, D313). The flag is
    propagated to Layer 4 synthesis as contextual signal.
    """

    model_config = ConfigDict(extra="forbid")

    document_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    leiden_runs: list[LeidenSeedRun]
    selected_seed: int
    selected_modularity: float
    mean_pairwise_ari: float
    low_stability_flag: bool
    community_assignments: dict[str, int] = Field(default_factory=dict)


# ---------- Layer 4 — hypothesis synthesis ----------


# F-032d / ISS-0021 capture-the-why: when the Anthropic structured-output
# API rejects the discriminated-union schema (``oneOf`` unsupported) the
# Layer-4 synthesizer falls back to the tier-b prompt path, and the model
# has been observed emitting the legacy ``segment_name`` / ``segment_id``
# vocabulary. Validation must tolerate both shapes: ``segment_name`` is
# accepted as an alias for ``name`` (canonical spelling first so
# ``model_json_schema()`` / grammar constraints still advertise ``name``),
# and a stray ``segment_id`` key is dropped pre-validation rather than
# tripping ``extra="forbid"`` and pausing every run at
# ``paused_pre_layer4``. ``model_dump()`` output is unchanged.
_NAME_ALIASES = AliasChoices("name", "segment_name")


def _drop_legacy_segment_id(data: object) -> object:
    """Pre-validation hook: ignore the legacy ``segment_id`` key (F-032d)."""
    if isinstance(data, dict) and "segment_id" in data:
        data = {k: v for k, v in data.items() if k != "segment_id"}
    return data


class ProposedSegment(BaseModel):
    """One proposed segment within a ``SegmentedHypothesis``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, validation_alias=_NAME_ALIASES)
    description: str
    representative_keywords: list[str] = Field(default_factory=list)
    representative_entities: list[str] = Field(default_factory=list)

    # F-032d / ISS-0021: tolerate the legacy ``segment_id`` key.
    _ignore_segment_id = model_validator(mode="before")(_drop_legacy_segment_id)


class SegmentedHypothesis(BaseModel):
    """Layer 4 hypothesis proposing 1+ segments (D314)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    hypothesis_kind: Literal["segmented"] = "segmented"
    name: str = Field(min_length=1, validation_alias=_NAME_ALIASES)
    segment_count: int = Field(ge=1)
    segments: list[ProposedSegment] = Field(min_length=1)
    agreement_summary: str
    divergence_summary: str
    confidence_band: Literal["high", "medium", "low"]
    narrative_argument_for: str
    narrative_argument_against: str

    # F-032d / ISS-0021: tolerate the legacy ``segment_id`` key.
    _ignore_segment_id = model_validator(mode="before")(_drop_legacy_segment_id)


class NullHypothesis(BaseModel):
    """Layer 4 mandatory null hypothesis: undifferentiated whole (D314)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    hypothesis_kind: Literal["null"] = "null"
    name: str = Field(
        default="Null hypothesis: undifferentiated whole",
        validation_alias=_NAME_ALIASES,
    )
    narrative_argument_for: str
    narrative_argument_against: str
    confidence_band: Literal["high", "medium", "low"]

    # F-032d / ISS-0021: tolerate the legacy ``segment_id`` key.
    _ignore_segment_id = model_validator(mode="before")(_drop_legacy_segment_id)


# Discriminated-union type alias — used as list element type per R5.
Hypothesis = Annotated[
    Union[SegmentedHypothesis, NullHypothesis],
    Field(discriminator="hypothesis_kind"),
]


class SynthesisMetadata(BaseModel):
    """Metadata captured alongside a Layer 4 hypothesis set."""

    model_config = ConfigDict(extra="forbid")

    model: str
    low_stability_flag: bool
    layer3_mean_pairwise_ari: float
    generated_at: datetime


class Layer4HypothesisSet(BaseModel):
    """Layer 4 artifact persisted as JSONB in ``decomposition_runs``.

    Validator enforces exactly one ``NullHypothesis`` in ``hypotheses``
    (D314). The list contains 2–4 entries total.
    """

    # Phase-6 fix: ``populate_by_name=True`` + ``AliasChoices`` accept
    # both ``hypotheses`` and ``hypothesis_set`` for the top-level field.
    # Anthropic Haiku reliably emits ``hypothesis_set`` (the class name)
    # instead of the documented ``hypotheses`` field; we coerce in
    # rather than rejecting. Output (model_dump) still uses ``hypotheses``.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    hypotheses: list[Hypothesis] = Field(
        min_length=2,
        max_length=4,
        validation_alias=AliasChoices("hypotheses", "hypothesis_set"),
    )
    synthesis_metadata: SynthesisMetadata

    @model_validator(mode="after")
    def _exactly_one_null_hypothesis(self) -> "Layer4HypothesisSet":
        null_count = sum(
            1 for h in self.hypotheses if h.hypothesis_kind == "null"
        )
        if null_count != 1:
            raise ValueError(
                f"Layer4HypothesisSet must contain exactly one NullHypothesis "
                f"(found {null_count})"
            )
        return self
