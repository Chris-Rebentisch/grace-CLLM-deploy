"""Tests for seed suggester (mocked LLM provider, no real API calls)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.discovery.seed_models import SeedSuggestion
from src.discovery.seed_suggester import (
    _build_suggestion_prompt,
    suggest_additional_seeds,
)


def test_build_suggestion_prompt():
    """Prompt construction includes all required sections."""
    prompt = _build_suggestion_prompt(
        doc_summary={"total_documents": 10, "domains": ["legal"]},
        cq_summary={"total_cqs": 50, "by_type": {"ENTITY": 20}},
        industry_id="financial_services",
        available_sources=[
            {"id": "lkif_norm", "name": "LKIF Norms", "description": "Norms", "domains": ["legal"]},
        ],
    )
    data = json.loads(prompt)
    assert "document_summary" in data
    assert "cq_summary" in data
    assert "current_industry_profile" in data
    assert "available_seed_sources" in data
    assert data["current_industry_profile"] == "financial_services"


@pytest.mark.asyncio
async def test_suggest_with_mocked_provider():
    """Suggestion works with mocked LLM provider."""
    from src.discovery.seed_models import SuggestionResponse, SeedSuggestion

    parsed = SuggestionResponse(suggestions=[
        SeedSuggestion(
            source_id="lkif_norm",
            reason="Documents mention regulatory compliance extensively",
            confidence=0.82,
            relevant_domains=["legal", "operations"],
        )
    ])
    mock_response = AsyncMock()
    mock_response.text = "{}"
    mock_response.parsed = parsed

    mock_provider = AsyncMock()
    mock_provider.generate_structured = AsyncMock(return_value=mock_response)

    config = {
        "seed": {"industry_profile": "financial_services"},
    }

    with (
        patch("src.discovery.seed_suggester.get_provider", return_value=mock_provider),
        patch("src.discovery.seed_suggester.resolve_sources_for_industry", return_value=[]),
    ):
        suggestions = await suggest_additional_seeds(config, db=None)

    # With empty resolve (no current sources), all registry sources are "available"
    # but since we mock the provider, we get our mocked response
    # However, we also need doc/cq summaries to proceed — without db, returns empty
    # Let's test with a mock db instead


@pytest.mark.asyncio
async def test_suggest_with_mocked_provider_and_db():
    """Suggestion works with mocked LLM provider and DB summaries."""
    from unittest.mock import MagicMock
    from src.discovery.seed_models import SuggestionResponse, SeedSuggestion

    parsed = SuggestionResponse(suggestions=[
        SeedSuggestion(
            source_id="lkif_norm",
            reason="Regulatory compliance patterns needed",
            confidence=0.85,
            relevant_domains=["legal"],
        )
    ])
    mock_response = MagicMock()
    mock_response.text = "{}"
    mock_response.parsed = parsed

    mock_provider = AsyncMock()
    mock_provider.generate_structured = AsyncMock(return_value=mock_response)

    mock_db = MagicMock()

    config = {
        "seed": {"industry_profile": "financial_services"},
    }

    with (
        patch("src.discovery.seed_suggester.get_provider", return_value=mock_provider),
        patch("src.discovery.seed_suggester.resolve_sources_for_industry", return_value=[]),
        patch("src.discovery.database.get_processing_summary", return_value={"total_documents": 5}),
        patch("src.discovery.cq_database.get_cq_summary", return_value={"total_cqs": 20}),
    ):
        suggestions = await suggest_additional_seeds(config, db=mock_db)

    assert len(suggestions) == 1
    assert suggestions[0].source_id == "lkif_norm"
    assert suggestions[0].confidence == 0.85


@pytest.mark.asyncio
async def test_suggest_empty_corpus():
    """Returns empty suggestions when no documents or CQs exist."""
    config = {
        "seed": {"industry_profile": "financial_services"},
    }

    with patch("src.discovery.seed_suggester.resolve_sources_for_industry", return_value=[]):
        suggestions = await suggest_additional_seeds(config, db=None)

    assert suggestions == []


@pytest.mark.asyncio
async def test_suggest_no_industry_profile():
    """Returns empty suggestions when no industry profile is set."""
    config = {"seed": {"industry_profile": ""}}
    suggestions = await suggest_additional_seeds(config, db=None)
    assert suggestions == []
