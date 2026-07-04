"""Tests for three-tier entity resolution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.entity_registry import EntityRegistry
from src.extraction.entity_resolver import (
    DisambiguationResult,
    EntityResolutionResult,
    EntityResolver,
    build_embedding_text,
)
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractedEntity
from src.extraction.instructor_client import ExtractionLLMError
from src.graph.arcade_client import ArcadeClient, ArcadeConfig


def _make_entity(name="Acme Corp", entity_type="Legal_Entity", **props):
    """Helper to create ExtractedEntity."""
    return ExtractedEntity(
        name=name,
        entity_type=entity_type,
        properties=props,
    )


def _mock_arcade_client():
    """Create an ArcadeClient with mocked execute methods."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock(return_value={"result": []})
    client.execute_sql = AsyncMock(return_value={"result": []})
    return client


def _make_resolver(
    arcade_client=None,
    config=None,
    instructor_client=None,
):
    """Create EntityResolver with sensible defaults."""
    return EntityResolver(
        arcade_client=arcade_client or _mock_arcade_client(),
        config=config or ExtractionSettings(),
        ollama_base_url="http://localhost:11434",
        instructor_client=instructor_client,
    )


# --- build_embedding_text ---


def test_build_embedding_text_basic():
    """build_embedding_text returns name + type without description."""
    result = build_embedding_text("Acme", "Legal_Entity", None)
    assert result == "Acme (Legal_Entity)"


def test_build_embedding_text_with_description():
    """build_embedding_text includes description when present."""
    result = build_embedding_text(
        "Acme", "Legal_Entity", {"description": "A Delaware corporation"}
    )
    assert result == "Acme (Legal_Entity): A Delaware corporation"


# --- Tier 1: Exact match ---


@pytest.mark.asyncio
async def test_tier1_exact_match_original_name():
    """Tier 1: exact match on original name returns grace_id."""
    client = _mock_arcade_client()
    # canonical_lookup returns a grace_id for original name
    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value="uuid-123",
    ), patch(
        "src.extraction.entity_resolver.append_entity_alias",
        new_callable=AsyncMock,
    ) as mock_alias:
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Acme Corp")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "exact"
    assert result.resolved_grace_id == "uuid-123"
    assert result.is_new is False
    mock_alias.assert_not_called()


@pytest.mark.asyncio
async def test_tier1_exact_match_normalized_name():
    """Tier 1: exact match on normalized name (suffix stripped)."""
    call_count = 0

    async def _lookup(client, entity_type, name):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # original name miss
        return "uuid-456"  # normalized name hit

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        side_effect=_lookup,
    ), patch(
        "src.extraction.entity_resolver.append_entity_alias",
        new_callable=AsyncMock,
    ) as mock_alias:
        resolver = _make_resolver()
        registry = EntityRegistry()
        entity = _make_entity("Acme Corp LLC")  # normalizes to "acme corp"
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "exact"
    assert result.resolved_grace_id == "uuid-456"
    mock_alias.assert_called_once()


@pytest.mark.asyncio
async def test_tier1_exact_match_case_only_via_second_lookup():
    """Tier 1: first lookup uses extracted casing; graph matches normalized lowercase."""
    calls: list[str] = []

    async def _lookup(client, entity_type, name):
        calls.append(name)
        if name == "ACME Holdings":
            return None
        if name == "acme holdings":
            return "uuid-casefold"
        return None

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        side_effect=_lookup,
    ):
        resolver = _make_resolver()
        registry = EntityRegistry()
        entity = _make_entity("ACME Holdings")
        result = await resolver.resolve_entity(entity, registry)

    assert calls == ["ACME Holdings", "acme holdings"]
    assert result.resolution_tier == "exact"
    assert result.resolved_grace_id == "uuid-casefold"
    assert result.matched_name == "acme holdings"


@pytest.mark.asyncio
async def test_tier1_no_match_proceeds_to_tier2():
    """No exact match proceeds to Tier 2."""
    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        client = _mock_arcade_client()
        # No candidates in graph
        client.execute_cypher.return_value = {"result": []}
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("NewEntity")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "new"
    assert result.is_new is True


# --- Tier 2: Embedding similarity ---


@pytest.mark.asyncio
async def test_tier2_above_merge_threshold():
    """Tier 2: similarity above merge threshold -> auto-merge."""
    client = _mock_arcade_client()
    # vectorNeighbors ANN response with low distance (high similarity)
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-100", "name": "Acme Corporation",
                 "_deprecated": False, "distance": 0.02},
            ]
        }]
    }

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Legal_Entity": {"merge": 0.85, "review": 0.70}},
        )
        resolver = _make_resolver(arcade_client=client, config=config)
        registry = EntityRegistry()
        entity = _make_entity("Acme Corp")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "embedding"
    assert result.resolved_grace_id == "uuid-100"
    assert result.is_new is False
    assert result.similarity_score is not None
    assert result.similarity_score > 0.85


