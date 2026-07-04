"""Pydantic v2 models for Segmentation Map + Layer 5/6 payloads (Chunk 41, D325).

Source-of-truth models for:

* ``SegmentationMap`` — Layer 7 ratification artifact persisted in
  ``segmentation_maps`` table (D326). Hash-chained governance.
* ``Segment`` / ``DocumentSource`` / ``SegmentDependency`` — nested
  components of the map.
* ``Layer5DecisionPayload`` — operator decision recorded in
  ``decomposition_runs.layer5_decision`` JSONB (D320, D327).
* ``Layer5SegmentModification`` — per-segment rename/merge/split/drop.
* ``Layer6ValidationPayload`` — sample-CQ validation recorded in
  ``decomposition_runs.layer6_validation`` JSONB (D324, D327).
* ``Layer6SegmentValidation`` — per-segment validation result.
* ``GeneratedCQSnapshot`` — transient sample CQ produced by the
  Layer 6 adapter (D324). Never persisted to ``competency_questions``.

All models ``ConfigDict(extra='forbid')``. ``@model_validator(mode='after')``
on ``SegmentationMap`` enforces unique segment names + DAG (no cycles)
on segment dependencies.

Three-layer schema discipline preserved:
1. Pydantic = source of truth.
2. JSON Schema generated via ``model_json_schema()`` for the wire.
3. YAML serialization via ``pydantic_yaml.to_yaml_str()`` for the
   operator-facing artifact (D325).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# Reusable patterns.
_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PASCAL_CASE_UNDERSCORE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?:_[A-Z][A-Za-z0-9]*)*$")


# ---------- Segmentation Map components (D325) ----------


class DocumentSource(BaseModel):
    """One archive-relative document inclusion specifier (D325)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    inclusion_kind: Literal["folder", "glob", "explicit_list"]
    glob: str | None = None
    explicit_paths: list[str] | None = None

    @model_validator(mode="after")
    def _check_inclusion_kind_consistency(self) -> "DocumentSource":
        if self.inclusion_kind == "glob" and self.glob is None:
            raise ValueError(
                "DocumentSource.glob is required when inclusion_kind='glob'"
            )
        if (
            self.inclusion_kind == "explicit_list"
            and not self.explicit_paths
        ):
            raise ValueError(
                "DocumentSource.explicit_paths is required when "
                "inclusion_kind='explicit_list'"
            )
        return self


class SegmentDependency(BaseModel):
    """One inter-segment dependency edge (D325)."""

    model_config = ConfigDict(extra="forbid")

    segment: str = Field(min_length=1)
    relationship: Literal["extends", "references_via"]
    types: list[str] | None = None


class Segment(BaseModel):
    """One segment proposed by the Segmentation Map (D325).

    ``name`` is snake_case (regex-validated). ``expected_entity_types``
    are PascalCase_With_Underscores per CLAUDE.md naming convention.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str
    document_sources: list[DocumentSource] = Field(default_factory=list)
    expected_entity_types: list[str] = Field(default_factory=list)
    dependencies: list[SegmentDependency] = Field(default_factory=list)
    sample_cqs: list[str] = Field(default_factory=list)
    build_priority: Literal["high", "medium", "low"]

    @field_validator("name")
    @classmethod
    def _name_is_snake_case(cls, v: str) -> str:
        if not _SNAKE_CASE_RE.match(v):
            raise ValueError(
                f"Segment.name must be snake_case (got: {v!r})"
            )
        return v

    @field_validator("expected_entity_types")
    @classmethod
    def _entity_types_pascal_case_underscores(cls, v: list[str]) -> list[str]:
        for t in v:
            if not _PASCAL_CASE_UNDERSCORE_RE.match(t):
                raise ValueError(
                    f"expected_entity_types entry must be "
                    f"PascalCase_With_Underscores (got: {t!r})"
                )
        return v


class SegmentationMap(BaseModel):
    """Layer 7 Segmentation Map artifact (D325).

    Persisted in ``segmentation_maps`` table (D326). YAML serialization
    via ``pydantic_yaml.to_yaml_str()``. JSON Schema via
    ``model_json_schema()``. Hash-chain payload uses canonical-JSON
    SHA-256 (D326), not YAML — YAML round-trip non-determinism cannot
    break the chain (Risk R8).

    ``governance_metadata`` is an open-shape extensibility hook for
    workflow metadata that does not warrant a top-level field
    (mirrors D291 ``extension_metadata`` pattern).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    decomposition_run_id: UUID
    organization_id: UUID | None = None
    produced_at: datetime
    produced_by: str | None = None
    archive_root_canonical_hash: str = Field(min_length=1)
    segments: list[Segment] = Field(min_length=1)
    null_hypothesis_accepted: bool
    governance_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_segments(self) -> "SegmentationMap":
        # Unique segment names.
        names = [s.name for s in self.segments]
        if len(set(names)) != len(names):
            raise ValueError(
                "SegmentationMap.segments must have unique names"
            )

        # DAG check on dependencies — no cycles.
        # Build adjacency list segment -> {dependency segments}.
        name_set = set(names)
        adj: dict[str, set[str]] = {n: set() for n in names}
        for seg in self.segments:
            for dep in seg.dependencies:
                if dep.segment not in name_set:
                    raise ValueError(
                        f"Segment.dependencies references unknown segment "
                        f"{dep.segment!r} (not present in segments list)"
                    )
                adj[seg.name].add(dep.segment)

        # DFS-based cycle detection.
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in names}

        def _dfs(node: str) -> None:
            if color[node] == GRAY:
                raise ValueError(
                    f"Cyclic segment dependency detected at {node!r}"
                )
            if color[node] == BLACK:
                return
            color[node] = GRAY
            for nb in adj[node]:
                _dfs(nb)
            color[node] = BLACK

        for n in names:
            if color[n] == WHITE:
                _dfs(n)
        return self


