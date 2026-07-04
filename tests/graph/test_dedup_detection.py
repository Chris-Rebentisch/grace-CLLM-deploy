"""Tests for duplicate entity detection (mocked ArcadeDB, no live server).

Covers the F-0025b / ISS-0035 suffix-OCR-variant pass and the
F-0003 / ISS-0043 FROM-V rider (schema:types enumeration, V-less DB).
"""

import re
from unittest.mock import AsyncMock

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.graph.dedup_detection import (
    _canonicalize_name_for_dedup,
    detect_duplicates,
    detect_fuzzy_duplicates,
)

_FROM_V_RE = re.compile(r"\bFROM\s+V\b", re.IGNORECASE)
_WRITE_RE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|UPDATE|INSERT|DROP)\b", re.IGNORECASE)


def _mock_client() -> ArcadeClient:
    """Create an ArcadeClient with mocked execute_cypher and execute_sql."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock()
    client.execute_sql = AsyncMock()
    return client


def _all_queries(client: ArcadeClient) -> list[str]:
    """Every SQL + Cypher query string issued through the mocked client."""
    return [
        call.args[0]
        for mock in (client.execute_sql, client.execute_cypher)
        for call in mock.call_args_list
    ]


@pytest.mark.asyncio
async def test_exact_name_match_detected():
    """Exact name duplicates within same type are detected (regression pin)."""
    client = _mock_client()
    client.execute_cypher.side_effect = [
        # Exact-name pass
        {"result": [{"a_id": "uuid-1", "b_id": "uuid-2", "name": "Alice"}]},
        # Suffix-variant pass entity fetch — nothing to converge
        {"result": []},
    ]
    report = await detect_duplicates(client, entity_type="Person")
    assert report.total_candidates == 1
    assert report.candidates[0].entity_type == "Person"
    assert report.candidates[0].name == "Alice"
    assert report.candidates[0].match_type == "exact_name"
    assert report.by_type == {"Person": 1}


@pytest.mark.asyncio
async def test_different_types_not_matched():
    """Entities with same name but different types are NOT matched (type-scoped)."""
    client = _mock_client()
    # ISS-0043 rider: types are enumerated from schema:types (never FROM V)
    client.execute_sql.return_value = {
        "result": [
            {"name": "Person", "type": "vertex"},
            {"name": "Company", "type": "vertex"},
            {"name": "party_to", "type": "edge"},  # edge types excluded
        ]
    }
    # Per-type queries: exact pass + variant-pass entity fetch, per type
    client.execute_cypher.side_effect = [
        {"result": []},  # Person: exact
        {"result": []},  # Person: entities
        {"result": []},  # Company: exact
        {"result": []},  # Company: entities
    ]
    report = await detect_duplicates(client, entity_type=None)
    assert report.total_candidates == 0
    assert report.candidates == []
    # Both vertex types (and only vertex types) were scanned
    assert client.execute_cypher.call_count == 4


@pytest.mark.asyncio
async def test_deprecated_excluded():
    """Deprecated entities are excluded from duplicate detection."""
    client = _mock_client()
    client.execute_cypher.return_value = {"result": []}
    report = await detect_duplicates(client, entity_type="Person")
    assert report.total_candidates == 0
    # Both passes filter deprecated entities
    exact_query = client.execute_cypher.call_args_list[0][0][0]
    entities_query = client.execute_cypher.call_args_list[1][0][0]
    assert "_deprecated = false" in exact_query
    assert "_deprecated = false" in entities_query


@pytest.mark.asyncio
async def test_no_duplicates_empty_report():
    """No duplicates returns empty report."""
    client = _mock_client()
    client.execute_sql.return_value = {
        "result": [{"name": "Person", "type": "vertex"}]
    }
    client.execute_cypher.return_value = {"result": []}
    report = await detect_duplicates(client, entity_type=None)
    assert report.total_candidates == 0
    assert report.by_type == {}
    assert report.candidates == []


# --- Suffix-OCR-variant pass (F-0025b / ISS-0035) ---


@pytest.mark.asyncio
async def test_suffix_variant_llo_llc_pair_detected():
    """The F-0025b pair (LLO scan misread of LLC) surfaces as a candidate."""
    client = _mock_client()
    client.execute_cypher.side_effect = [
        {"result": []},  # exact pass: raw names differ, no exact match
        {"result": [
            {"n.grace_id": "uuid-1", "n.name": "Cedar Grove Residences LLC"},
            {"n.grace_id": "uuid-2", "n.name": "Cedar Grove Residences LLO"},
        ]},
    ]
    report = await detect_duplicates(client, entity_type="Legal_Entity")
    assert report.total_candidates == 1
    cand = report.candidates[0]
    assert cand.match_type == "normalized_name"
    assert cand.entity_a_grace_id == "uuid-1"
    assert cand.entity_b_grace_id == "uuid-2"
    assert "LLO" in cand.name and "LLC" in cand.name
    assert report.by_type == {"Legal_Entity": 1}


@pytest.mark.asyncio
async def test_suffix_variant_token_boundary_no_false_positive():
    """Names merely ENDING in variant letter-sequences never converge.

    F-0025b / ISS-0035: canonicalization is whole-trailing-token only —
    "Zinc" must NOT become "z", "Apollo" must NOT become "apo llc".
    """
    client = _mock_client()
    client.execute_cypher.side_effect = [
        {"result": []},  # exact pass
        {"result": [
            {"n.grace_id": "uuid-1", "n.name": "Zinc"},
            {"n.grace_id": "uuid-2", "n.name": "Z Inc"},
            {"n.grace_id": "uuid-3", "n.name": "Apollo"},
            {"n.grace_id": "uuid-4", "n.name": "Apo LLO"},
        ]},
    ]
    report = await detect_duplicates(client, entity_type="Legal_Entity")
    # "Zinc" -> "zinc" (unchanged); "Z Inc" -> "z inc" (inc is canonical, not
    # a variant); "Apollo" -> "apollo"; "Apo LLO" -> "apo llc". No collisions.
    assert report.total_candidates == 0


def test_canonicalize_variants_and_boundaries():
    """Unit pin of the canonicalization map + token-boundary behavior."""
    assert _canonicalize_name_for_dedup("Cedar Grove Residences LLO") == \
        "cedar grove residences llc"
    assert _canonicalize_name_for_dedup("Acme LLG") == "acme llc"
    assert _canonicalize_name_for_dedup("Acme L.L.C.") == "acme llc"
    assert _canonicalize_name_for_dedup("Foo lnc") == "foo inc"
    assert _canonicalize_name_for_dedup("Foo 1nc") == "foo inc"
    assert _canonicalize_name_for_dedup("Bar C0rp") == "bar corp"
    assert _canonicalize_name_for_dedup("Baz 1td") == "baz ltd"
    # Token boundary: substrings never rewritten
    assert _canonicalize_name_for_dedup("Zinc") == "zinc"
    assert _canonicalize_name_for_dedup("Apollo") == "apollo"
    assert _canonicalize_name_for_dedup("Costello") == "costello"
    # Deliberately NOT full suffix stripping — different suffixes stay distinct
    assert _canonicalize_name_for_dedup("Acme LLC") != \
        _canonicalize_name_for_dedup("Acme Inc")


@pytest.mark.asyncio
async def test_exact_pairs_not_double_reported_by_variant_pass():
    """Identical raw names are the exact pass's job — variant pass skips them."""
    client = _mock_client()
    client.execute_cypher.side_effect = [
        # exact pass reports the identical-name pair once
        {"result": [{"a_id": "uuid-1", "b_id": "uuid-2", "name": "Acme LLC"}]},
        # variant-pass entity fetch sees the same two rows
        {"result": [
            {"n.grace_id": "uuid-1", "n.name": "Acme LLC"},
            {"n.grace_id": "uuid-2", "n.name": "Acme LLC"},
        ]},
    ]
    report = await detect_duplicates(client, entity_type="Legal_Entity")
    assert report.total_candidates == 1
    assert report.candidates[0].match_type == "exact_name"


