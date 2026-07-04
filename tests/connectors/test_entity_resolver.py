"""Tests for entity resolver (CP5, D410)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.connectors.entity_resolver import resolve_or_create
from src.connectors.models import ConnectorRecord, ResolvedEntity
from src.graph.management_models import GraphNamespace


# ---------------------------------------------------------------------------
# Mock infrastructure
# ---------------------------------------------------------------------------


@dataclass
class MockCanonicalEntity:
    canonical_grace_id: UUID = field(default_factory=uuid4)
    canonical_name: str = "Test Entity"
    canonical_type: str = "Legal_Entity"
    embedding_vector: list[float] | None = None


class MockRegistry:
    """Duck-typed CanonicalEntityRegistry mock."""

    def __init__(self, result: tuple | None = None):
        self._result = result or (None, "unresolved")

    async def resolve(self, name: str, entity_type: str) -> tuple:
        return self._result


class MockArcadeClient:
    """Tracks cypher calls."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute_cypher(self, cypher: str, params: dict) -> None:
        self.calls.append((cypher, params))


class MockSession:
    """Tracks SQL executions and commits."""

    def __init__(self):
        self.executions: list[tuple] = []
        self.commit_count = 0

    def execute(self, stmt, params=None):
        self.executions.append((str(stmt), params))

    def commit(self):
        self.commit_count += 1


def _make_record(name: str = "Test Corp") -> ConnectorRecord:
    return ConnectorRecord(
        source_record_id="r-001",
        entity_type="Legal_Entity",
        name=name,
        source_system="SyntheticA",
        source_updated_at=datetime.now(UTC),
    )


