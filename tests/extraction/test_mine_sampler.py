"""Tests for MINE sampling harness."""

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest
from pydantic import BaseModel

from src.extraction.mine_sampler import (
    FactEntityMentions,
    FactExtractor,
    FactJudgment,
    FactList,
    FactMention,
    GraphFactChecker,
    MINESampler,
    MineSampleRow,
    _fallback_name_lookup,
    _mentions_for_fact,
    neighborhood_to_serializer_inputs,
)
from src.retrieval.retrieval_models import RankedResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(extract_side_effects=None):
    """Build a mock ExtractionLLMClient."""
    client = AsyncMock()
    type(client).extraction_model = PropertyMock(return_value="qwen2.5:7b")
    type(client).verification_model = PropertyMock(return_value="qwen2.5:7b")
    type(client).extraction_provider = PropertyMock(return_value="ollama")
    if extract_side_effects:
        client.extract = AsyncMock(side_effect=extract_side_effects)
    return client


def _mock_arcade_client():
    """Build a mock ArcadeClient."""
    return AsyncMock()


# ---------------------------------------------------------------------------
# FactExtractor tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fact_extractor_extracts_facts():
    """FactExtractor returns list of fact strings from LLM."""
    fact_list = FactList(facts=["Acme was founded in 2010.", "Acme is based in NYC."])
    client = _mock_client()
    client.extract = AsyncMock(return_value=fact_list)

    extractor = FactExtractor()
    result = await extractor.extract_facts("Acme was founded in 2010 in NYC.", client)

    assert len(result) == 2
    assert "Acme was founded in 2010." in result
    assert "Acme is based in NYC." in result


@pytest.mark.asyncio
async def test_fact_extractor_empty_text():
    """FactExtractor returns empty list for empty text."""
    client = _mock_client()
    extractor = FactExtractor()
    result = await extractor.extract_facts("", client)
    assert result == []


@pytest.mark.asyncio
async def test_fact_extractor_llm_failure():
    """FactExtractor returns empty list on LLM failure."""
    from src.extraction.instructor_client import ExtractionLLMError

    client = _mock_client()
    client.extract = AsyncMock(side_effect=ExtractionLLMError("timeout"))

    extractor = FactExtractor()
    result = await extractor.extract_facts("Some text here.", client)
    assert result == []


# ---------------------------------------------------------------------------
# Mention extraction tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mention_extraction_structured():
    """FactExtractor.extract_mentions returns structured FactEntityMentions."""
    mentions = FactEntityMentions(facts=[
        FactMention(
            fact_text="Acme owns SubCo.",
            mentioned_entities=[["Legal_Entity", "Acme"], ["Legal_Entity", "SubCo"]],
        ),
    ])
    client = _mock_client()
    # First call is facts, second is mentions
    client.extract = AsyncMock(return_value=mentions)

    extractor = FactExtractor()
    result = await extractor.extract_mentions(["Acme owns SubCo."], client)

    assert len(result) == 1
    assert result[0].fact_text == "Acme owns SubCo."
    assert len(result[0].mentioned_entities) == 2
    assert result[0].mentioned_entities[0] == ["Legal_Entity", "Acme"]


@pytest.mark.asyncio
async def test_mention_extraction_empty_facts():
    """extract_mentions returns empty list for empty facts."""
    client = _mock_client()
    extractor = FactExtractor()
    result = await extractor.extract_mentions([], client)
    assert result == []


def test_mentions_for_fact_matches_by_fact_text_not_list_order():
    """Mentions attach to facts via normalized fact_text, not list index."""
    mentions = [
        FactMention(fact_text="Second fact.", mentioned_entities=[["Person", "Bob"]]),
        FactMention(fact_text="First fact.", mentioned_entities=[["Person", "Alice"]]),
    ]
    assert _mentions_for_fact("First fact.", 0, mentions) == [["Person", "Alice"]]
    assert _mentions_for_fact("Second fact.", 0, mentions) == [["Person", "Bob"]]


def test_mentions_for_fact_index_fallback_requires_matching_text():
    """Index fallback used only when candidate fact_text matches the fact."""
    mentions = [
        FactMention(fact_text="Only fact.", mentioned_entities=[["Org", "Acme"]]),
    ]
    assert _mentions_for_fact("Only fact.", 0, mentions) == [["Org", "Acme"]]
    assert _mentions_for_fact("Wrong fact.", 0, mentions) == []


# ---------------------------------------------------------------------------
# GraphFactChecker tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_fact_checker_recovered():
    """GraphFactChecker returns recovered=True when LLM judges yes."""
    judgment = FactJudgment(recovered=True, reasoning="Entity and relationship present.")
    client = _mock_client()
    client.extract = AsyncMock(return_value=judgment)

    checker = GraphFactChecker()
    result = await checker.check_fact(
        "Acme owns SubCo.", "Entity: Legal_Entity Acme...", client
    )

    assert result["recovered"] is True
    assert "fact" in result
    assert result["fact"] == "Acme owns SubCo."


