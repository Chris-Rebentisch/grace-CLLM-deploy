"""Tests for subgraph serialization (template + Turtle + LLM)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import rdflib
import pytest

from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import RankedResult
from src.retrieval.serializer import (
    LLMSerializer,
    TemplateSerializer,
    TurtleSerializer,
    get_serializer,
)


def _result(grace_id: str, name: str, entity_type: str = "Entity") -> RankedResult:
    return RankedResult(
        grace_id=grace_id,
        entity_type=entity_type,
        name=name,
        properties={"name": name, "jurisdiction": "BVI"},
        rerank_score=0.9,
        rrf_score=0.5,
        contributing_strategies=["graph"],
    )


def test_template_serializer_formats():
    """TemplateSerializer formats entities and relationships."""
    results = [_result("id-1", "Acme", "Legal_Entity")]
    rels = [{
        "source_name": "Acme",
        "target_name": "Cedar Cay",
        "relationship_type": "owns",
        "source_grace_id": "id-1",
        "target_grace_id": "id-2",
    }]
    serializer = TemplateSerializer()
    output = serializer.serialize(results, rels)
    assert 'Legal_Entity' in output
    assert 'Acme' in output
    assert '--[owns]-->' in output
    assert 'Cedar Cay' in output


def test_template_serializer_token_budget():
    """TemplateSerializer respects token budget (truncates)."""
    results = [_result(f"id-{i}", f"Entity{i}") for i in range(100)]
    serializer = TemplateSerializer()
    output = serializer.serialize(results, [], token_budget=10)
    # 10 tokens ~ 40 chars. Should be much shorter than all 100 entities
    assert len(output) < 200


def test_turtle_serializer_valid():
    """TurtleSerializer produces valid Turtle parseable by rdflib."""
    results = [_result("id-1", "Acme", "Legal_Entity")]
    serializer = TurtleSerializer()
    output = serializer.serialize(results, [])
    # Parse with rdflib to verify valid Turtle
    g = rdflib.Graph()
    g.parse(data=output, format="turtle")
    assert len(g) > 0


def test_turtle_serializer_includes_types():
    """TurtleSerializer includes entity types and properties."""
    results = [_result("id-1", "Acme", "Legal_Entity")]
    serializer = TurtleSerializer()
    output = serializer.serialize(results, [])
    assert "Legal_Entity" in output
    assert "Acme" in output


def test_llm_serializer_raises():
    """LLMSerializer sync serialize() raises NotImplementedError with async-only message."""
    serializer = LLMSerializer()
    with pytest.raises(NotImplementedError, match="async-only"):
        serializer.serialize([], [])


def test_get_serializer_factory():
    """get_serializer returns correct serializer type."""
    assert isinstance(get_serializer("template"), TemplateSerializer)
    assert isinstance(get_serializer("turtle"), TurtleSerializer)
    assert isinstance(get_serializer("llm"), LLMSerializer)
    with pytest.raises(ValueError):
        get_serializer("unknown")


@pytest.mark.asyncio
async def test_llm_serializer_async_produces_output():
    """LLMSerializer.serialize_async() produces prose output via mocked LLM."""
    mock_response = MagicMock()
    mock_response.text = "Acme Capital is a legal entity based in BVI."
    mock_provider = AsyncMock()
    mock_provider.generate = AsyncMock(return_value=mock_response)

    results = [_result("id-1", "Acme", "Legal_Entity")]
    serializer = LLMSerializer(retrieval_config=RetrievalConfig())

    mock_llm_config = {
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "base_url": "http://localhost:11434",
        "api_key": "",
        "timeout": 300,
    }
    with (
        patch("src.shared.llm_provider.get_llm_config", return_value=mock_llm_config),
        patch("src.shared.llm_provider.get_provider", return_value=mock_provider),
    ):
        output = await serializer.serialize_async(results, [], token_budget=2000)

    assert "Acme" in output
    assert output == "Acme Capital is a legal entity based in BVI."


@pytest.mark.asyncio
async def test_llm_serializer_async_respects_token_budget():
    """LLMSerializer passes correct max_tokens (token count, not x4)."""
    mock_response = MagicMock()
    mock_response.text = "Summary text."
    mock_provider = AsyncMock()
    mock_provider.generate = AsyncMock(return_value=mock_response)

    results = [_result("id-1", "Acme", "Legal_Entity")]
    serializer = LLMSerializer(retrieval_config=RetrievalConfig())
    token_budget = 500

    mock_llm_config = {
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "base_url": "http://localhost:11434",
        "api_key": "",
        "timeout": 300,
    }
    with (
        patch("src.shared.llm_provider.get_llm_config", return_value=mock_llm_config),
        patch("src.shared.llm_provider.get_provider", return_value=mock_provider),
    ):
        await serializer.serialize_async(results, [], token_budget=token_budget)

    # Verify max_tokens = max(64, int(500 * 0.4)) = max(64, 200) = 200
    call_kwargs = mock_provider.generate.call_args
    assert call_kwargs.kwargs["max_tokens"] == 200


@pytest.mark.asyncio
async def test_llm_serializer_async_fallback_on_failure():
    """LLMSerializer returns template output on LLM failure."""
    mock_provider = AsyncMock()
    mock_provider.generate = AsyncMock(side_effect=Exception("LLM down"))

    results = [_result("id-1", "Acme", "Legal_Entity")]
    serializer = LLMSerializer(retrieval_config=RetrievalConfig())

    mock_llm_config = {
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "base_url": "http://localhost:11434",
        "api_key": "",
        "timeout": 300,
    }
    with (
        patch("src.shared.llm_provider.get_llm_config", return_value=mock_llm_config),
        patch("src.shared.llm_provider.get_provider", return_value=mock_provider),
    ):
        output = await serializer.serialize_async(results, [], token_budget=2000)

    # Should fall back to template serialization
    assert "Acme" in output
    assert "Legal_Entity" in output


@pytest.mark.asyncio
async def test_llm_serializer_async_empty_results():
    """LLMSerializer returns empty string for empty input."""
    serializer = LLMSerializer(retrieval_config=RetrievalConfig())
    output = await serializer.serialize_async([], [], token_budget=2000)
    assert output == ""


@pytest.mark.asyncio
async def test_llm_serializer_async_json_mode_false():
    """LLMSerializer calls LLM with json_mode=False for prose output."""
    mock_response = MagicMock()
    mock_response.text = "Summary."
    mock_provider = AsyncMock()
    mock_provider.generate = AsyncMock(return_value=mock_response)

    results = [_result("id-1", "Acme", "Legal_Entity")]
    serializer = LLMSerializer(retrieval_config=RetrievalConfig())

    mock_llm_config = {
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "base_url": "http://localhost:11434",
        "api_key": "",
        "timeout": 300,
    }
    with (
        patch("src.shared.llm_provider.get_llm_config", return_value=mock_llm_config),
        patch("src.shared.llm_provider.get_provider", return_value=mock_provider),
    ):
        await serializer.serialize_async(results, [], token_budget=2000)

    call_kwargs = mock_provider.generate.call_args
    assert call_kwargs.kwargs["json_mode"] is False


@pytest.mark.asyncio
async def test_serialize_async_default_wraps_sync():
    """TemplateSerializer.serialize_async() returns same as serialize()."""
    results = [_result("id-1", "Acme", "Legal_Entity")]
    rels = [{"source_name": "A", "target_name": "B", "relationship_type": "owns"}]
    serializer = TemplateSerializer()

    sync_output = serializer.serialize(results, rels)
    async_output = await serializer.serialize_async(results, rels)

    assert sync_output == async_output


def test_get_serializer_llm_passes_config():
    """get_serializer('llm', config=...) constructs LLMSerializer with config."""
    config = RetrievalConfig(serialization_model="llama3:8b", ollama_base_url="http://localhost:11434")
    serializer = get_serializer("llm", config=config)
    assert isinstance(serializer, LLMSerializer)
    assert serializer._config is config
    assert serializer._config.serialization_model == "llama3:8b"
