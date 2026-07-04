"""Tests for entity CRUD operations (mocked ArcadeDB, no live server)."""

from unittest.mock import AsyncMock, patch

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.graph.entity_models import (
    BulkInsertRequest,
    EntityCreate,
    EntityUpdate,
)
from src.graph.entity_ops import (
    append_entity_alias,
    bulk_insert,
    canonical_lookup,
    get_entity,
    insert_entity,
    update_entity,
)


# ---------------------------------------------------------------------------
# Helper — build a mock ArcadeClient with mocked execute_cypher
# ---------------------------------------------------------------------------


def _mock_arcade_client() -> ArcadeClient:
    """Create an ArcadeClient with mocked execute_cypher."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock()
    return client


# ===========================================================================
# canonical_lookup tests
# ===========================================================================


@pytest.mark.asyncio
async def test_canonical_lookup_hit():
    """canonical_lookup returns grace_id when entity exists."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {
        "result": [{"n.grace_id": "existing-uuid"}]
    }
    result = await canonical_lookup(client, "Person", "Alice")
    assert result == "existing-uuid"
    # Verify opencypher query was called
    call_args = client.execute_cypher.call_args[0][0]
    assert "MATCH" in call_args
    assert "Person" in call_args
    assert "Alice" in call_args


@pytest.mark.asyncio
async def test_canonical_lookup_is_case_insensitive():
    """F-28 residual: canonical_lookup must case-fold both sides so
    'Riverbend Road tract' dedups with 'Riverbend Road Tract' at insert time
    (case-variant vertices otherwise split a sender's corroboration count).

    The generated OpenCypher must wrap both the stored name and the query
    literal in toLower() (verified live against grace_test to actually match).
    """
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {"result": [{"n.grace_id": "gid-1"}]}
    result = await canonical_lookup(client, "Land_Parcel", "Riverbend Road tract")
    assert result == "gid-1"
    query = client.execute_cypher.call_args[0][0]
    assert "toLower(n.name) = toLower(" in query, query
    # Alias comparison must also be case-insensitive.
    assert "ANY(a IN n.aliases WHERE toLower(a) = toLower(" in query, query


@pytest.mark.asyncio
async def test_canonical_lookup_miss():
    """canonical_lookup returns None when no entity matches."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {"result": []}
    result = await canonical_lookup(client, "Person", "Unknown")
    assert result is None


@pytest.mark.asyncio
async def test_canonical_lookup_no_name():
    """canonical_lookup returns None when name is None."""
    client = _mock_arcade_client()
    result = await canonical_lookup(client, "Person", None)
    assert result is None
    client.execute_cypher.assert_not_called()


# ===========================================================================
# insert_entity tests
# ===========================================================================


@pytest.mark.asyncio
async def test_insert_entity_new():
    """insert_entity creates new entity with grace_id, returns created=True."""
    client = _mock_arcade_client()
    # canonical_lookup returns empty (no match)
    # CREATE returns the new node
    client.execute_cypher.side_effect = [
        {"result": []},  # canonical_lookup
        {"result": [{"@rid": "#1:0", "@type": "Person", "grace_id": "new-uuid"}]},  # CREATE
    ]
    entity = EntityCreate(entity_type="Person", properties={"name": "Alice"})
    result = await insert_entity(client, entity)
    assert result.created is True
    assert result.canonical_match is False
    assert result.entity_type == "Person"
    assert result.rid == "#1:0"
    assert result.grace_id  # UUID was generated


@pytest.mark.asyncio
async def test_insert_entity_canonical_match():
    """insert_entity finds canonical match, returns created=False.

    F-016 / ISS-0008: when the incoming payload has nothing the existing
    vertex lacks, NO fill-only merge write is issued (fetch is the last call).
    """
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": [{"n.grace_id": "existing-uuid"}]},  # canonical_lookup hit
        # fetch existing — already carries `name`, so nothing to fill
        {"result": [{"@rid": "#1:5", "grace_id": "existing-uuid", "name": "Alice"}]},
    ]
    entity = EntityCreate(entity_type="Person", properties={"name": "Alice"})
    result = await insert_entity(client, entity)
    assert result.created is False
    assert result.canonical_match is True
    assert result.grace_id == "existing-uuid"
    assert result.rid == "#1:5"
    assert client.execute_cypher.call_count == 2  # no merge SET issued


@pytest.mark.asyncio
async def test_insert_entity_includes_system_properties():
    """insert_entity includes all system properties in Cypher CREATE."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": []},  # canonical_lookup
        {"result": [{"@rid": "#2:0"}]},  # CREATE
    ]
    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={"name": "Acme"},
        extraction_confidence=0.9,
        human_validated=True,
    )
    result = await insert_entity(client, entity)
    assert result.created is True
    # Check the CREATE query includes system props
    create_query = client.execute_cypher.call_args_list[1][0][0]
    assert "grace_id" in create_query
    assert "extracted_at" in create_query
    assert "_deprecated" in create_query
    assert "human_validated" in create_query