@pytest.mark.asyncio
async def test_graph_fact_checker_not_recovered():
    """GraphFactChecker returns recovered=False when LLM judges no."""
    judgment = FactJudgment(recovered=False, reasoning="No matching entity found.")
    client = _mock_client()
    client.extract = AsyncMock(return_value=judgment)

    checker = GraphFactChecker()
    result = await checker.check_fact(
        "Revenue was $5M.", "Entity: Legal_Entity Acme...", client
    )

    assert result["recovered"] is False


@pytest.mark.asyncio
async def test_graph_fact_checker_llm_failure():
    """GraphFactChecker returns recovered=False on LLM failure."""
    from src.extraction.instructor_client import ExtractionLLMError

    client = _mock_client()
    client.extract = AsyncMock(side_effect=ExtractionLLMError("down"))

    checker = GraphFactChecker()
    result = await checker.check_fact("Some fact.", "Graph context.", client)

    assert result["recovered"] is False
    assert "failed" in result["reasoning"].lower()


# ---------------------------------------------------------------------------
# MINESampler retention math tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mine_sampler_computes_retention():
    """3 facts, 2 recovered = 0.6667 retention."""
    facts = FactList(facts=["Fact A.", "Fact B.", "Fact C."])
    mentions = FactEntityMentions(facts=[
        FactMention(fact_text="Fact A.", mentioned_entities=[]),
        FactMention(fact_text="Fact B.", mentioned_entities=[]),
        FactMention(fact_text="Fact C.", mentioned_entities=[]),
    ])

    judgments_responses = [
        FactJudgment(recovered=True, reasoning="found"),
        FactJudgment(recovered=True, reasoning="found"),
        FactJudgment(recovered=False, reasoning="not found"),
    ]

    client = _mock_client()
    call_count = {"n": 0}

    async def mock_extract(system_prompt, user_prompt, response_model):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx == 0:
            return facts
        elif idx == 1:
            return mentions
        else:
            return judgments_responses[idx - 2]

    client.extract = AsyncMock(side_effect=mock_extract)

    arcade_client = _mock_arcade_client()

    # Mock the session
    session = MagicMock()
    doc_row = MagicMock()
    doc_row.extracted_text = "Source document text."
    doc_row.id = uuid4()
    session.query.return_value.filter.return_value.first.side_effect = [
        doc_row,  # document lookup
        None,     # dedup check (no existing)
    ]

    sampler = MINESampler()
    result = await sampler.sample_document(
        doc_row.id, session, client, arcade_client
    )

    assert result["total_facts"] == 3
    assert result["recovered_facts"] == 2
    assert abs(result["retention_score"] - 0.6667) < 0.01


@pytest.mark.asyncio
async def test_mine_sampler_zero_facts():
    """No facts extracted → retention 0.0, no division error."""
    facts = FactList(facts=[])
    client = _mock_client()
    client.extract = AsyncMock(return_value=facts)

    arcade_client = _mock_arcade_client()

    session = MagicMock()
    doc_row = MagicMock()
    doc_row.extracted_text = "Very short."
    doc_row.id = uuid4()
    session.query.return_value.filter.return_value.first.side_effect = [
        doc_row,  # document lookup
        None,     # dedup check
    ]

    sampler = MINESampler()
    result = await sampler.sample_document(
        doc_row.id, session, client, arcade_client
    )

    assert result["total_facts"] == 0
    assert result["retention_score"] == 0.0
    assert result["cached"] is False


@pytest.mark.asyncio
async def test_mine_sampler_dedup_by_hash():
    """Same text + same models → cached result returned."""
    client = _mock_client()
    arcade_client = _mock_arcade_client()

    existing_row = MagicMock()
    existing_row.id = uuid4()
    existing_row.retention_score = 0.75
    existing_row.total_facts = 4
    existing_row.recovered_facts = 3
    existing_row.judgments = [{"fact": "x", "recovered": True, "reasoning": "y"}]

    session = MagicMock()
    doc_row = MagicMock()
    doc_row.extracted_text = "Document text."
    doc_row.id = uuid4()
    # First query returns doc, second returns existing sample (dedup hit)
    session.query.return_value.filter.return_value.first.side_effect = [
        doc_row,      # document lookup
        existing_row,  # dedup check returns existing
    ]

    sampler = MINESampler()
    result = await sampler.sample_document(
        doc_row.id, session, client, arcade_client
    )

    assert result["cached"] is True
    assert result["retention_score"] == 0.75
    assert result["total_facts"] == 4


