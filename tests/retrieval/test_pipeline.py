"""Tests for the retrieval pipeline orchestrator (all external deps mocked)."""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.retrieval.bm25_strategy import BM25SearchIndex
from src.retrieval.pipeline import RetrievalPipeline
from src.retrieval.reranker import CrossEncoderReranker
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import (
    RetrievalCandidate,
    RetrievalQuery,
)
from src.retrieval.semantic_strategy import SemanticSearchIndex


def _mock_pipeline(
    graph_results: list | None = None,
    semantic_results: list | None = None,
    bm25_results: list | None = None,
    reranker_scores: list | None = None,
    config: RetrievalConfig | None = None,
) -> RetrievalPipeline:
    """Create a pipeline with all external deps mocked."""
    cfg = config or RetrievalConfig()

    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock(return_value={"result": []})

    semantic_index = MagicMock(spec=SemanticSearchIndex)
    semantic_index.search = AsyncMock(return_value=semantic_results or [])

    bm25_index = MagicMock(spec=BM25SearchIndex)
    bm25_index.search = MagicMock(return_value=bm25_results or [])

    reranker = MagicMock(spec=CrossEncoderReranker)

    pipeline = RetrievalPipeline(
        client=client,
        config=cfg,
        semantic_index=semantic_index,
        bm25_index=bm25_index,
        reranker=reranker,
    )
    # Mark indexes as built so we skip auto-build
    pipeline._indexes_built = True

    return pipeline


def _candidate(grace_id: str, strategy: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        grace_id=grace_id,
        entity_type="Entity",
        name=f"entity-{grace_id}",
        properties={"name": f"entity-{grace_id}"},
        score=0.8,
        strategy=strategy,
        rank=1,
    )


@pytest.mark.asyncio
@patch("src.retrieval.pipeline.graph_search")
async def test_pipeline_full_response(mock_graph_search):
    """Full pipeline with all strategies mocked returns RetrievalResponse."""
    graph_cands = [_candidate("id-1", "graph")]
    semantic_cands = [_candidate("id-2", "semantic")]
    bm25_cands = [_candidate("id-1", "bm25")]

    mock_graph_search.return_value = graph_cands

    pipeline = _mock_pipeline(
        semantic_results=semantic_cands,
        bm25_results=bm25_cands,
    )

    # Mock reranker to return RankedResults
    from src.retrieval.retrieval_models import RankedResult
    pipeline.reranker.rerank.return_value = [
        RankedResult(
            grace_id="id-1",
            entity_type="Entity",
            name="entity-id-1",
            properties={"name": "entity-id-1"},
            rerank_score=0.9,
            rrf_score=0.03,
            contributing_strategies=["graph", "bm25"],
        ),
    ]

    query = RetrievalQuery(query_text="test query")
    response = await pipeline.query(query)

    assert response.query == "test query"
    assert len(response.results) == 1
    assert response.serialization_format == "template"
    assert response.total_candidates >= 1
    assert "fusion" in response.latency_ms
    assert "rerank" in response.latency_ms


@pytest.mark.asyncio
@patch("src.retrieval.pipeline.graph_search")
async def test_pipeline_auto_builds_indexes(mock_graph_search):
    """Pipeline auto-builds indexes on first query."""
    mock_graph_search.return_value = []

    pipeline = _mock_pipeline()
    pipeline._indexes_built = False  # Reset

    # Mock build_indexes
    pipeline.build_indexes = AsyncMock(return_value=0)

    from src.retrieval.retrieval_models import RankedResult
    pipeline.reranker.rerank.return_value = []

    query = RetrievalQuery(query_text="test")
    await pipeline.query(query)

    pipeline.build_indexes.assert_called_once()


@pytest.mark.asyncio
@patch("src.retrieval.pipeline.graph_search")
async def test_pipeline_respects_toggles(mock_graph_search):
    """Pipeline respects strategy enable/disable toggles."""
    mock_graph_search.return_value = []

    config = RetrievalConfig(
        graph_traversal_enabled=True,
        semantic_search_enabled=False,
        bm25_search_enabled=False,
    )
    pipeline = _mock_pipeline(config=config)

    from src.retrieval.retrieval_models import RankedResult
    pipeline.reranker.rerank.return_value = []

    query = RetrievalQuery(query_text="test")
    await pipeline.query(query)

    # Semantic and BM25 should not be called
    pipeline.semantic_index.search.assert_not_called()
    pipeline.bm25_index.search.assert_not_called()
    # Graph was called
    mock_graph_search.assert_called_once()


@pytest.mark.asyncio
@patch("src.retrieval.pipeline.graph_search")
async def test_pipeline_latency_tracking(mock_graph_search):
    """Pipeline latency_ms includes all components."""
    mock_graph_search.return_value = [_candidate("id-1", "graph")]

    pipeline = _mock_pipeline(
        semantic_results=[_candidate("id-1", "semantic")],
        bm25_results=[_candidate("id-1", "bm25")],
    )

    from src.retrieval.retrieval_models import RankedResult
    pipeline.reranker.rerank.return_value = [
        RankedResult(
            grace_id="id-1", entity_type="Entity", name="test",
            rerank_score=0.9, rrf_score=0.03,
            contributing_strategies=["graph"],
        )
    ]

    query = RetrievalQuery(query_text="test")
    response = await pipeline.query(query)

    assert "graph" in response.latency_ms
    assert "semantic" in response.latency_ms
    assert "bm25" in response.latency_ms
    assert "fusion" in response.latency_ms
    assert "rerank" in response.latency_ms