def _make_namespace() -> GraphNamespace:
    return GraphNamespace(
        id=str(uuid4()),
        database_name="test_child_ns",
        label_prefix="Test",
        namespace_type="child",
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_exact_match_writes_bridge_entity() -> None:
    """Exact match → Bridge_Entity edge written with 6 properties."""
    entity = MockCanonicalEntity()
    registry = MockRegistry((entity, "exact"))
    arcade = MockArcadeClient()
    db = MockSession()

    result = _run(resolve_or_create(
        _make_record(), _make_namespace(), registry,
        arcade_client=arcade, db=db,
        ollama_base_url="http://localhost:11434",
    ))

    assert result.outcome == "bridged"
    assert result.canonical_grace_id == str(entity.canonical_grace_id)
    assert len(arcade.calls) == 1
    cypher, params = arcade.calls[0]
    assert "Bridge_Entity" in cypher
    # Verify 6 properties
    for key in ("grace_id", "canonical_grace_id", "child_grace_id", "namespace", "resolution_method", "resolved_at"):
        assert key in params


def test_embedding_above_threshold_writes_bridge() -> None:
    """Embedding match >= 0.92 → Bridge_Entity edge written."""
    # Use a vector that will produce high cosine with itself
    vec = [1.0] * 768
    entity = MockCanonicalEntity(embedding_vector=vec)
    registry = MockRegistry((entity, "embedding"))
    arcade = MockArcadeClient()
    db = MockSession()

    async def mock_embed_texts(texts, base_url, model="nomic-embed-text"):
        return [vec]  # Same vector → cosine ~1.0

    import src.connectors.entity_resolver as er
    original = er.embed_texts
    er.embed_texts = mock_embed_texts

    try:
        result = _run(resolve_or_create(
            _make_record(), _make_namespace(), registry,
            arcade_client=arcade, db=db,
            ollama_base_url="http://localhost:11434",
        ))
        assert result.outcome == "bridged"
        assert len(arcade.calls) == 1
    finally:
        er.embed_texts = original


def test_embedding_below_threshold_queues() -> None:
    """Embedding match < 0.92 → child entity created + review queue row."""
    # Use orthogonal vectors for low cosine
    vec_entity = [1.0] + [0.0] * 767
    vec_query = [0.0] + [1.0] + [0.0] * 766
    entity = MockCanonicalEntity(embedding_vector=vec_entity)
    registry = MockRegistry((entity, "embedding"))
    arcade = MockArcadeClient()
    db = MockSession()

    async def mock_embed_texts(texts, base_url, model="nomic-embed-text"):
        return [vec_query]

    import src.connectors.entity_resolver as er
    original = er.embed_texts
    er.embed_texts = mock_embed_texts

    try:
        result = _run(resolve_or_create(
            _make_record(), _make_namespace(), registry,
            arcade_client=arcade, db=db,
            ollama_base_url="http://localhost:11434",
        ))
        assert result.outcome == "queued"
        assert len(arcade.calls) == 0  # No bridge edge
        assert len(db.executions) == 1  # Queue row inserted
        assert "entity_resolution_review_queue" in db.executions[0][0]
        # proposed_canonical_grace_id should be populated
        assert db.executions[0][1]["proposed_gid"] == str(entity.canonical_grace_id)
    finally:
        er.embed_texts = original


def test_unresolved_queues_with_null_canonical() -> None:
    """Unresolved → child entity created + queue row with proposed_canonical_grace_id = NULL."""
    registry = MockRegistry((None, "unresolved"))
    arcade = MockArcadeClient()
    db = MockSession()

    result = _run(resolve_or_create(
        _make_record(), _make_namespace(), registry,
        arcade_client=arcade, db=db,
        ollama_base_url="http://localhost:11434",
    ))

    assert result.outcome == "queued"
    assert len(arcade.calls) == 0
    assert len(db.executions) == 1
    assert db.executions[0][1]["proposed_gid"] is None


def test_no_cross_system_reference_edges() -> None:
    """Does NOT write Cross_System_Reference edges."""
    entity = MockCanonicalEntity()
    registry = MockRegistry((entity, "exact"))
    arcade = MockArcadeClient()
    db = MockSession()

    _run(resolve_or_create(
        _make_record(), _make_namespace(), registry,
        arcade_client=arcade, db=db,
        ollama_base_url="http://localhost:11434",
    ))

    for cypher, _ in arcade.calls:
        assert "Cross_System_Reference" not in cypher


def test_high_confidence_floor_configurable() -> None:
    """high_confidence_floor parameter is configurable."""
    # With floor=0.0, even orthogonal vectors should bridge
    vec_entity = [1.0] + [0.0] * 767
    vec_query = [0.0] + [1.0] + [0.0] * 766
    entity = MockCanonicalEntity(embedding_vector=vec_entity)
    registry = MockRegistry((entity, "embedding"))
    arcade = MockArcadeClient()
    db = MockSession()

    async def mock_embed_texts(texts, base_url, model="nomic-embed-text"):
        return [vec_query]

    import src.connectors.entity_resolver as er
    original = er.embed_texts
    er.embed_texts = mock_embed_texts

    try:
        # With floor=0.0, this should still queue because cosine is ~0
        result = _run(resolve_or_create(
            _make_record(), _make_namespace(), registry,
            arcade_client=arcade, db=db,
            ollama_base_url="http://localhost:11434",
            high_confidence_floor=0.0,
        ))
        # Orthogonal vectors give cosine=0, which is >= 0.0, so it bridges
        assert result.outcome == "bridged"
    finally:
        er.embed_texts = original


def test_queue_row_status_pending() -> None:
    """Queue row has status='pending' on insert."""
    registry = MockRegistry((None, "unresolved"))
    arcade = MockArcadeClient()
    db = MockSession()

    _run(resolve_or_create(
        _make_record(), _make_namespace(), registry,
        arcade_client=arcade, db=db,
        ollama_base_url="http://localhost:11434",
    ))

    sql_str = db.executions[0][0]
    assert "'pending'" in sql_str


def test_uses_embed_texts_from_shared() -> None:
    """Uses await embed_texts(...) from src/shared/embeddings (not registry internals)."""
    import inspect
    import src.connectors.entity_resolver as er_mod
    source = inspect.getsource(er_mod)
    assert "from src.shared.embeddings import" in source or "src.shared.embeddings" in source


def test_resolved_entity_outcome_reflects_path() -> None:
    """ResolvedEntity.outcome reflects the actual resolution path."""
    entity = MockCanonicalEntity()

    # exact → bridged
    result1 = _run(resolve_or_create(
        _make_record(), _make_namespace(), MockRegistry((entity, "exact")),
        arcade_client=MockArcadeClient(), db=MockSession(),
        ollama_base_url="http://localhost:11434",
    ))
    assert result1.outcome == "bridged"

    # unresolved → queued
    result2 = _run(resolve_or_create(
        _make_record(), _make_namespace(), MockRegistry((None, "unresolved")),
        arcade_client=MockArcadeClient(), db=MockSession(),
        ollama_base_url="http://localhost:11434",
    ))
    assert result2.outcome == "queued"


def test_multiple_records_same_name_consistent() -> None:
    """Multiple records with same name produce consistent results."""
    entity = MockCanonicalEntity()
    registry = MockRegistry((entity, "exact"))

    results = []
    for _ in range(3):
        r = _run(resolve_or_create(
            _make_record("Consistent Corp"),
            _make_namespace(),
            registry,
            arcade_client=MockArcadeClient(),
            db=MockSession(),
            ollama_base_url="http://localhost:11434",
        ))
        results.append(r.outcome)

    assert all(o == "bridged" for o in results)