@pytest.mark.asyncio
async def test_detection_is_report_only():
    """`GET /duplicates` never auto-merges — every issued query is read-only."""
    client = _mock_client()
    client.execute_cypher.side_effect = [
        {"result": []},
        {"result": [
            {"n.grace_id": "uuid-1", "n.name": "Cedar Grove Residences LLC"},
            {"n.grace_id": "uuid-2", "n.name": "Cedar Grove Residences LLO"},
        ]},
    ]
    report = await detect_duplicates(client, entity_type="Legal_Entity")
    assert report.total_candidates == 1
    for query in _all_queries(client):
        assert not _WRITE_RE.search(query), f"write-shaped query issued: {query}"


# --- FROM-V rider (F-0003 / ISS-0043) ---


@pytest.mark.asyncio
async def test_vless_schema_clean_empty_report():
    """A V-less / schema-less database yields a clean empty report, not a 500."""
    client = _mock_client()
    client.execute_sql.return_value = {"result": []}  # no types registered
    report = await detect_duplicates(client, entity_type=None)
    assert report.total_candidates == 0
    assert report.by_type == {}
    assert report.candidates == []
    client.execute_cypher.assert_not_called()
    for query in _all_queries(client):
        assert not _FROM_V_RE.search(query), f"FROM V issued: {query}"