@pytest.mark.asyncio
@patch("src.retrieval.pipeline.graph_search")
async def test_pipeline_strategy_contributions(mock_graph_search):
    """Pipeline strategy_contributions counts correctly."""
    mock_graph_search.return_value = [_candidate("id-1", "graph")]

    pipeline = _mock_pipeline(
        semantic_results=[_candidate("id-2", "semantic")],
    )

    from src.retrieval.retrieval_models import RankedResult
    pipeline.reranker.rerank.return_value = [
        RankedResult(
            grace_id="id-1", entity_type="Entity", name="test",
            rerank_score=0.9, rrf_score=0.03,
            contributing_strategies=["graph"],
        ),
        RankedResult(
            grace_id="id-2", entity_type="Entity", name="test2",
            rerank_score=0.8, rrf_score=0.02,
            contributing_strategies=["semantic"],
        ),
    ]

    query = RetrievalQuery(query_text="test")
    response = await pipeline.query(query)

    assert response.strategy_contributions.get("graph", 0) >= 1
    assert response.strategy_contributions.get("semantic", 0) >= 1


@pytest.mark.asyncio
@patch("src.retrieval.pipeline.graph_search")
async def test_pipeline_query_aware_filter_tracks_omissions(mock_graph_search):
    """Query-aware filter omits irrelevant properties and reports omissions."""
    mock_graph_search.return_value = [_candidate("id-1", "graph")]
    pipeline = _mock_pipeline(semantic_results=[], bm25_results=[])

    from src.retrieval.retrieval_models import RankedResult
    pipeline.reranker.rerank.return_value = [
        RankedResult(
            grace_id="id-1",
            entity_type="Legal_Entity",
            name="Acme",
            properties={
                "name": "Acme",
                "broker_contact": "Jane",
                "expiry_date": "2026-12-31",
            },
            rerank_score=0.9,
            rrf_score=0.03,
            contributing_strategies=["graph"],
        ),
    ]

    query = RetrievalQuery(query_text="What insurance deadlines do we have this quarter?")
    response = await pipeline.query(query)
    assert "temporal" in response.query_intents
    assert response.properties_omitted_count >= 1
    assert "expiry_date" in response.results[0].properties
    assert "broker_contact" not in response.results[0].properties


@pytest.mark.asyncio
@patch("src.retrieval.pipeline.graph_search")
async def test_pipeline_iterative_override_on_runs_round2(mock_graph_search):
    """iterative_mode=on runs second round and reports iterative mode."""
    mock_graph_search.side_effect = [
        [_candidate("id-1", "graph")],
        [_candidate("id-2", "graph")],
    ]
    config = RetrievalConfig(iterative_retrieval_enabled=True)
    pipeline = _mock_pipeline(
        semantic_results=[_candidate("id-3", "semantic")],
        bm25_results=[],
        config=config,
    )

    from src.retrieval.retrieval_models import RankedResult
    pipeline.reranker.rerank.return_value = [
        RankedResult(
            grace_id="id-1",
            entity_type="Entity",
            name="entity-id-1",
            rerank_score=0.9,
            rrf_score=0.03,
            contributing_strategies=["graph"],
        ),
    ]
    query = RetrievalQuery(query_text="Who owns what via linked entities?", iterative_mode="on")
    response = await pipeline.query(query)
    assert response.retrieval_mode == "iterative_round2"
    assert "fusion_round2" in response.latency_ms
    assert mock_graph_search.call_count == 2


@pytest.mark.asyncio
@patch("src.retrieval.pipeline.graph_search")
async def test_pipeline_serialize_async_llm_format(mock_graph_search):
    """Pipeline uses serialize_async for 'llm' format and passes config to get_serializer."""
    mock_graph_search.return_value = [_candidate("id-1", "graph")]

    config = RetrievalConfig(serialization_format="llm")
    pipeline = _mock_pipeline(
        semantic_results=[],
        bm25_results=[],
        config=config,
    )

    from src.retrieval.retrieval_models import RankedResult
    pipeline.reranker.rerank.return_value = [
        RankedResult(
            grace_id="id-1",
            entity_type="Entity",
            name="entity-id-1",
            properties={"name": "entity-id-1"},
            rerank_score=0.9,
            rrf_score=0.03,
            contributing_strategies=["graph"],
        ),
    ]

    # Mock the LLM provider used by LLMSerializer
    mock_response = MagicMock()
    mock_response.text = "Entity-id-1 is an entity."
    mock_provider = AsyncMock()
    mock_provider.generate = AsyncMock(return_value=mock_response)

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
        query = RetrievalQuery(query_text="test query")
        response = await pipeline.query(query)

    assert response.serialization_format == "llm"
    assert response.serialized_context == "Entity-id-1 is an entity."