@pytest.mark.asyncio
async def test_tier2_below_review_threshold_new():
    """Tier 2: similarity below review threshold -> new entity."""
    client = _mock_arcade_client()
    # vectorNeighbors ANN response with high distance (low similarity)
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-200", "name": "Completely Different Corp",
                 "_deprecated": False, "distance": 0.5},
            ]
        }]
    }

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Legal_Entity": {"merge": 0.90, "review": 0.78}},
        )
        resolver = _make_resolver(arcade_client=client, config=config)
        registry = EntityRegistry()
        entity = _make_entity("Acme Corp")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "new"
    assert result.is_new is True


@pytest.mark.asyncio
async def test_tier2_empty_candidates_new():
    """No entities of type in graph -> new entity."""
    client = _mock_arcade_client()
    # vectorNeighbors returns empty neighbors
    client.execute_sql.return_value = {"result": [{"neighbors": []}]}

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("NewEntity")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "new"
    assert result.is_new is True


@pytest.mark.asyncio
async def test_tier2_per_type_thresholds():
    """Per-type thresholds are used when configured."""
    client = _mock_arcade_client()
    # distance 0.02 -> similarity 0.98, above Person merge=0.93
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-300", "name": "John Smith",
                 "_deprecated": False, "distance": 0.02},
            ]
        }]
    }

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Person": {"merge": 0.93, "review": 0.82}},
        )
        resolver = _make_resolver(arcade_client=client, config=config)
        # Mock the instructor client for Tier 3 if needed
        mock_client = AsyncMock()
        mock_client.resolve = AsyncMock(
            return_value=DisambiguationResult(decision="YES", reasoning="Same person")
        )
        resolver._instructor_client = mock_client
        registry = EntityRegistry()
        entity = _make_entity("John Smith", entity_type="Person")
        result = await resolver.resolve_entity(entity, registry)

    # Should have used Person thresholds, not defaults
    assert result.resolution_tier in ("embedding", "llm")
    assert result.is_new is False


@pytest.mark.asyncio
async def test_tier2_candidates_json_populated():
    """candidates_json is populated with correct structure."""
    client = _mock_arcade_client()
    # vectorNeighbors returns two candidates with different distances
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-a", "name": "Alpha Corp",
                 "_deprecated": False, "distance": 0.05},
                {"grace_id": "uuid-b", "name": "Beta Corp",
                 "_deprecated": False, "distance": 0.1},
            ]
        }]
    }

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Unknown Corp")
        result = await resolver.resolve_entity(entity, registry)

    assert result.candidates_json is not None
    assert len(result.candidates_json) >= 1
    assert "grace_id" in result.candidates_json[0]
    assert "name" in result.candidates_json[0]
    assert "score" in result.candidates_json[0]
    if len(result.candidates_json) >= 2:
        assert result.candidates_json[0]["score"] >= result.candidates_json[1]["score"]


# --- Tier 3: LLM disambiguation ---


@pytest.mark.asyncio
async def test_tier3_yes_merge():
    """Tier 3: YES -> returns matched grace_id."""
    client = _mock_arcade_client()
    # distance 0.12 -> similarity 0.88, between review=0.80 and merge=0.95
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-400", "name": "Acme Corporation",
                 "_deprecated": False, "distance": 0.12},
            ]
        }]
    }

    mock_instructor = AsyncMock()
    mock_instructor.resolve = AsyncMock(
        return_value=DisambiguationResult(decision="YES", reasoning="Same company, different suffix")
    )

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Legal_Entity": {"merge": 0.95, "review": 0.80}},
        )
        resolver = _make_resolver(
            arcade_client=client, config=config, instructor_client=mock_instructor
        )
        registry = EntityRegistry()
        entity = _make_entity("Acme Corp")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "llm"
    assert result.resolved_grace_id == "uuid-400"
    assert result.is_new is False
    assert result.llm_reasoning == "Same company, different suffix"


@pytest.mark.asyncio
async def test_tier3_no_new_entity():
    """Tier 3: NO -> returns new entity."""
    client = _mock_arcade_client()
    # distance 0.12 -> similarity 0.88, between review=0.80 and merge=0.95
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-500", "name": "Acme Industries",
                 "_deprecated": False, "distance": 0.12},
            ]
        }]
    }

    mock_instructor = AsyncMock()
    mock_instructor.resolve = AsyncMock(
        return_value=DisambiguationResult(decision="NO", reasoning="Different entities")
    )

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Legal_Entity": {"merge": 0.95, "review": 0.80}},
        )
        resolver = _make_resolver(
            arcade_client=client, config=config, instructor_client=mock_instructor
        )
        registry = EntityRegistry()
        entity = _make_entity("Acme Corp")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "new"
    assert result.is_new is True
    assert result.llm_reasoning == "Different entities"