# ===========================================================================
# get_entity tests
# ===========================================================================


@pytest.mark.asyncio
async def test_get_entity_found():
    """get_entity returns dict when entity exists."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {
        "result": [{"@rid": "#1:0", "grace_id": "uuid-1", "name": "Alice"}]
    }
    result = await get_entity(client, "uuid-1")
    assert result is not None
    assert result["name"] == "Alice"


@pytest.mark.asyncio
async def test_get_entity_not_found():
    """get_entity returns None when entity does not exist."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {"result": []}
    result = await get_entity(client, "nonexistent")
    assert result is None


# ===========================================================================
# update_entity tests
# ===========================================================================


@pytest.mark.asyncio
async def test_update_entity_partial():
    """update_entity applies partial update (2 of 5 properties)."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {
        "result": [{"@rid": "#1:0", "grace_id": "uuid-1", "name": "Bob", "age": 30}]
    }
    update = EntityUpdate(properties={"name": "Bob", "age": 30})
    result = await update_entity(client, "uuid-1", update)
    assert result["name"] == "Bob"
    query = client.execute_cypher.call_args[0][0]
    assert "SET" in query
    assert "n.name = 'Bob'" in query
    assert "n.age = 30" in query


@pytest.mark.asyncio
async def test_update_entity_not_found():
    """update_entity raises ValueError when entity not found."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {"result": []}
    update = EntityUpdate(properties={"name": "Ghost"})
    with pytest.raises(ValueError, match="Entity not found"):
        await update_entity(client, "nonexistent", update)


# ===========================================================================
# bulk_insert tests
# ===========================================================================


@pytest.mark.asyncio
async def test_bulk_insert_mixed():
    """bulk_insert with mix of successes and failures returns correct counts."""
    client = _mock_arcade_client()

    call_count = 0

    async def mock_execute_cypher(query, **kwargs):
        nonlocal call_count
        call_count += 1
        # First entity: canonical miss + successful CREATE
        if call_count == 1:
            return {"result": []}  # canonical miss
        if call_count == 2:
            return {"result": [{"@rid": "#1:0"}]}  # CREATE success
        # Second entity: canonical miss + CREATE fails
        if call_count == 3:
            return {"result": []}  # canonical miss
        if call_count == 4:
            raise Exception("ArcadeDB write error")
        return {"result": []}

    client.execute_cypher = AsyncMock(side_effect=mock_execute_cypher)

    request = BulkInsertRequest(
        entities=[
            EntityCreate(entity_type="Person", properties={"name": "Alice"}),
            EntityCreate(entity_type="Person", properties={"name": "Bob"}),
        ],
        extraction_event_id="batch-evt-1",
    )
    result = await bulk_insert(client, request)
    assert result.entities_created == 1
    assert result.entities_failed == 1
    assert len(result.errors) == 1
    # Verify batch-level extraction_event_id was propagated
    assert request.entities[0].extraction_event_id == "batch-evt-1"


@pytest.mark.asyncio
async def test_append_entity_alias_appends_when_missing():
    """append_entity_alias adds alias when it doesn't exist."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": [{"aliases": ["Acme Corporation"]}]},
        {"result": [{"n.grace_id": "uuid-1"}]},
    ]
    changed = await append_entity_alias(client, "uuid-1", "Acme Corp")
    assert changed is True
    update_query = client.execute_cypher.call_args_list[1][0][0]
    assert "SET n.aliases" in update_query
    assert "Acme Corp" in update_query


@pytest.mark.asyncio
async def test_append_entity_alias_noop_when_present():
    """append_entity_alias is no-op when alias already exists."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {"result": [{"aliases": ["Acme Corp"]}]}
    changed = await append_entity_alias(client, "uuid-1", "Acme Corp")
    assert changed is False
    # Only fetch query should run
    assert client.execute_cypher.call_count == 1
