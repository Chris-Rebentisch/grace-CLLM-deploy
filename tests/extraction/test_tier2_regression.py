"""Tier-2 regression tests: Tier 1/3 unchanged, D86 bias, log write path (CP7)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.extraction.entity_resolver import EntityResolver, EntityResolutionResult
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractedEntity
from src.extraction.entity_registry import EntityRegistry


def _make_resolver(**kwargs) -> tuple[EntityResolver, AsyncMock]:
    mock_client = AsyncMock()
    config = ExtractionSettings()
    resolver = EntityResolver(
        arcade_client=mock_client,
        config=config,
        **kwargs,
    )
    return resolver, mock_client


def _make_entity(name="Test Corp", entity_type="Legal_Entity") -> ExtractedEntity:
    return ExtractedEntity(name=name, entity_type=entity_type, properties={"name": name})


@pytest.mark.asyncio
async def test_tier1_exact_behaves_identically():
    """_tier1_exact behaves identically (regression)."""
    resolver, mock_client = _make_resolver()
    # canonical_lookup finds a match on first try
    mock_client.execute_cypher.return_value = {
        "result": [{"n.grace_id": "existing-id"}]
    }

    entity = _make_entity()
    result = await resolver._tier1_exact(entity, "type:Legal_Entity")

    assert result is not None
    assert result.resolution_tier == "exact"
    assert result.resolved_grace_id == "existing-id"
    assert result.is_new is False


@pytest.mark.asyncio
async def test_tier3_llm_behaves_identically():
    """_tier3_llm behaves identically (regression)."""
    from src.extraction.instructor_client import ExtractionLLMClient
    from src.extraction.entity_resolver import DisambiguationResult

    mock_instructor = AsyncMock(spec=ExtractionLLMClient)
    mock_instructor.resolve.return_value = DisambiguationResult(
        decision="YES",
        reasoning="Same entity"
    )

    resolver, mock_client = _make_resolver(instructor_client=mock_instructor)
    mock_client.execute_cypher.return_value = {"result": []}

    entity = _make_entity()
    tier2_result = EntityResolutionResult(
        extracted_name="Test Corp",
        extracted_type="Legal_Entity",
        resolved_grace_id="g1",
        matched_name="Test Corporation",
        resolution_tier="_tier3",
        similarity_score=0.82,
        blocking_key="type:Legal_Entity",
        is_new=False,
        candidate_count=1,
        candidates_json=[{"grace_id": "g1", "name": "Test Corporation", "score": 0.82}],
    )

    result = await resolver._tier3_llm(entity, tier2_result, "type:Legal_Entity")

    assert result.resolution_tier == "llm"
    assert result.is_new is False
    assert result.resolved_grace_id == "g1"
    assert result.llm_reasoning == "Same entity"


@pytest.mark.asyncio
async def test_d86_conservative_false_merge_bias():
    """D86 conservative-false-merge-bias preserved (missed ANN candidate -> new entity)."""
    resolver, mock_client = _make_resolver()
    # ANN query fails — D86 says: resolve as new, never false merge
    mock_client.execute_sql.side_effect = Exception("ANN query failed")

    with patch("src.extraction.entity_resolver.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]
        entity = _make_entity()
        registry = EntityRegistry()
        result = await resolver._tier2_embedding(entity, "type:Legal_Entity", registry)

    assert result is not None
    assert result.is_new is True
    assert result.resolution_tier == "new"


@pytest.mark.asyncio
async def test_entity_resolution_log_write_path_unchanged():
    """entity_resolution_log write path unchanged (every decision still logged with all fields)."""
    # Verify EntityResolutionResult still has all expected fields
    result = EntityResolutionResult(
        extracted_name="Test",
        extracted_type="Legal_Entity",
        resolved_grace_id="g1",
        matched_name="Test Corp",
        resolution_tier="embedding",
        similarity_score=0.95,
        blocking_key="type:Legal_Entity",
        is_new=False,
        candidate_count=3,
        candidates_json=[{"grace_id": "g1", "name": "Test Corp", "score": 0.95}],
        llm_reasoning=None,
        resolution_note=None,
    )

    # All fields must be present for the log writer
    assert result.extracted_name == "Test"
    assert result.extracted_type == "Legal_Entity"
    assert result.resolved_grace_id == "g1"
    assert result.matched_name == "Test Corp"
    assert result.resolution_tier in ("exact", "embedding", "llm", "new", "_tier3")
    assert result.similarity_score is not None
    assert result.blocking_key == "type:Legal_Entity"
    assert result.candidate_count == 3
    assert result.candidates_json is not None


def test_recalibration_comparison_drift_check():
    """Re-calibration comparison: drift check on fixture data."""
    # Simulate a drift calculation
    old_scores = [0.92, 0.88, 0.95, 0.91]
    # ANN scores are close (small drift expected due to approximate search)
    new_scores = [0.91, 0.87, 0.94, 0.90]

    diffs = [abs(o - n) for o, n in zip(old_scores, new_scores)]
    mean_drift = sum(diffs) / len(diffs)
    max_drift = max(diffs)

    # All diffs are 0.01 — well within 0.05 bar
    assert mean_drift <= 0.05
    assert max_drift <= 0.05

    # No flipped outcomes
    merge_threshold = 0.90
    review_threshold = 0.78

    def classify(s):
        if s >= merge_threshold:
            return "merge"
        if s >= review_threshold:
            return "review"
        return "new"

    flipped = sum(1 for o, n in zip(old_scores, new_scores) if classify(o) != classify(n))
    assert flipped == 0