@pytest.mark.asyncio
async def test_tier3_llm_failure():
    """Tier 3: ExtractionLLMError -> new entity with resolution_note."""
    client = _mock_arcade_client()
    # distance 0.12 -> similarity 0.88, between review=0.80 and merge=0.95
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-600", "name": "Acme Industries",
                 "_deprecated": False, "distance": 0.12},
            ]
        }]
    }

    mock_instructor = AsyncMock()
    mock_instructor.resolve = AsyncMock(
        side_effect=ExtractionLLMError("LLM timeout")
    )

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Legal_Entity": {"merge": 0.95, "review": 0.80}},
        )
        resolver = _make_resolver(
            arcade_client=client, config=config, instructor_client=mock_instructor
        )
        registry = EntityRegistry()
        entity = _make_entity("Acme Corp")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "new"
    assert result.is_new is True
    assert result.resolution_note == "llm_disambiguation_failed"


# --- Batch ---


@pytest.mark.asyncio
async def test_resolve_batch_processes_all():
    """resolve_batch processes all entities and returns correct count."""
    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ):
        client = _mock_arcade_client()
        client.execute_cypher.return_value = {"result": []}
        resolver = _make_resolver(arcade_client=client)
        entities = [
            _make_entity("Entity A"),
            _make_entity("Entity B"),
            _make_entity("Entity C"),
        ]
        results = await resolver.resolve_batch(entities)

    assert len(results) == 3
    assert all(r.resolution_tier == "new" for r in results)


@pytest.mark.asyncio
async def test_resolve_batch_cache_prevents_duplicate():
    """Entity registry prevents duplicate resolution for same name+type."""
    call_count = 0

    async def _mock_lookup(client, entity_type, name):
        nonlocal call_count
        call_count += 1
        return "uuid-cached"

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        side_effect=_mock_lookup,
    ):
        resolver = _make_resolver()
        entities = [
            _make_entity("Acme Corp"),
            _make_entity("Acme Corp"),  # Same name+type, should use cache
        ]
        results = await resolver.resolve_batch(entities)

    assert len(results) == 2
    assert results[0].resolved_grace_id == "uuid-cached"
    assert results[1].resolved_grace_id == "uuid-cached"
    # canonical_lookup called only once (for first entity, original name)
    # Second entity hits cache
    assert call_count == 1


# --- Argmax ---


@pytest.mark.asyncio
async def test_argmax_highest_score_wins():
    """Multiple candidates above merge threshold -> highest score wins."""
    client = _mock_arcade_client()
    # uuid-b has lower distance (higher similarity) than uuid-a
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-a", "name": "Alpha Corp",
                 "_deprecated": False, "distance": 0.10},
                {"grace_id": "uuid-b", "name": "Alpha Corporation",
                 "_deprecated": False, "distance": 0.02},
            ]
        }]
    }

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Legal_Entity": {"merge": 0.85, "review": 0.70}},
        )
        resolver = _make_resolver(arcade_client=client, config=config)
        registry = EntityRegistry()
        entity = _make_entity("Alpha Inc")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolved_grace_id == "uuid-b"  # higher cosine similarity


@pytest.mark.asyncio
async def test_argmax_tie_lexicographic():
    """Exact tie in score -> lexicographically smaller grace_id wins."""
    client = _mock_arcade_client()
    # Both candidates have identical distance (tied scores)
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-bbb", "name": "Corp B",
                 "_deprecated": False, "distance": 0.02},
                {"grace_id": "uuid-aaa", "name": "Corp A",
                 "_deprecated": False, "distance": 0.02},
            ]
        }]
    }

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Legal_Entity": {"merge": 0.85, "review": 0.70}},
        )
        resolver = _make_resolver(arcade_client=client, config=config)
        registry = EntityRegistry()
        entity = _make_entity("Corp X")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolved_grace_id == "uuid-aaa"  # lexicographically smaller


@pytest.mark.asyncio
async def test_tier2_between_review_and_merge_proceeds_to_tier3():
    """Similarity between review and merge thresholds proceeds to Tier 3."""
    client = _mock_arcade_client()
    # distance 0.12 -> similarity 0.88, between review=0.80 and merge=0.95
    client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "uuid-700", "name": "Acme Industries",
                 "_deprecated": False, "distance": 0.12},
            ]
        }]
    }

    mock_instructor = AsyncMock()
    mock_instructor.resolve = AsyncMock(
        return_value=DisambiguationResult(decision="NO", reasoning="Different company")
    )

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        config = ExtractionSettings(
            er_thresholds={"Legal_Entity": {"merge": 0.95, "review": 0.80}},
        )
        resolver = _make_resolver(
            arcade_client=client, config=config, instructor_client=mock_instructor
        )
        registry = EntityRegistry()
        entity = _make_entity("Acme Corp")
        result = await resolver.resolve_entity(entity, registry)

    # Should have gone through Tier 3
    mock_instructor.resolve.assert_called_once()
    assert result.resolution_tier == "new"
    assert result.llm_reasoning == "Different company"
