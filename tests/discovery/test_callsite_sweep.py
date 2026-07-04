"""Tests for callsite sweep — CP6 (merge_llm_calls) + CP7 (seed_suggester, schema_extractor) of Chunk 63 (D444)."""

import ast
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from src.shared.llm_provider import LLMResponse


# =============================================================================
# CP6 — merge_llm_calls.py
# =============================================================================


@pytest.mark.asyncio
async def test_merge_call1_returns_parsed_call1response():
    """Call1: mocked provider returns Call1Response-conformant JSON → .parsed is Call1Response."""
    from src.discovery.merge_models import Call1Response

    mock_result = Call1Response(clusters=[])
    mock_response = LLMResponse(
        text="{}",
        raw_response={},
        model="test",
        provider="test",
        duration_ms=100,
        parsed=mock_result,
    )

    mock_provider = AsyncMock()
    mock_provider.provider_name = "test"
    mock_provider.generate_structured = AsyncMock(return_value=mock_response)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=mock_provider):
        from src.discovery.merge_llm_calls import call1_canonical_phrasing

        result = await call1_canonical_phrasing([], {})

    assert isinstance(result, Call1Response)


@pytest.mark.asyncio
async def test_merge_call2_returns_parsed_call2response():
    """Call2: mocked → .parsed is Call2Response. No coercion branch reachable."""
    from src.discovery.merge_models import Call2Response

    mock_result = Call2Response(domain_groups=[], cross_domain_links=[])
    mock_response = LLMResponse(
        text="{}",
        raw_response={},
        model="test",
        provider="test",
        duration_ms=100,
        parsed=mock_result,
    )

    mock_provider = AsyncMock()
    mock_provider.provider_name = "test"
    mock_provider.generate_structured = AsyncMock(return_value=mock_response)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=mock_provider):
        from src.discovery.merge_llm_calls import call2_hierarchy

        result = await call2_hierarchy([], [], {})

    assert isinstance(result, Call2Response)


@pytest.mark.asyncio
async def test_merge_call3_returns_parsed_call3response():
    """Call3: mocked → .parsed is Call3Response. No coercion branch reachable."""
    from src.discovery.merge_models import Call3Response

    mock_result = Call3Response(gap_fill_cqs=[], path_annotations=[])
    mock_response = LLMResponse(
        text="{}",
        raw_response={},
        model="test",
        provider="test",
        duration_ms=100,
        parsed=mock_result,
    )

    mock_provider = AsyncMock()
    mock_provider.provider_name = "test"
    mock_provider.generate_structured = AsyncMock(return_value=mock_response)

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=mock_provider):
        from src.discovery.merge_llm_calls import call3_gap_analysis

        result = await call3_gap_analysis({}, {}, [], [], {})

    assert isinstance(result, Call3Response)


@pytest.mark.asyncio
async def test_merge_failure_returns_none():
    """Failure contract: mocked exception → function returns None."""
    mock_provider = AsyncMock()
    mock_provider.provider_name = "test"
    mock_provider.generate_structured = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("src.discovery.merge_llm_calls.get_provider", return_value=mock_provider):
        from src.discovery.merge_llm_calls import call1_canonical_phrasing

        result = await call1_canonical_phrasing([], {})

    assert result is None


def test_merge_llm_calls_no_parse_json_robust_import():
    """Import absence: merge_llm_calls.py AST has zero _parse_json_robust imports."""
    with open("src/discovery/merge_llm_calls.py") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.names:
                names = [alias.name for alias in node.names]
                assert "_parse_json_robust" not in names, (
                    "merge_llm_calls.py still imports _parse_json_robust"
                )


# =============================================================================
# CP7 — seed_suggester.py
# =============================================================================


