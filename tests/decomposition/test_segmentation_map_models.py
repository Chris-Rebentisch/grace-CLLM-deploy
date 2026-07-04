"""Tests for Chunk 41 D325 Segmentation Map Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pydantic_yaml import parse_yaml_raw_as, to_yaml_str

from src.decomposition.segmentation_map_models import (
    DocumentSource,
    GeneratedCQSnapshot,
    Layer5DecisionPayload,
    Layer5SegmentModification,
    Layer6SegmentValidation,
    Layer6ValidationPayload,
    Segment,
    SegmentationMap,
    SegmentDependency,
)


def _make_segment(
    name: str = "ops",
    dependencies: list[SegmentDependency] | None = None,
) -> Segment:
    return Segment(
        name=name,
        description="Operations segment",
        document_sources=[
            DocumentSource(path="ops/", inclusion_kind="folder"),
        ],
        expected_entity_types=["Operations_Memo", "Acme_Inc"],
        dependencies=dependencies or [],
        sample_cqs=["What memos describe ops?"],
        build_priority="high",
    )


def _make_map(segments: list[Segment]) -> SegmentationMap:
    return SegmentationMap(
        schema_version="1.0",
        decomposition_run_id=uuid4(),
        organization_id=None,
        produced_at=datetime(2026, 5, 8, 12, tzinfo=timezone.utc),
        produced_by="alice",
        archive_root_canonical_hash="a" * 64,
        segments=segments,
        null_hypothesis_accepted=False,
        governance_metadata={"reviewer": "alice"},
    )


# ---------- Round-trip + schema generation ----------


def test_pydantic_yaml_round_trip_preserves_all_fields():
    seg = _make_segment()
    sm = _make_map([seg])
    yaml_text = to_yaml_str(sm)
    rebuilt = parse_yaml_raw_as(SegmentationMap, yaml_text)
    assert rebuilt.model_dump(mode="json") == sm.model_dump(mode="json")


def test_model_json_schema_includes_discriminated_inclusion_kind():
    schema = SegmentationMap.model_json_schema()
    # SegmentationMap should reference a Segment / DocumentSource def.
    defs = schema.get("$defs", {})
    assert "DocumentSource" in defs
    inclusion = defs["DocumentSource"]["properties"]["inclusion_kind"]
    enum_values = inclusion.get("enum", [])
    assert set(enum_values) == {"folder", "glob", "explicit_list"}


def test_schema_version_const_is_one_dot_zero():
    schema = SegmentationMap.model_json_schema()
    sv = schema["properties"]["schema_version"]
    # Pydantic v2 renders Literal as `enum: ['1.0']` or `const: '1.0'`.
    if "const" in sv:
        assert sv["const"] == "1.0"
    else:
        assert sv.get("enum") == ["1.0"]


# ---------- DAG + uniqueness validators ----------


def test_dag_cycle_rejection():
    a = _make_segment(
        "ops",
        dependencies=[SegmentDependency(segment="finance", relationship="extends")],
    )
    b = _make_segment(
        "finance",
        dependencies=[SegmentDependency(segment="ops", relationship="extends")],
    )
    with pytest.raises(ValidationError) as exc:
        _make_map([a, b])
    assert "Cyclic" in str(exc.value)


def test_unique_segment_names_required():
    with pytest.raises(ValidationError) as exc:
        _make_map([_make_segment("ops"), _make_segment("ops")])
    assert "unique names" in str(exc.value)


def test_dependency_to_unknown_segment_rejected():
    bad = _make_segment(
        "ops",
        dependencies=[SegmentDependency(segment="missing", relationship="extends")],
    )
    with pytest.raises(ValidationError) as exc:
        _make_map([bad])
    assert "unknown segment" in str(exc.value)


# ---------- extra='forbid' enforcement ----------


def test_extra_forbid_rejects_unknown_field_on_segmentation_map():
    with pytest.raises(ValidationError):
        SegmentationMap(
            schema_version="1.0",
            decomposition_run_id=uuid4(),
            produced_at=datetime.now(timezone.utc),
            archive_root_canonical_hash="a" * 64,
            segments=[_make_segment()],
            null_hypothesis_accepted=False,
            unexpected_field="x",
        )


def test_extra_forbid_rejects_unknown_field_on_segment():
    with pytest.raises(ValidationError):
        Segment(
            name="ops",
            description="x",
            build_priority="high",
            unknown="y",
        )


# ---------- governance_metadata default ----------


def test_governance_metadata_defaults_to_empty_dict():
    sm = SegmentationMap(
        schema_version="1.0",
        decomposition_run_id=uuid4(),
        produced_at=datetime.now(timezone.utc),
        archive_root_canonical_hash="a" * 64,
        segments=[_make_segment()],
        null_hypothesis_accepted=False,
    )
    assert sm.governance_metadata == {}


# ---------- name regex ----------


def test_segment_name_must_be_snake_case():
    with pytest.raises(ValidationError) as exc:
        Segment(name="OpsSegment", description="x", build_priority="high")
    assert "snake_case" in str(exc.value)


def test_expected_entity_types_must_be_pascal_with_underscores():
    with pytest.raises(ValidationError) as exc:
        Segment(
            name="ops",
            description="x",
            expected_entity_types=["bad_lowercase"],
            build_priority="high",
        )
    assert "PascalCase_With_Underscores" in str(exc.value)


# ---------- Layer 5 / Layer 6 payload sanity ----------


def test_layer5_decision_kinds_all_reachable():
    kinds = [
        "accepted_segmented",
        "accepted_null",
        "rerun_finer",
        "rerun_coarser",
        "reject_all_reformulate",
    ]
    for k in kinds:
        p = Layer5DecisionPayload(
            decision_kind=k,
            decided_at=datetime.now(timezone.utc),
        )
        assert p.decision_kind == k


def test_layer6_validation_payload_round_trip():
    payload = Layer6ValidationPayload(
        segments=[
            Layer6SegmentValidation(
                segment_name="ops",
                sample_cqs=[
                    GeneratedCQSnapshot(text="What ops?", cq_type="DESCRIPTIVE"),
                ],
                approved_count=1,
                rejected_count=0,
            )
        ],
        validated_at=datetime.now(timezone.utc),
    )
    rebuilt = Layer6ValidationPayload.model_validate(
        payload.model_dump(mode="json")
    )
    assert rebuilt.segments[0].sample_cqs[0].cq_type == "DESCRIPTIVE"


def test_document_source_inclusion_kind_consistency():
    # glob requires glob field
    with pytest.raises(ValidationError):
        DocumentSource(path="x", inclusion_kind="glob")
    # explicit_list requires explicit_paths
    with pytest.raises(ValidationError):
        DocumentSource(path="x", inclusion_kind="explicit_list")
    # folder requires neither
    DocumentSource(path="x", inclusion_kind="folder")
