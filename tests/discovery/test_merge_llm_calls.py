"""Tests for Tier 3: LLM calls for canonical phrasing, hierarchy, and gap analysis."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.discovery.merge_llm_calls import (
    call1_canonical_phrasing,
    call2_hierarchy,
    call3_gap_analysis,
)
from src.discovery.merge_models import Call1Response, Call2Response, Call3Response
from src.shared.llm_provider import LLMResponse


def _mock_provider(parsed_model_instance, temperature_check: float | None = None):
    """Create a mocked LLM provider that returns generate_structured with .parsed set."""
    provider = MagicMock()
    provider.provider_name = "ollama"
    provider.model = "qwen2.5:7b"
    provider.generate_structured = AsyncMock(
        return_value=LLMResponse(
            text="{}",
            model="qwen2.5:7b",
            input_tokens=100,
            output_tokens=50,
            duration_ms=1000,
            provider="ollama",
            parsed=parsed_model_instance,
        )
    )
    return provider


def _sample_clusters_data():
    """Sample cluster data for Call 1 tests."""
    return [
        {
            "cluster_label": 0,
            "members": [
                {"index": 0, "text": "What types of insurance exist?", "source_pass": "top_down", "domain": "insurance"},
                {"index": 1, "text": "What kinds of insurance policies are there?", "source_pass": "bottom_up", "domain": "insurance"},
            ],
        }
    ]


# --- Call 1 tests ---


@pytest.mark.asyncio
async def test_call1_prompt_construction():
    """Verify generate_structured is called with correct arguments."""
    parsed = Call1Response(clusters=[
        {"cluster_label": 0, "canonical_text": "What types of insurance exist?", "canonical_index": 0, "split_recommendations": []}
    ])
    provider = _mock_provider(parsed)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        await call1_canonical_phrasing(_sample_clusters_data(), {})

    # Verify generate_structured was called
    call_args = provider.generate_structured.call_args
    user_prompt = call_args.kwargs.get("user_prompt") or call_args[1] if len(call_args[0]) > 1 else ""
    assert call_args is not None
    assert call_args.kwargs.get("response_model") == Call1Response


@pytest.mark.asyncio
async def test_call1_response_parsing():
    """Mock LLM response with valid Call1Response, verify parsing."""
    parsed = Call1Response(clusters=[
        {"cluster_label": 0, "canonical_text": "What types of insurance exist?", "canonical_index": 0, "split_recommendations": []}
    ])
    provider = _mock_provider(parsed)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        result = await call1_canonical_phrasing(_sample_clusters_data(), {})

    assert result is not None
    assert isinstance(result, Call1Response)
    assert len(result.clusters) == 1
    assert result.clusters[0].canonical_text == "What types of insurance exist?"
    assert result.clusters[0].canonical_index == 0


# --- Call 2 tests ---


@pytest.mark.asyncio
async def test_call2_prompt_construction():
    """Verify generate_structured is called with Call2Response model."""
    parsed = Call2Response(
        domain_groups=[{"domain": "insurance", "sub_domains": [{"name": "policy_types", "cq_ids": ["cq-1"]}]}],
        cross_domain_links=[],
    )
    canonical_cqs = [{"id": "cq-1", "text": "What types of insurance?", "domain": "insurance", "cq_type": "SCOPING"}]
    singletons = []
    provider = _mock_provider(parsed)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        await call2_hierarchy(canonical_cqs, singletons, {})

    call_args = provider.generate_structured.call_args
    assert call_args.kwargs.get("response_model") == Call2Response


@pytest.mark.asyncio
async def test_call2_response_parsing():
    """Mock valid Call2Response."""
    parsed = Call2Response(
        domain_groups=[{"domain": "insurance", "sub_domains": [{"name": "policy_types", "cq_ids": ["cq-1"]}]}],
        cross_domain_links=[{"source_cq_id": "cq-1", "target_cq_id": "cq-2", "relationship": "covers"}],
    )
    provider = _mock_provider(parsed)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        result = await call2_hierarchy([{"id": "cq-1"}], [], {})

    assert result is not None
    assert isinstance(result, Call2Response)
    assert len(result.domain_groups) == 1
    assert result.domain_groups[0].domain == "insurance"
    assert len(result.cross_domain_links) == 1


# --- Call 3 tests ---


@pytest.mark.asyncio
async def test_call3_prompt_construction():
    """Verify generate_structured is called with Call3Response model."""
    parsed = Call3Response(gap_fill_cqs=[], path_annotations=[])
    provider = _mock_provider(parsed)
    gap_report = {"gaps": [{"gap_type": "domain_gap", "target": "legal"}], "domain_coverage": {}, "type_coverage": {}}

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        await call3_gap_analysis({}, gap_report, [], [], {"gap_fill_max_cqs": 15})

    call_args = provider.generate_structured.call_args
    assert call_args.kwargs.get("response_model") == Call3Response


@pytest.mark.asyncio
async def test_call3_response_parsing():
    """Mock valid Call3Response."""
    parsed = Call3Response(
        gap_fill_cqs=[
            {"canonical_text": "What legal entities are involved?", "domain": "legal", "cq_type": "SCOPING", "gap_addressed": "domain_gap", "rationale": "No legal CQs"}
        ],
        path_annotations=[
            {"cq_id": "cq-1", "expected_path": "Company -> owns -> Policy", "path_types": ["Company", "Policy"], "path_properties": ["owns"]}
        ],
    )
    provider = _mock_provider(parsed)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        result = await call3_gap_analysis({}, {}, [], [], {"gap_fill_max_cqs": 15})

    assert result is not None
    assert isinstance(result, Call3Response)
    assert len(result.gap_fill_cqs) == 1
    assert len(result.path_annotations) == 1


@pytest.mark.asyncio
async def test_gap_fill_cap_enforcement():
    """More than 15 gap fills -> truncated + warning."""
    gap_fills = [
        {"canonical_text": f"Gap fill {i}?", "domain": "legal", "cq_type": "SCOPING", "gap_addressed": "domain_gap", "rationale": "test"}
        for i in range(20)
    ]
    parsed = Call3Response(gap_fill_cqs=gap_fills, path_annotations=[])
    provider = _mock_provider(parsed)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        result = await call3_gap_analysis({}, {}, [], [], {"gap_fill_max_cqs": 15})

    assert result is not None
    assert len(result.gap_fill_cqs) == 15


@pytest.mark.asyncio
async def test_invalid_json_returns_none():
    """Exception in generate_structured -> returns None."""
    provider = MagicMock()
    provider.provider_name = "ollama"
    provider.generate_structured = AsyncMock(side_effect=RuntimeError("LLM error"))

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        result = await call1_canonical_phrasing(_sample_clusters_data(), {})

    assert result is None


@pytest.mark.asyncio
async def test_call1_uses_provider():
    """Verify get_provider() is called."""
    parsed = Call1Response(clusters=[
        {"cluster_label": 0, "canonical_text": "Test", "canonical_index": 0, "split_recommendations": []}
    ])
    provider = _mock_provider(parsed)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider) as mock_get:
        await call1_canonical_phrasing(_sample_clusters_data(), {})

    mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_call3_temperature():
    """Verify temperature 0.3 for gap analysis."""
    parsed = Call3Response(gap_fill_cqs=[], path_annotations=[])
    provider = _mock_provider(parsed)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=provider):
        await call3_gap_analysis({}, {}, [], [], {"gap_fill_max_cqs": 15})

    call_args = provider.generate_structured.call_args
    temperature = call_args.kwargs.get("temperature")
    assert temperature == pytest.approx(0.3)