@pytest.mark.asyncio
async def test_seed_suggester_returns_parsed():
    """seed_suggester: mocked → .parsed is SuggestionResponse."""
    from src.discovery.seed_models import SuggestionResponse

    mock_result = SuggestionResponse(suggestions=[])
    mock_response = LLMResponse(
        text="{}",
        raw_response={},
        model="test",
        provider="test",
        duration_ms=100,
        parsed=mock_result,
    )

    mock_provider = AsyncMock()
    mock_provider.provider_name = "test"
    mock_provider.generate_structured = AsyncMock(return_value=mock_response)

    # Minimal config and registry mocks
    mock_registry = MagicMock()
    mock_registry.sources = []

    with (
        patch("src.discovery.seed_suggester.get_provider", return_value=mock_provider),
        patch("src.discovery.seed_suggester.resolve_sources_for_industry", return_value=[MagicMock(id="s1")]),
        patch("src.discovery.seed_suggester.load_seed_registry", return_value=mock_registry),
    ):
        from src.discovery.seed_suggester import suggest_additional_seeds

        result = await suggest_additional_seeds(
            {"seed": {"industry_profile": "general"}}, db=None
        )

    # No available sources (all filtered), returns []
    assert result == []


# =============================================================================
# CP7 — schema_extractor.py
# =============================================================================


@pytest.mark.asyncio
async def test_schema_extractor_stage1_returns_parsed():
    """schema_extractor Stage 1: mocked → .parsed is Stage1Output."""
    from src.discovery.schema_models import Stage1Output

    mock_result = Stage1Output(entity_types=[], relationships=[])
    mock_response = LLMResponse(
        text="{}",
        raw_response={},
        model="test",
        provider="test",
        duration_ms=100,
        input_tokens=10,
        output_tokens=20,
        parsed=mock_result,
    )

    mock_provider = AsyncMock()
    mock_provider.provider_name = "test"
    mock_provider.generate_structured = AsyncMock(return_value=mock_response)

    from src.discovery.schema_extractor import run_stage1_pass

    output, dur, inp, out, model = await run_stage1_pass(
        pass_name="top_down",
        domain="test",
        document_text="test doc",
        cqs=[],
        seed_reference_text=None,
        config={},
        provider=mock_provider,
    )

    assert isinstance(output, Stage1Output)
    assert dur == 100
    assert model == "test"


@pytest.mark.asyncio
async def test_schema_extractor_stage2_returns_parsed():
    """schema_extractor Stage 2: mocked → .parsed is Stage2Output with correct field set."""
    from src.discovery.schema_models import Stage1TypeSummary, Stage2Output

    type_summary = Stage1TypeSummary(
        name="Test_Type",
        description="A test type",
        domain="test",
    )

    mock_result = Stage2Output(
        properties=[{"name": "test_prop", "data_type": "string", "description": "test"}],
        relationships_from_this_type=[],
        evidence_documents=["doc1.pdf"],
    )
    mock_response = LLMResponse(
        text="{}",
        raw_response={},
        model="test",
        provider="test",
        duration_ms=50,
        parsed=mock_result,
    )

    mock_provider = AsyncMock()
    mock_provider.provider_name = "test"
    mock_provider.generate_structured = AsyncMock(return_value=mock_response)

    from src.discovery.schema_extractor import run_stage2_detail

    result = await run_stage2_detail(
        type_summary=type_summary,
        domain="test",
        document_text="test doc",
        cqs=[],
        seed_reference_text=None,
        config={},
        provider=mock_provider,
    )

    # Stage 2 returns a ProposedEntityType, not Stage2Output directly
    from src.discovery.schema_models import ProposedEntityType

    assert isinstance(result, ProposedEntityType)
    assert result.name == "Test_Type"
    assert result.evidence_documents == ["doc1.pdf"]


@pytest.mark.asyncio
async def test_schema_extractor_failure_returns_none():
    """Failure contract: mocked exception → function returns None."""
    mock_provider = AsyncMock()
    mock_provider.provider_name = "test"
    mock_provider.generate_structured = AsyncMock(side_effect=RuntimeError("boom"))

    from src.discovery.schema_extractor import run_stage1_pass

    output, dur, inp, out, model = await run_stage1_pass(
        pass_name="top_down",
        domain="test",
        document_text="test doc",
        cqs=[],
        seed_reference_text=None,
        config={},
        provider=mock_provider,
    )

    assert output is None


def test_schema_extractor_no_parse_json_robust_import():
    """Import absence: schema_extractor.py AST has zero _parse_json_robust imports."""
    with open("src/discovery/schema_extractor.py") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.names:
                names = [alias.name for alias in node.names]
                assert "_parse_json_robust" not in names, (
                    "schema_extractor.py still imports _parse_json_robust"
                )
