"""Tests for orphan vertex detection (mocked ArcadeDB, no live server).

Covers the F-0003 / ISS-0043 FROM-V rider: concrete-type enumeration via
schema:types, clean empty report on a V-less database, no V-supertype query.
"""

import re
from unittest.mock import AsyncMock

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.graph.orphan_detection import detect_orphans

_FROM_V_RE = re.compile(r"\bFROM\s+V\b", re.IGNORECASE)


def _mock_client() -> ArcadeClient:
    """Create an ArcadeClient with mocked execute_sql."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_sql = AsyncMock()
    return client


def _sql_queries(client: ArcadeClient) -> list[str]:
    return [call.args[0] for call in client.execute_sql.call_args_list]


@pytest.mark.asyncio
async def test_orphans_found():
    """detect_orphans returns orphan entities with no edges."""
    client = _mock_client()
    client.execute_sql.side_effect = [
        # schema:types enumeration (ISS-0043 rider — never FROM V)
        {"result": [
            {"name": "Person", "type": "vertex"},
            {"name": "Company", "type": "vertex"},
            {"name": "party_to", "type": "edge"},  # edges excluded
        ]},
        # Person: orphan query + count
        {"result": [{"grace_id": "orphan-1", "name": "Alice", "extracted_at": None}]},
        {"result": [{"cnt": 6}]},
        # Company: orphan query + count
        {"result": [{"grace_id": "orphan-2", "name": "Acme", "extracted_at": None}]},
        {"result": [{"cnt": 4}]},
    ]
    report = await detect_orphans(client)
    assert report.orphan_count == 2
    assert report.total_entities == 10
    assert report.orphan_rate == pytest.approx(0.2)
    assert len(report.orphans) == 2
    assert report.orphans[0].grace_id == "orphan-1"
    # entity_type comes from the concrete type being scanned
    assert report.orphans[0].entity_type == "Person"
    assert report.orphans[1].name == "Acme"
    assert report.orphans[1].entity_type == "Company"


@pytest.mark.asyncio
async def test_no_orphans():
    """detect_orphans returns empty list when all entities have edges."""
    client = _mock_client()
    client.execute_sql.side_effect = [
        {"result": [{"name": "Person", "type": "vertex"}]},
        {"result": []},  # No orphans
        {"result": [{"cnt": 5}]},  # Total count
    ]
    report = await detect_orphans(client)
    assert report.orphan_count == 0
    assert report.total_entities == 5
    assert report.orphan_rate == 0.0
    assert report.orphans == []


@pytest.mark.asyncio
async def test_deprecated_excluded():
    """detect_orphans SQL queries exclude deprecated entities."""
    client = _mock_client()
    client.execute_sql.side_effect = [
        {"result": [{"name": "Person", "type": "vertex"}]},
        {"result": []},  # Orphan query already filters _deprecated = false
        {"result": [{"cnt": 3}]},
    ]
    report = await detect_orphans(client)
    assert report.orphan_count == 0
    # Both the orphan query and the total-count query filter deprecated rows
    queries = _sql_queries(client)
    assert "_deprecated = false" in queries[1]
    assert "_deprecated = false" in queries[2]


@pytest.mark.asyncio
async def test_empty_graph():
    """detect_orphans handles empty graph (types registered, zero rows)."""
    client = _mock_client()
    client.execute_sql.side_effect = [
        {"result": [{"name": "Person", "type": "vertex"}]},
        {"result": []},  # No orphans
        {"result": [{"cnt": 0}]},  # Zero entities
    ]
    report = await detect_orphans(client)
    assert report.orphan_count == 0
    assert report.total_entities == 0
    assert report.orphan_rate == 0.0


# --- FROM-V rider (F-0003 / ISS-0043) ---


@pytest.mark.asyncio
async def test_vless_schema_clean_empty_report():
    """A V-less / schema-less database yields a clean empty report, not a 500."""
    client = _mock_client()
    client.execute_sql.return_value = {"result": []}  # no types registered
    report = await detect_orphans(client)
    assert report.orphan_count == 0
    assert report.total_entities == 0
    assert report.orphan_rate == 0.0
    assert report.orphans == []
    # Exactly one query issued (the schema enumeration) and it is not FROM V
    assert client.execute_sql.call_count == 1
    assert "schema:types" in _sql_queries(client)[0]


@pytest.mark.asyncio
async def test_never_queries_v_supertype():
    """No issued SQL ever targets the generic V supertype; type names quoted."""
    client = _mock_client()
    client.execute_sql.side_effect = [
        {"result": [{"name": "Legal_Entity", "type": "vertex"}]},
        {"result": []},
        {"result": [{"cnt": 2}]},
    ]
    await detect_orphans(client)
    queries = _sql_queries(client)
    for query in queries:
        assert not _FROM_V_RE.search(query), f"FROM V issued: {query}"
    # Server-controlled type names are backtick-quoted when interpolated
    assert "FROM `Legal_Entity`" in queries[1]
    assert "FROM `Legal_Entity`" in queries[2]