@pytest.mark.asyncio
async def test_mine_sampler_includes_provenance():
    """Verify extraction_model, judge_model stored in result."""
    facts = FactList(facts=["Fact A."])
    mentions = FactEntityMentions(facts=[
        FactMention(fact_text="Fact A.", mentioned_entities=[]),
    ])
    judgment = FactJudgment(recovered=True, reasoning="found")

    client = _mock_client()
    type(client).verification_model = PropertyMock(return_value="qwen2.5-verifier")
    call_count = {"n": 0}

    async def mock_extract(system_prompt, user_prompt, response_model):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx == 0:
            return facts
        elif idx == 1:
            return mentions
        else:
            return judgment

    client.extract = AsyncMock(side_effect=mock_extract)
    arcade_client = _mock_arcade_client()

    session = MagicMock()
    doc_row = MagicMock()
    doc_row.extracted_text = "Text."
    doc_row.id = uuid4()
    session.query.return_value.filter.return_value.first.side_effect = [
        doc_row, None,
    ]

    sampler = MINESampler()
    result = await sampler.sample_document(
        doc_row.id, session, client, arcade_client
    )

    # Check that session.add was called with a MineSampleRow
    add_call = session.add.call_args
    row = add_call[0][0]
    assert row.extraction_model == "qwen2.5:7b"
    assert row.judge_model == "qwen2.5-verifier"


@pytest.mark.asyncio
async def test_mine_sampler_document_not_found():
    """ValueError raised when document not in processed_documents."""
    client = _mock_client()
    arcade_client = _mock_arcade_client()

    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None

    sampler = MINESampler()
    with pytest.raises(ValueError, match="not found"):
        await sampler.sample_document(uuid4(), session, client, arcade_client)


# ---------------------------------------------------------------------------
# F-033 / ISS-0015: fallback name lookup tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_name_lookup_exact_match():
    """Fallback matches name against ANY vertex type (type-agnostic)."""
    arcade = AsyncMock()
    arcade.execute_cypher = AsyncMock(
        return_value={"result": [{"n.grace_id": "gid-1"}]}
    )
    gid = await _fallback_name_lookup(arcade, "Acme")
    assert gid == "gid-1"
    query = arcade.execute_cypher.call_args[0][0]
    assert "MATCH (n)" in query  # no type scoping


@pytest.mark.asyncio
async def test_fallback_name_lookup_substring_second_pass():
    """Exact-match miss falls through to substring (CONTAINS) pass."""
    arcade = AsyncMock()
    arcade.execute_cypher = AsyncMock(side_effect=[
        {"result": []},
        {"result": [{"n.grace_id": "gid-2"}]},
    ])
    gid = await _fallback_name_lookup(arcade, "GP-8894-1120")
    assert gid == "gid-2"
    assert arcade.execute_cypher.call_count == 2
    second_query = arcade.execute_cypher.call_args_list[1][0][0]
    assert "CONTAINS" in second_query


@pytest.mark.asyncio
async def test_fallback_name_lookup_none_found():
    """Both passes miss → None."""
    arcade = AsyncMock()
    arcade.execute_cypher = AsyncMock(return_value={"result": []})
    assert await _fallback_name_lookup(arcade, "Nobody Here") is None


@pytest.mark.asyncio
async def test_fallback_name_lookup_empty_name():
    """Empty/None names never hit the graph."""
    arcade = AsyncMock()
    assert await _fallback_name_lookup(arcade, "") is None
    assert await _fallback_name_lookup(arcade, None) is None
    arcade.execute_cypher.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_name_lookup_query_error_tolerated():
    """A failing pass is skipped, not raised."""
    arcade = AsyncMock()
    arcade.execute_cypher = AsyncMock(side_effect=[
        RuntimeError("boom"),
        {"result": [{"n.grace_id": "gid-3"}]},
    ])
    assert await _fallback_name_lookup(arcade, "Acme") == "gid-3"