# ---------- Layer 5 decision (D320) ----------


class Layer5SegmentModification(BaseModel):
    """One segment-level rename/merge/split/drop modification (D320)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["rename", "merge", "split", "drop"]
    target_segment: str = Field(min_length=1)
    # Rename + split + merge use ``new_name``; merge / split may set
    # ``related_segments`` for the merge sources or split parents.
    new_name: str | None = None
    related_segments: list[str] | None = None
    rationale: str | None = None


class Layer5DecisionPayload(BaseModel):
    """Operator decision for Layer 5 Structured Interview (D320).

    Persisted in ``decomposition_runs.layer5_decision`` JSONB. Five
    ``decision_kind`` values per D320:

    * ``accepted_segmented`` — operator picked one segmented hypothesis
      (with optional modifications).
    * ``accepted_null`` — operator accepted the mandatory null
      hypothesis.
    * ``rerun_finer`` — trigger ±1.5× resolution re-run, finer.
    * ``rerun_coarser`` — trigger ±1.5× resolution re-run, coarser.
    * ``reject_all_reformulate`` — IDEA2-style single-pass
      reformulation; new ``decomposition_runs`` row via Path B.
    """

    model_config = ConfigDict(extra="forbid")

    decision_kind: Literal[
        "accepted_segmented",
        "accepted_null",
        "rerun_finer",
        "rerun_coarser",
        "reject_all_reformulate",
    ]
    selected_hypothesis_name: str | None = None
    modifications: list[Layer5SegmentModification] = Field(default_factory=list)
    rationale: str = ""
    decided_by: UUID | None = None
    decided_at: datetime


# ---------- Layer 6 validation (D324) ----------


class GeneratedCQSnapshot(BaseModel):
    """One transient sample CQ from the Layer 6 adapter (D324).

    Held in ``decomposition_runs.layer6_validation`` JSONB only.
    Never written to ``competency_questions``.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    cq_type: str = Field(default="UNCLASSIFIED")
    provenance: dict[str, Any] = Field(default_factory=dict)


class Layer6SegmentValidation(BaseModel):
    """Per-segment validation result for Layer 6 (D324)."""

    model_config = ConfigDict(extra="forbid")

    segment_name: str = Field(min_length=1)
    sample_cqs: list[GeneratedCQSnapshot] = Field(default_factory=list)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    operator_notes: str = ""


class Layer6ValidationPayload(BaseModel):
    """Layer 6 sample-CQ validation persisted in JSONB (D324)."""

    model_config = ConfigDict(extra="forbid")

    segments: list[Layer6SegmentValidation] = Field(default_factory=list)
    validated_by: UUID | None = None
    validated_at: datetime
