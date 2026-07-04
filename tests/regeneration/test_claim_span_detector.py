"""Tests for ClaimSpanDetector (§7 of chunk-23-spec.md)."""

from __future__ import annotations

import pytest

from src.regeneration.claim_span_detector import ClaimSpanDetector
from src.regeneration.regeneration_config import RegenSettings
from src.retrieval.retrieval_models import RankedResult, RetrievalResponse


def _retr(results: list[RankedResult]) -> RetrievalResponse:
    return RetrievalResponse(
        query="q",
        results=results,
        serialized_context="",
        serialization_format="template",
        total_candidates=len(results),
        strategy_contributions={},
        latency_ms={},
    )


def _ranked(name: str, grace_id: str, rerank: float) -> RankedResult:
    return RankedResult(
        grace_id=grace_id,
        entity_type="Company",
        name=name,
        rerank_score=rerank,
        rrf_score=0.0,
        contributing_strategies=["semantic"],
    )


def test_sentence_per_span_baseline_pre_merge() -> None:
    settings = RegenSettings()
    det = ClaimSpanDetector(settings)
    # Distinct bands to prevent merge.
    retr = _retr(
        [
            _ranked("Apple", "g1", 0.9),
            _ranked("Microsoft", "g2", 0.3),
        ]
    )
    text = "Apple is mentioned. Microsoft is also here. Nothing matches this."
    spans, note = det.detect(text, retr)
    assert note is None
    # Three sentences, three distinct band/id combos → no merge.
    assert len(spans) == 3


def test_band_mapping_by_rerank_score() -> None:
    settings = RegenSettings()
    det = ClaimSpanDetector(settings)
    retr = _retr(
        [
            _ranked("Alpha", "g1", 0.9),
            _ranked("Beta", "g2", 0.6),
            _ranked("Gamma", "g3", 0.1),
        ]
    )
    text = "Alpha is here. Beta is here. Gamma is here."
    spans, _ = det.detect(text, retr)
    bands = {s.supporting_grace_ids[0]: s.certainty_band for s in spans}
    assert bands["g1"] == "high"
    assert bands["g2"] == "medium"
    assert bands["g3"] == "low"


def test_no_name_match_is_insufficient_evidence_singleton() -> None:
    settings = RegenSettings()
    det = ClaimSpanDetector(settings)
    retr = _retr([_ranked("Apple", "g1", 0.9)])
    # Sentence 1 matches Apple → not all insufficient, so result is retained.
    text = "Apple is mentioned. This sentence has no matches."
    spans, note = det.detect(text, retr)
    assert note is None
    bands = [s.certainty_band for s in spans]
    assert "insufficient_evidence" in bands
    assert "high" in bands


def test_span_confidence_always_low_in_v1() -> None:
    settings = RegenSettings()
    det = ClaimSpanDetector(settings)
    retr = _retr([_ranked("Apple", "g1", 0.9)])
    text = "Apple is present. Something else entirely."
    spans, _ = det.detect(text, retr)
    for s in spans:
        assert s.span_confidence == "low"


def test_supporting_grace_ids_from_case_insensitive_match() -> None:
    settings = RegenSettings()
    det = ClaimSpanDetector(settings)
    retr = _retr(
        [
            _ranked("Acme Corp", "g1", 0.9),
            _ranked("widget", "g2", 0.7),
        ]
    )
    text = "ACME CORP released a Widget yesterday."
    spans, _ = det.detect(text, retr)
    assert len(spans) == 1
    assert spans[0].supporting_grace_ids == ["g1", "g2"]


def test_adjacent_merge_collapses_same_ids_same_band() -> None:
    settings = RegenSettings()
    det = ClaimSpanDetector(settings)
    retr = _retr([_ranked("Apple", "g1", 0.9)])
    text = "Apple was founded. Apple grew fast. Apple is a giant."
    spans, _ = det.detect(text, retr)
    # All three sentences reference Apple with band=high and same ids → merge.
    assert len(spans) == 1
    assert spans[0].sentence_indices == [0, 1, 2]
    assert spans[0].certainty_band == "high"
    assert spans[0].supporting_grace_ids == ["g1"]


def test_all_insufficient_returns_empty_with_note() -> None:
    settings = RegenSettings()
    det = ClaimSpanDetector(settings)
    retr = _retr([_ranked("Nothing", "g1", 0.9)])
    text = "Foo bar. Baz qux. Nothing relevant whatsoever here really."
    spans, note = det.detect(text, retr)
    # "Nothing" is a name; "Nothing relevant..." would match, so tweak
    text2 = "Foo bar. Baz qux. Lorem ipsum dolor."
    spans2, note2 = det.detect(text2, retr)
    assert spans2 == []
    assert note2 == "no_substantive_claims_detected"


def test_llm_judged_and_hybrid_modes_raise_not_implemented() -> None:
    for mode in ("llm_judged", "hybrid"):
        settings = RegenSettings(span_detector_mode=mode)
        det = ClaimSpanDetector(settings)
        retr = _retr([])
        with pytest.raises(NotImplementedError):
            det.detect("anything.", retr)