@pytest.mark.asyncio
async def test_typed_db_never_queries_v_supertype():
    """Type enumeration goes through schema:types; no query touches `V`."""
    client = _mock_client()
    client.execute_sql.return_value = {
        "result": [{"name": "Person", "type": "vertex"}]
    }
    client.execute_cypher.return_value = {"result": []}
    await detect_duplicates(client, entity_type=None)
    schema_query = client.execute_sql.call_args_list[0][0][0]
    assert "schema:types" in schema_query
    for query in _all_queries(client):
        assert not _FROM_V_RE.search(query), f"FROM V issued: {query}"


@pytest.mark.asyncio
async def test_type_names_backtick_quoted():
    """Server-controlled type names are backtick-quoted when interpolated."""
    client = _mock_client()
    client.execute_cypher.return_value = {"result": []}
    await detect_duplicates(client, entity_type="Legal_Entity")
    exact_query = client.execute_cypher.call_args_list[0][0][0]
    entities_query = client.execute_cypher.call_args_list[1][0][0]
    assert "`Legal_Entity`" in exact_query
    assert "`Legal_Entity`" in entities_query


# --- Fuzzy duplicate detection tests ---


@pytest.mark.asyncio
async def test_fuzzy_duplicates_above_threshold():
    """detect_fuzzy_duplicates returns candidates above threshold."""
    client = _mock_client()
    # Return two similar entities
    client.execute_cypher.return_value = {
        "result": [
            {"n.grace_id": "uuid-1", "n.name": "Acme Corp"},
            {"n.grace_id": "uuid-2", "n.name": "Acme Corporation"},
        ]
    }

    # Mock per-entity SQL queries: embedding fetch + vectorNeighbors
    async def sql_side_effect(sql, *args, **kwargs):
        if "SELECT _embedding" in sql:
            return {"result": [{"_embedding": [1.0, 0.0, 0.0]}]}
        elif "vectorNeighbors" in sql:
            return {"result": [{"neighbors": [
                {"grace_id": "uuid-1", "name": "Acme Corp",
                 "_deprecated": False, "distance": 0.0},
                {"grace_id": "uuid-2", "name": "Acme Corporation",
                 "_deprecated": False, "distance": 0.05},
            ]}]}
        return {"result": []}

    client.execute_sql.side_effect = sql_side_effect

    report = await detect_fuzzy_duplicates(
        client, entity_type="Legal_Entity", similarity_threshold=0.85
    )

    assert report.total_candidates == 1
    assert report.candidates[0].match_type == "embedding_similarity"
    assert report.candidates[0].similarity_score is not None
    assert report.candidates[0].similarity_score > 0.85


@pytest.mark.asyncio
async def test_fuzzy_duplicates_below_threshold_empty():
    """detect_fuzzy_duplicates returns empty when all below threshold."""
    client = _mock_client()
    client.execute_cypher.return_value = {
        "result": [
            {"n.grace_id": "uuid-1", "n.name": "Acme Corp"},
            {"n.grace_id": "uuid-2", "n.name": "Totally Different"},
        ]
    }

    # Mock per-entity SQL: embedding fetch + vectorNeighbors with high distance
    async def sql_side_effect(sql, *args, **kwargs):
        if "SELECT _embedding" in sql:
            return {"result": [{"_embedding": [1.0, 0.0, 0.0]}]}
        elif "vectorNeighbors" in sql:
            # All neighbors have high distance — below threshold
            return {"result": [{"neighbors": [
                {"grace_id": "uuid-1", "name": "Acme Corp",
                 "_deprecated": False, "distance": 0.5},
                {"grace_id": "uuid-2", "name": "Totally Different",
                 "_deprecated": False, "distance": 0.9},
            ]}]}
        return {"result": []}

    client.execute_sql.side_effect = sql_side_effect

    report = await detect_fuzzy_duplicates(
        client, entity_type="Legal_Entity", similarity_threshold=0.85
    )

    assert report.total_candidates == 0


@pytest.mark.asyncio
async def test_fuzzy_vless_schema_clean_empty_report():
    """Fuzzy path with no entity_type on a V-less DB is clean-empty too."""
    client = _mock_client()
    client.execute_sql.return_value = {"result": []}
    report = await detect_fuzzy_duplicates(client, entity_type=None)
    assert report.total_candidates == 0
    for query in _all_queries(client):
        assert not _FROM_V_RE.search(query), f"FROM V issued: {query}"


@pytest.mark.asyncio
async def test_duplicate_candidate_backward_compat():
    """DuplicateCandidate with similarity_score=None works for exact_name."""
    from src.graph.management_models import DuplicateCandidate

    candidate = DuplicateCandidate(
        entity_a_grace_id="a",
        entity_b_grace_id="b",
        entity_type="Person",
        name="Alice",
        match_type="exact_name",
    )
    assert candidate.similarity_score is None
