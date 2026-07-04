"""Tests for Layer 4 hypothesis synthesis models and orchestration.

CP2 contributes 5 model-schema tests (round-trip, discriminator,
0-null reject, 2-null reject, extra-forbid). CP9 contributes 3
synthesis-function tests (mocked LLM fixture parse, low_stability
prompt injection, type-alias discriminated-union round-trip).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.decomposition.models import (
    Hypothesis,  # noqa: F401 — exported alias
    Layer4HypothesisSet,
    NullHypothesis,
    ProposedSegment,
    SegmentedHypothesis,
    SynthesisMetadata,
)


def _segmented(name: str = "Segmented A", confidence: str = "medium") -> SegmentedHypothesis:
    return SegmentedHypothesis(
        name=name,
        segment_count=2,
        segments=[
            ProposedSegment(
                name="Alpha",
                description="alpha desc",
                representative_keywords=["a", "b"],
                representative_entities=["Acme"],
            ),
            ProposedSegment(
                name="Beta",
                description="beta desc",
                representative_keywords=["c"],
                representative_entities=["Beacon"],
            ),
        ],
        agreement_summary="agree",
        divergence_summary="diverge",
        confidence_band=confidence,  # type: ignore[arg-type]
        narrative_argument_for="for",
        narrative_argument_against="against",
    )


def _null(confidence: str = "low") -> NullHypothesis:
    return NullHypothesis(
        narrative_argument_for="for",
        narrative_argument_against="against",
        confidence_band=confidence,  # type: ignore[arg-type]
    )


def _meta(low_stability: bool = False) -> SynthesisMetadata:
    return SynthesisMetadata(
        model="qwen2.5:7b-instruct",
        low_stability_flag=low_stability,
        layer3_mean_pairwise_ari=0.72,
        generated_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc),
    )


# ---------- CP2 model-schema tests ----------


def test_layer4_hypothesis_set_round_trips_through_pydantic():
    """Model serializes and re-validates without loss."""
    hs = Layer4HypothesisSet(
        hypotheses=[_segmented(), _null()],
        synthesis_metadata=_meta(),
    )
    raw = hs.model_dump(mode="json")
    again = Layer4HypothesisSet.model_validate(raw)
    assert again.hypotheses[0].hypothesis_kind == "segmented"
    assert again.hypotheses[1].hypothesis_kind == "null"
    assert again.hypotheses[1].name == "Null hypothesis: undifferentiated whole"


def test_layer4_discriminated_union_json_schema_includes_discriminator():
    """JSON Schema for the union exposes ``hypothesis_kind`` discriminator."""
    schema = Layer4HypothesisSet.model_json_schema()
    # Ensure both variants are reachable through the schema.
    serialized = repr(schema)
    assert "hypothesis_kind" in serialized
    assert "segmented" in serialized
    assert "null" in serialized


def test_layer4_validator_rejects_zero_nulls():
    """A list with no NullHypothesis is rejected by the validator."""
    with pytest.raises(ValidationError) as exc:
        Layer4HypothesisSet(
            hypotheses=[_segmented("Seg1"), _segmented("Seg2")],
            synthesis_metadata=_meta(),
        )
    assert "exactly one NullHypothesis" in str(exc.value)


def test_layer4_validator_rejects_two_nulls():
    """A list with two NullHypothesis entries is rejected."""
    with pytest.raises(ValidationError) as exc:
        Layer4HypothesisSet(
            hypotheses=[_null(), _null()],
            synthesis_metadata=_meta(),
        )
    assert "exactly one NullHypothesis" in str(exc.value)


def test_layer4_extra_forbid_rejects_unknown_field():
    """``ConfigDict(extra='forbid')`` rejects additional keys."""
    raw = {
        "hypotheses": [
            _segmented().model_dump(),
            _null().model_dump(),
        ],
        "synthesis_metadata": _meta().model_dump(mode="json"),
        "ghost_field": "boo",
    }
    with pytest.raises(ValidationError):
        Layer4HypothesisSet.model_validate(raw)


def test_layer4_confidence_band_is_literal_only():
    """``confidence_band`` rejects numeric values (D120/D217)."""
    with pytest.raises(ValidationError):
        SegmentedHypothesis(
            name="seg",
            segment_count=1,
            segments=[
                ProposedSegment(name="A", description="d"),
            ],
            agreement_summary="x",
            divergence_summary="y",
            confidence_band="0.85",  # type: ignore[arg-type]
            narrative_argument_for="for",
            narrative_argument_against="against",
        )


# ---------- CP9 synthesis-function tests ----------


import asyncio
import json
from pathlib import Path
from typing import Any

from src.decomposition.config import DecompositionConfig
from src.decomposition.layer4_synthesize import (
    _LOW_STABILITY_CONTEXT,
    synthesize_hypotheses,
)
from src.decomposition.models import (
    EmbeddingProvenance,
    Layer1Summary,
    Layer2Decision,
    Layer3Decision,
    LeidenSeedRun,
    UmapParams,
)


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "layer4"

_FIXTURES = [
    "clear_multi_segment.json",
    "ambiguous_2_vs_3.json",
    "small_founder_null_hypothesis.json",
]


def _layer1_stub() -> Layer1Summary:
    return Layer1Summary(archive_root="/tmp/archive", total_files=42, files=[], folders=[])


def _layer2_stub() -> Layer2Decision:
    return Layer2Decision(
        algorithm="hdbscan",
        cluster_count=3,
        outlier_count=0,
        outlier_ratio_at_gate=0.0,
        outlier_ratio_gate=0.30,
        cluster_labels=[0, 1, 2],
        umap=UmapParams(),
        embedding=EmbeddingProvenance(model="nomic-embed-text", dimension=768, document_count=42),
    )


def _layer3_stub(low_stability: bool = False) -> Layer3Decision:
    return Layer3Decision(
        document_count=42,
        edge_count=10,
        leiden_runs=[LeidenSeedRun(seed=s, modularity=0.5, community_count=3) for s in (1, 2, 3, 4, 5)],
        selected_seed=1,
        selected_modularity=0.5,
        mean_pairwise_ari=0.30 if low_stability else 0.92,
        low_stability_flag=low_stability,
        community_assignments={"a": 0, "b": 1},
    )


class _CapturingLLM:
    """Mock LLM returning a canned string and recording each call."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.response


