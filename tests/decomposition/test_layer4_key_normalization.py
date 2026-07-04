"""F-59 regression: Layer-4 synthesis must tolerate Claude's ``segment_name``
field drift (the Anthropic path does not strictly enforce the schema keys the
way Ollama's XGrammar does), which otherwise 12x-ValidationError'd into
``paused_pre_layer4``.
"""

from __future__ import annotations

import pytest

from src.decomposition.layer4_synthesize import (
    _PROMPT_FILE,
    _normalize_hypothesis_keys,
)
from src.decomposition.models import Layer4HypothesisSet, ProposedSegment


def test_segment_name_renamed_to_name():
    data = {
        "hypotheses": [
            {"hypothesis_kind": "segmented", "segment_name": "Operations"},
            {"hypothesis_kind": "null", "name": "No meaningful split"},
        ]
    }
    out = _normalize_hypothesis_keys(data)
    assert out["hypotheses"][0]["name"] == "Operations"
    assert "segment_name" not in out["hypotheses"][0]
    # An already-correct hypothesis is untouched.
    assert out["hypotheses"][1]["name"] == "No meaningful split"


def test_existing_name_wins_over_segment_name():
    data = {"hypotheses": [{"name": "Keep", "segment_name": "Drop"}]}
    out = _normalize_hypothesis_keys(data)
    assert out["hypotheses"][0]["name"] == "Keep"


def test_no_hypotheses_is_noop():
    assert _normalize_hypothesis_keys({}) == {}
    assert _normalize_hypothesis_keys({"hypotheses": None}) == {"hypotheses": None}


# ---------------------------------------------------------------------------
# F-032d / ISS-0021 — tier-b prompt-schema drift tolerance.
# The Anthropic structured-output API rejects the discriminated-union schema
# (``oneOf`` unsupported) → tier-b prompt fallback, whose output has been
# observed carrying ``segment_name`` / ``segment_id`` where the models want
# ``name`` / no id. Both shapes must now validate directly into
# ``Layer4HypothesisSet`` (AliasChoices + pre-validation ``segment_id`` drop).
# ---------------------------------------------------------------------------


_METADATA = {
    "model": "claude-haiku",
    "low_stability_flag": False,
    "layer3_mean_pairwise_ari": 0.72,
    "generated_at": "2026-07-01T00:00:00Z",
}


def _payload(segment: dict, hypothesis_name_key: str = "name") -> dict:
    return {
        "hypotheses": [
            {
                "hypothesis_kind": "segmented",
                hypothesis_name_key: "Two-segment org",
                "segment_count": 1,
                "segments": [segment],
                "agreement_summary": "a",
                "divergence_summary": "d",
                "confidence_band": "low",
                "narrative_argument_for": "f",
                "narrative_argument_against": "a",
            },
            {
                "hypothesis_kind": "null",
                hypothesis_name_key: "No meaningful split",
                "narrative_argument_for": "f",
                "narrative_argument_against": "a",
                "confidence_band": "medium",
            },
        ],
        "synthesis_metadata": _METADATA,
    }


def test_legacy_segment_name_and_segment_id_validate():
    """Tier-b legacy vocabulary (``segment_name`` + ``segment_id``) validates."""
    payload = _payload(
        {
            "segment_name": "Operations",
            "segment_id": "seg-1",
            "description": "ops",
        },
        hypothesis_name_key="segment_name",
    )
    hyp_set = Layer4HypothesisSet.model_validate(payload)
    assert hyp_set.hypotheses[0].name == "Two-segment org"
    assert hyp_set.hypotheses[0].segments[0].name == "Operations"
    # ``segment_id`` is dropped; serialization stays canonical.
    dumped = hyp_set.hypotheses[0].segments[0].model_dump()
    assert "segment_id" not in dumped
    assert dumped["name"] == "Operations"


def test_canonical_name_vocabulary_validates():
    """The canonical shape (``name``, no id) still validates unchanged."""
    payload = _payload({"name": "Operations", "description": "ops"})
    hyp_set = Layer4HypothesisSet.model_validate(payload)
    assert hyp_set.hypotheses[0].segments[0].name == "Operations"
    assert hyp_set.hypotheses[1].name == "No meaningful split"


def test_other_extra_segment_keys_still_forbidden():
    """Tolerance is scoped to ``segment_id`` — extra='forbid' stays intact."""
    payload = _payload(
        {"name": "Operations", "description": "ops", "bogus_key": 1}
    )
    with pytest.raises(Exception):
        Layer4HypothesisSet.model_validate(payload)


def test_tier_b_prompt_field_vocabulary_matches_model():
    """F-032d / ISS-0021: the tier-b prompt must name the model's actual
    segment fields and explicitly forbid the legacy drift vocabulary."""
    prompt = _PROMPT_FILE.read_text(encoding="utf-8")
    # Every ProposedSegment field is named verbatim in the prompt.
    for field_name in ProposedSegment.model_fields:
        assert f'"{field_name}"' in prompt, (
            f"tier-b prompt does not mention segment field {field_name!r}"
        )
    # The legacy vocabulary is explicitly prohibited, not prescribed.
    assert 'MUST use the literal key ``"name"``' in prompt
    assert 'no ``"segment_id"``' in prompt