@pytest.mark.asyncio
async def test_sampler_engages_fallback_when_typed_lookup_misses():
    """F-033 / ISS-0015: typed canonical_lookup miss must not zero retention.

    canonical_lookup returns None (type mismatch / no provenance linkage);
    the type-agnostic fallback finds the vertex, so the judge sees real
    graph context instead of the empty-graph sentinel.
    """
    facts = FactList(facts=["The policy number is GP-8894-1120."])
    mentions = FactEntityMentions(facts=[
        FactMention(
            fact_text="The policy number is GP-8894-1120.",
            mentioned_entities=[["Insurance_Policy", "GP-8894-1120"]],
        ),
    ])
    judgment = FactJudgment(recovered=True, reasoning="policy vertex present")

    client = _mock_client()
    call_count = {"n": 0}

    async def mock_extract(system_prompt, user_prompt, response_model):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx == 0:
            return facts
        elif idx == 1:
            return mentions
        # Judge call: graph context must NOT be the empty-graph sentinel
        assert "No matching entities found" not in user_prompt
        return judgment

    client.extract = AsyncMock(side_effect=mock_extract)

    arcade_client = _mock_arcade_client()
    arcade_client.execute_cypher = AsyncMock(
        return_value={"result": [{"n.grace_id": "policy-gid"}]}
    )

    session = MagicMock()
    doc_row = MagicMock()
    doc_row.extracted_text = "Policy GP-8894-1120 covers the property."
    doc_row.id = uuid4()
    session.query.return_value.filter.return_value.first.side_effect = [
        doc_row, None,
    ]

    neighborhood = {
        "seed": {
            "grace_id": "policy-gid",
            "@type": "Insurance_Policy",
            "name": "GP-8894-1120",
            "policy_number": "GP-8894-1120",
        },
        "neighbors": [],
        "edges": [],
    }

    with patch(
        "src.extraction.mine_sampler.canonical_lookup",
        new=AsyncMock(return_value=None),
    ), patch(
        "src.extraction.mine_sampler.fetch_entity_neighborhood",
        new=AsyncMock(return_value=neighborhood),
    ) as mock_fetch:
        sampler = MINESampler()
        result = await sampler.sample_document(
            doc_row.id, session, client, arcade_client
        )

    mock_fetch.assert_awaited_once()
    assert mock_fetch.call_args[0][1] == "policy-gid"
    assert result["recovered_facts"] == 1
    assert result["retention_score"] == 1.0


# ---------------------------------------------------------------------------
# neighborhood_to_serializer_inputs tests
# ---------------------------------------------------------------------------


def test_neighborhood_to_serializer_inputs():
    """Converts neighborhood dict to RankedResult list + relationship dicts."""
    neighborhood = {
        "seed": {
            "grace_id": "seed-1",
            "@type": "Legal_Entity",
            "name": "Acme",
            "jurisdiction": "US",
        },
        "neighbors": [
            {
                "grace_id": "n-1",
                "@type": "Person",
                "name": "Alice",
            },
        ],
        "edges": [
            {
                "source_grace_id": "seed-1",
                "target_grace_id": "n-1",
                "relationship_type": "employs",
                "since": "2020",
            },
        ],
    }

    results, rels = neighborhood_to_serializer_inputs(neighborhood)

    assert len(results) == 2  # seed + 1 neighbor
    assert isinstance(results[0], RankedResult)
    assert results[0].grace_id == "seed-1"
    assert results[0].entity_type == "Legal_Entity"
    assert results[0].name == "Acme"
    assert results[1].name == "Alice"

    assert len(rels) == 1
    assert rels[0]["relationship_type"] == "employs"
    assert rels[0]["since"] == "2020"


def test_neighborhood_to_serializer_inputs_empty():
    """Empty neighborhood produces empty lists."""
    results, rels = neighborhood_to_serializer_inputs({"seed": {}, "neighbors": [], "edges": []})
    assert results == []
    assert rels == []


def test_neighborhood_to_serializer_inputs_entity_cap():
    """Entity cap limits the number of results."""
    neighborhood = {
        "seed": {"grace_id": "s", "@type": "E", "name": "S"},
        "neighbors": [
            {"grace_id": f"n-{i}", "@type": "E", "name": f"N{i}"}
            for i in range(20)
        ],
        "edges": [],
    }
    results, _ = neighborhood_to_serializer_inputs(neighborhood, entity_cap=5)
    assert len(results) == 5


def test_neighborhood_to_serializer_inputs_excludes_system_keys():
    """System keys like @rid, @cat, @in, @out are excluded from properties."""
    neighborhood = {
        "seed": {
            "grace_id": "s",
            "@type": "E",
            "@rid": "#1:0",
            "@cat": "v",
            "@in": {},
            "@out": {},
            "name": "Test",
            "real_prop": "value",
        },
        "neighbors": [],
        "edges": [],
    }
    results, _ = neighborhood_to_serializer_inputs(neighborhood)
    assert len(results) == 1
    props = results[0].properties
    assert "@rid" not in props
    assert "@cat" not in props
    assert "real_prop" in props


# ---------------------------------------------------------------------------
# Alembic migration test
# ---------------------------------------------------------------------------


def test_alembic_migration_file_exists():
    """mine_samples migration file exists."""
    from pathlib import Path

    migration = Path("alembic/versions/c3d4e5f6a7b8_create_mine_samples_table.py")
    assert migration.exists(), f"Migration file not found: {migration}"


def test_mine_sample_row_model():
    """MineSampleRow has expected columns."""
    columns = {c.name for c in MineSampleRow.__table__.columns}
    expected = {
        "id", "document_id", "source_text_hash", "source_facts", "judgments",
        "total_facts", "recovered_facts", "retention_score", "extraction_model",
        "judge_model", "schema_version_id", "sampled_at", "metadata_extra",
    }
    assert expected.issubset(columns)