def test_layer4_synthesis_each_fixture_parses_through_instructor():
    """CP9: each of the three fixtures parses cleanly via the synthesis function."""
    cfg = DecompositionConfig()
    for fixture_name in _FIXTURES:
        payload = (_FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")
        # Fixture itself must be a valid Layer4HypothesisSet.
        Layer4HypothesisSet.model_validate(json.loads(payload))

        llm = _CapturingLLM(response=payload)
        low_stab = "ambiguous" in fixture_name or "founder" in fixture_name
        result = asyncio.run(
            synthesize_hypotheses(
                _layer1_stub(),
                _layer2_stub(),
                _layer3_stub(low_stability=low_stab),
                llm,
                cfg,
            )
        )
        assert isinstance(result, Layer4HypothesisSet)
        null_count = sum(1 for h in result.hypotheses if h.hypothesis_kind == "null")
        assert null_count == 1


def test_layer4_synthesis_low_stability_flag_injects_user_prompt():
    """CP9: low_stability_flag=True inserts the literal context block; False omits it."""
    payload = (_FIXTURE_DIR / "clear_multi_segment.json").read_text(encoding="utf-8")
    cfg = DecompositionConfig()

    llm_true = _CapturingLLM(response=payload)
    asyncio.run(
        synthesize_hypotheses(
            _layer1_stub(),
            _layer2_stub(),
            _layer3_stub(low_stability=True),
            llm_true,
            cfg,
        )
    )
    user_prompt_true = llm_true.calls[0]["kwargs"].get("user_prompt", "")
    assert _LOW_STABILITY_CONTEXT in user_prompt_true

    llm_false = _CapturingLLM(response=payload)
    asyncio.run(
        synthesize_hypotheses(
            _layer1_stub(),
            _layer2_stub(),
            _layer3_stub(low_stability=False),
            llm_false,
            cfg,
        )
    )
    user_prompt_false = llm_false.calls[0]["kwargs"].get("user_prompt", "")
    assert _LOW_STABILITY_CONTEXT not in user_prompt_false


def test_layer4_synthesis_pydantic_29_list_of_discriminated_union_pattern():
    """CP9: Pydantic 2.9 type-alias discriminated-union list works end-to-end.

    Regression for R5 — list[Hypothesis] with Hypothesis declared as
    ``Annotated[Union[...], Field(discriminator=...)]`` round-trips.
    """
    payload = (_FIXTURE_DIR / "ambiguous_2_vs_3.json").read_text(encoding="utf-8")
    llm = _CapturingLLM(response=payload)
    cfg = DecompositionConfig()
    result = asyncio.run(
        synthesize_hypotheses(
            _layer1_stub(),
            _layer2_stub(),
            _layer3_stub(low_stability=True),
            llm,
            cfg,
        )
    )
    # Round-trip through the discriminated-union list.
    dumped = result.model_dump(mode="json")
    again = Layer4HypothesisSet.model_validate(dumped)
    kinds = [h.hypothesis_kind for h in again.hypotheses]
    assert kinds.count("null") == 1
    assert "segmented" in kinds
    # Ensure each segmented entry has at least one segment.
    for h in again.hypotheses:
        if h.hypothesis_kind == "segmented":
            assert len(h.segments) >= 1
