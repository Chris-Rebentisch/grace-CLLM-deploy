"""Tests for the federation query router (Chunk 52, D384 CF3 relaxation).

~22 tests covering resolve_target_namespaces, federated_query,
merge_results, and the internal type adapters.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.federation.models import FederationConfig
from src.retrieval.federation_router import (
    FederatedRetrievalResponse,
    NamespaceTarget,
    NamespaceWarning,
    QueryRoutingConfig,
    _fused_to_ranked,
    _ranked_to_candidate,
    _strip_label_prefix,
    federated_query,
    merge_results,
    reset_federation_circuit,
    resolve_target_namespaces,
)
from src.retrieval.retrieval_models import (
    FusedCandidate,
    RankedResult,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResponse,
)


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """D498: the per-namespace circuit breaker is module-global state; clear it
    before each test so a failure tripped in one test never leaks into another."""
    reset_federation_circuit()
    yield
    reset_federation_circuit()


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_ranked_result(
    grace_id: str = "g1",
    entity_type: str = "Legal_Entity",
    name: str = "TestEntity",
    rrf_score: float = 0.5,
    **kwargs,
) -> RankedResult:
    return RankedResult(
        grace_id=grace_id,
        entity_type=entity_type,
        name=name,
        properties=kwargs.get("properties", {"key": "val"}),
        rerank_score=kwargs.get("rerank_score", rrf_score),
        rrf_score=rrf_score,
        contributing_strategies=kwargs.get("contributing_strategies", ["graph"]),
        hop_distance=kwargs.get("hop_distance", None),
    )


def _make_response(
    results: list[RankedResult] | None = None,
    query: str = "test query",
) -> RetrievalResponse:
    return RetrievalResponse(
        query=query,
        results=results or [],
        serialized_context="ctx",
        serialization_format="template",
        total_candidates=len(results) if results else 0,
        strategy_contributions={},
        latency_ms={"total": 50.0},
    )


def _make_target(
    name: str = "grace",
    namespace_type: str = "mother",
    label_prefix: str | None = None,
    ontology_module: str | None = None,
    prefixed_types: list[str] | None = None,
) -> NamespaceTarget:
    return NamespaceTarget(
        name=name,
        namespace_type=namespace_type,
        label_prefix=label_prefix,
        ontology_module=ontology_module,
        prefixed_types=prefixed_types or [],
    )


def _default_config() -> FederationConfig:
    return FederationConfig()


# ---------------------------------------------------------------------------
# resolve_target_namespaces tests (5)
# ---------------------------------------------------------------------------


class _FakeNamespaceRow:
    """Minimal stand-in for GraphNamespaceRow.

    F2-17: carries ``is_ready`` (default True) to mirror the F-49 readiness
    contract — resolve_target_namespaces only routes through READY rows.
    """

    def __init__(self, database_name, namespace_type, label_prefix=None,
                 ontology_module=None, is_ready=True):
        self.database_name = database_name
        self.namespace_type = namespace_type
        self.label_prefix = label_prefix
        self.ontology_module = ontology_module
        self.is_ready = is_ready


class _FakeVersion:
    def __init__(self, schema_modules):
        self.schema_modules = schema_modules


class _FakeScopeEntry:
    def __init__(self, resource_kind, resource_label, action="view", decision="allow"):
        self.resource_kind = resource_kind
        self.resource_label = resource_label
        self.action = action
        self.decision = decision


class _FakeEffectiveScope:
    def __init__(self, allows=None, denies=None):
        self.allows = allows or []
        self.denies = denies or []


def _patch_resolve_deps(db_rows, schema_modules, scope_allows=None):
    """Set up mocks for resolve_target_namespaces dependencies.

    Returns (db_mock, principal_mock, patches) where patches is a list of
    active mock.patch contexts to stop after the test.
    """
    db = MagicMock()
    # F2-17: the F-49 readiness gate chains a SECOND .filter(is_ready) onto
    # the namespace query — make the mocked chain filter-stable so any number
    # of .filter() calls resolves to the same query object.
    query = db.query.return_value
    query.filter.return_value = query
    query.all.return_value = db_rows

    p1 = patch(
        "src.ontology.database.get_active_version",
        return_value=_FakeVersion(schema_modules),
    )
    p2 = patch(
        "src.permissions.principal_context.effective_scope",
        return_value=_FakeEffectiveScope(allows=scope_allows or []),
    )
    p1.start()
    p2.start()

    principal = MagicMock()
    return db, principal, [p1, p2]


def test_resolve_mother_and_children():
    """Happy path: mother + children returned."""
    db, principal, patches = _patch_resolve_deps(
        db_rows=[
            _FakeNamespaceRow("grace", "mother", None, "finance"),
            _FakeNamespaceRow("procore", "child", "Procore", "construction"),
        ],
        schema_modules={
            "finance": {"entity_types": {"Legal_Entity": {}, "Insurance_Policy": {}}},
            "construction": {"entity_types": {"Task": {}, "Worker": {}}},
        },
    )
    try:
        targets = resolve_target_namespaces(db, principal)
        assert len(targets) == 2
        mother = next(t for t in targets if t.namespace_type == "mother")
        child = next(t for t in targets if t.namespace_type == "child")
        assert mother.name == "grace"
        assert child.name == "procore"
    finally:
        for p in patches:
            p.stop()


def test_resolve_empty_children_returns_mother_only():
    """No children registered: mother only returned."""
    db, principal, patches = _patch_resolve_deps(
        db_rows=[_FakeNamespaceRow("grace", "mother", None, "finance")],
        schema_modules={"finance": {"entity_types": {"Legal_Entity": {}}}},
    )
    try:
        targets = resolve_target_namespaces(db, principal)
        assert len(targets) == 1
        assert targets[0].namespace_type == "mother"
    finally:
        for p in patches:
            p.stop()


def test_resolve_prefixed_types_population():
    """Child prefixed_types are label-prefixed; mother types unprefixed."""
    db, principal, patches = _patch_resolve_deps(
        db_rows=[
            _FakeNamespaceRow("grace", "mother", None, "finance"),
            _FakeNamespaceRow("procore", "child", "Procore", "construction"),
        ],
        schema_modules={
            "finance": {"entity_types": {"Legal_Entity": {}}},
            "construction": {"entity_types": {"Task": {}, "Worker": {}}},
        },
    )
    try:
        targets = resolve_target_namespaces(db, principal)
        mother = next(t for t in targets if t.namespace_type == "mother")
        child = next(t for t in targets if t.namespace_type == "child")
        assert "Legal_Entity" in mother.prefixed_types
        assert "Procore_Task" in child.prefixed_types
        assert "Procore_Worker" in child.prefixed_types
    finally:
        for p in patches:
            p.stop()


def test_resolve_principal_scope_intersection():
    """Child excluded when principal's allowed modules don't include it."""
    db, principal, patches = _patch_resolve_deps(
        db_rows=[
            _FakeNamespaceRow("grace", "mother", None, "finance"),
            _FakeNamespaceRow("procore", "child", "Procore", "construction"),
            _FakeNamespaceRow("legal", "child", "Legal", "legal_ops"),
        ],
        schema_modules={
            "finance": {"entity_types": {"Legal_Entity": {}}},
            "construction": {"entity_types": {"Task": {}}},
            "legal_ops": {"entity_types": {"Contract": {}}},
        },
        scope_allows=[_FakeScopeEntry("ontology_module", "construction")],
    )
    try:
        targets = resolve_target_namespaces(db, principal)
        names = [t.name for t in targets]
        assert "grace" in names
        assert "procore" in names
        assert "legal" not in names
    finally:
        for p in patches:
            p.stop()


def test_resolve_mother_always_included():
    """Mother namespace is always included regardless of scope."""
    db, principal, patches = _patch_resolve_deps(
        db_rows=[_FakeNamespaceRow("grace", "mother", None, "finance")],
        schema_modules={"finance": {"entity_types": {"Legal_Entity": {}}}},
        scope_allows=[_FakeScopeEntry("ontology_module", "other")],
    )
    try:
        targets = resolve_target_namespaces(db, principal)
        assert len(targets) == 1
        assert targets[0].namespace_type == "mother"
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# federated_query tests (5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.retrieval.federation_router.resolve_target_namespaces")
async def test_federated_query_fan_out(mock_resolve):
    """Fan-out calls namespace_query_fn for each target."""
    targets = [
        _make_target("grace", "mother"),
        _make_target("child1", "child", "Child1"),
    ]
    mock_resolve.return_value = targets

    call_log = []

    async def mock_fn(query, target):
        call_log.append(target.name)
        return _make_response([_make_ranked_result(grace_id=f"g_{target.name}")])

    result = await federated_query(
        RetrievalQuery(query_text="test"),
        namespace_query_fn=mock_fn,
        db=MagicMock(),
        principal=MagicMock(),
        federation_config=_default_config(),
        routing_config=QueryRoutingConfig(per_namespace_timeout_seconds=5.0),
    )

    assert "grace" in call_log
    assert "child1" in call_log
    assert isinstance(result, FederatedRetrievalResponse)


@pytest.mark.asyncio
@patch("src.retrieval.federation_router.resolve_target_namespaces")
async def test_federated_query_child_timeout_warning(mock_resolve):
    """Child namespace timeout produces a warning, query continues."""
    targets = [
        _make_target("grace", "mother"),
        _make_target("child1", "child", "Child1"),
    ]
    mock_resolve.return_value = targets

    async def mock_fn(query, target):
        if target.name == "child1":
            await asyncio.sleep(10)
        return _make_response([_make_ranked_result()])

    result = await federated_query(
        RetrievalQuery(query_text="test"),
        namespace_query_fn=mock_fn,
        db=MagicMock(),
        principal=MagicMock(),
        federation_config=_default_config(),
        routing_config=QueryRoutingConfig(per_namespace_timeout_seconds=0.01),
    )

    assert len(result.namespace_warnings) == 1
    assert result.namespace_warnings[0].namespace == "child1"


@pytest.mark.asyncio
@patch("src.retrieval.federation_router.resolve_target_namespaces")
async def test_federated_query_mother_timeout_raises(mock_resolve):
    """Mother namespace timeout raises 504."""
    from fastapi import HTTPException

    targets = [_make_target("grace", "mother")]
    mock_resolve.return_value = targets

    async def mock_fn(query, target):
        await asyncio.sleep(10)

    with pytest.raises(HTTPException) as exc_info:
        await federated_query(
            RetrievalQuery(query_text="test"),
            namespace_query_fn=mock_fn,
            db=MagicMock(),
            principal=MagicMock(),
            federation_config=_default_config(),
            routing_config=QueryRoutingConfig(
                per_namespace_timeout_seconds=0.01,
                mother_timeout_posture="fail",
            ),
        )

    assert exc_info.value.status_code == 504


@pytest.mark.asyncio
@patch("src.retrieval.federation_router.resolve_target_namespaces")
async def test_federated_query_mother_degrade_default(mock_resolve):
    """D498: default 'degrade' posture — a mother timeout does NOT raise 504; it
    degrades to a warning and still serves the remaining namespaces."""
    targets = [
        _make_target("grace", "mother"),
        _make_target("child1", "child", "Child1"),
    ]
    mock_resolve.return_value = targets

    async def mock_fn(query, target):
        if target.name == "grace":
            await asyncio.sleep(10)  # mother times out
        return _make_response([_make_ranked_result()])

    result = await federated_query(
        RetrievalQuery(query_text="test"),
        namespace_query_fn=mock_fn,
        db=MagicMock(),
        principal=MagicMock(),
        federation_config=_default_config(),
        routing_config=QueryRoutingConfig(
            per_namespace_timeout_seconds=0.01,
            mother_timeout_posture="degrade",
        ),
    )

    assert any(w.namespace == "grace" for w in result.namespace_warnings)
    assert "child1" in result.source_namespaces


@pytest.mark.asyncio
@patch("src.retrieval.federation_router.resolve_target_namespaces")
async def test_federated_query_mother_unreachable_degrades(mock_resolve):
    """D498: a mother ConnectionError degrades (no 504) under default posture."""
    targets = [_make_target("grace", "mother")]
    mock_resolve.return_value = targets

    async def mock_fn(query, target):
        raise ConnectionError("mother not reachable")

    result = await federated_query(
        RetrievalQuery(query_text="test"),
        namespace_query_fn=mock_fn,
        db=MagicMock(),
        principal=MagicMock(),
        federation_config=_default_config(),
        routing_config=QueryRoutingConfig(mother_timeout_posture="degrade"),
    )

    assert any("Unreachable" in w.reason for w in result.namespace_warnings)
    assert not result.results


@pytest.mark.asyncio
@patch("src.retrieval.federation_router.resolve_target_namespaces")
async def test_circuit_breaker_skips_failed_namespace(mock_resolve):
    """D498: after a namespace fails, the breaker skips it on the next query so
    the timeout is not re-paid — the query fn is not invoked for the open namespace."""
    targets = [_make_target("grace", "mother")]
    mock_resolve.return_value = targets

    call_count = {"n": 0}

    async def mock_fn(query, target):
        call_count["n"] += 1
        raise ConnectionError("down")

    rc = QueryRoutingConfig(
        mother_timeout_posture="degrade",
        circuit_breaker_cooldown_seconds=60.0,
    )
    # First query attempts the mother, fails, trips the breaker.
    await federated_query(
        RetrievalQuery(query_text="q1"),
        namespace_query_fn=mock_fn, db=MagicMock(), principal=MagicMock(),
        federation_config=_default_config(), routing_config=rc,
    )
    assert call_count["n"] == 1
    # Second query: breaker open → namespace skipped, fn NOT called again.
    result2 = await federated_query(
        RetrievalQuery(query_text="q2"),
        namespace_query_fn=mock_fn, db=MagicMock(), principal=MagicMock(),
        federation_config=_default_config(), routing_config=rc,
    )
    assert call_count["n"] == 1  # unchanged — skipped via circuit breaker
    assert any("circuit-open" in w.reason for w in result2.namespace_warnings)


@pytest.mark.asyncio
@patch("src.retrieval.federation_router.resolve_target_namespaces")
async def test_federated_query_child_error_warning(mock_resolve):
    """Child error produces warning, not failure."""
    targets = [
        _make_target("grace", "mother"),
        _make_target("child1", "child", "Child1"),
    ]
    mock_resolve.return_value = targets

    async def mock_fn(query, target):
        if target.name == "child1":
            raise ConnectionError("child down")
        return _make_response([_make_ranked_result()])

    result = await federated_query(
        RetrievalQuery(query_text="test"),
        namespace_query_fn=mock_fn,
        db=MagicMock(),
        principal=MagicMock(),
        federation_config=_default_config(),
        routing_config=QueryRoutingConfig(per_namespace_timeout_seconds=5.0),
    )

    assert len(result.namespace_warnings) == 1
    assert "child down" in result.namespace_warnings[0].reason


@pytest.mark.asyncio
@patch("src.retrieval.federation_router.resolve_target_namespaces")
async def test_federated_query_concurrent_fan_out(mock_resolve):
    """Fan-out is concurrent: wall-clock ~ max, not sum."""
    import time

    targets = [
        _make_target("grace", "mother"),
        _make_target("child1", "child", "Child1"),
        _make_target("child2", "child", "Child2"),
    ]
    mock_resolve.return_value = targets

    async def mock_fn(query, target):
        await asyncio.sleep(0.05)
        return _make_response([_make_ranked_result(grace_id=f"g_{target.name}")])

    start = time.monotonic()
    result = await federated_query(
        RetrievalQuery(query_text="test"),
        namespace_query_fn=mock_fn,
        db=MagicMock(),
        principal=MagicMock(),
        federation_config=_default_config(),
        routing_config=QueryRoutingConfig(per_namespace_timeout_seconds=5.0),
    )
    elapsed = time.monotonic() - start

    # Concurrent: should be ~0.05s, not ~0.15s.
    assert elapsed < 0.3
    assert len(result.source_namespaces) == 3


# ---------------------------------------------------------------------------
# merge_results tests (9)
# ---------------------------------------------------------------------------


def test_merge_post_filter_wrong_prefix_drop():
    """Candidates with wrong prefix are dropped for child namespace."""
    target = _make_target("child1", "child", "Procore")
    results = {
        "child1": _make_response([
            _make_ranked_result(grace_id="g1", entity_type="Procore_Task"),
            _make_ranked_result(grace_id="g2", entity_type="Legal_Entity"),
        ]),
    }

    merged = merge_results(results, [target], _default_config())

    # Only Procore_Task should survive; Legal_Entity dropped.
    assert len(merged.results) == 1
    assert merged.results[0].grace_id == "g1"


def test_merge_post_filter_mother_unprefixed_pass():
    """Mother's unprefixed types pass through."""
    mother = _make_target("grace", "mother")
    results = {
        "grace": _make_response([
            _make_ranked_result(grace_id="g1", entity_type="Legal_Entity"),
        ]),
    }

    merged = merge_results(results, [mother], _default_config())

    assert len(merged.results) == 1
    assert merged.results[0].entity_type == "Legal_Entity"


def test_merge_strip_label_prefix():
    """Child entity types are stripped of label prefix."""
    target = _make_target("child1", "child", "Procore")
    results = {
        "child1": _make_response([
            _make_ranked_result(grace_id="g1", entity_type="Procore_Task"),
        ]),
    }

    merged = merge_results(results, [target], _default_config())

    assert merged.results[0].entity_type == "Task"


def test_merge_filter_properties_for_federation():
    """filter_properties_for_federation applied with layer='domain'."""
    target = _make_target("child1", "child", "Procore")
    results = {
        "child1": _make_response([
            _make_ranked_result(
                grace_id="g1",
                entity_type="Procore_Task",
                properties={"key": "val", "extraction_date": "2024-01-01"},
            ),
        ]),
    }

    config = _default_config()
    merged = merge_results(results, [target], config)

    # domain is a shared layer, so all properties pass through.
    assert "key" in merged.results[0].properties


def test_merge_ranked_to_candidate_field_mapping():
    """_ranked_to_candidate maps fields correctly."""
    result = _make_ranked_result(
        grace_id="g1", entity_type="Task", rrf_score=0.8, hop_distance=2,
    )
    candidate = _ranked_to_candidate(result, "child1", 3)

    assert candidate.grace_id == "g1"
    assert candidate.entity_type == "Task"
    assert candidate.score == 0.8
    assert candidate.strategy == "child1"
    assert candidate.rank == 3
    assert candidate.hop_distance == 2


def test_merge_fused_to_ranked_backward_compat():
    """_fused_to_ranked produces RankedResult shape."""
    fused = FusedCandidate(
        grace_id="g1",
        entity_type="Task",
        name="Test",
        properties={"k": "v"},
        rrf_score=0.7,
        contributing_strategies=["ns1", "ns2"],
        strategy_ranks={"ns1": 1, "ns2": 2},
    )

    ranked = _fused_to_ranked(fused)

    assert isinstance(ranked, RankedResult)
    assert ranked.rerank_score == 0.7
    assert ranked.rrf_score == 0.7
    assert ranked.contributing_strategies == ["ns1", "ns2"]


def test_merge_rrf_fusion_with_source_namespaces():
    """RRF fusion produces result_source_namespaces positionally matched."""
    mother = _make_target("grace", "mother")
    child = _make_target("child1", "child", "Procore")
    results = {
        "grace": _make_response([
            _make_ranked_result(grace_id="g1", entity_type="Legal_Entity"),
        ]),
        "child1": _make_response([
            _make_ranked_result(grace_id="g2", entity_type="Procore_Task"),
        ]),
    }

    merged = merge_results(results, [mother, child], _default_config())

    assert len(merged.result_source_namespaces) == len(merged.results)
    for ns in merged.result_source_namespaces:
        assert ns in ("grace", "child1")


def test_merge_federated_response_structure():
    """FederatedRetrievalResponse has all required fields."""
    target = _make_target("grace", "mother")
    results = {
        "grace": _make_response([_make_ranked_result()]),
    }

    merged = merge_results(results, [target], _default_config())

    assert isinstance(merged, FederatedRetrievalResponse)
    assert isinstance(merged.source_namespaces, list)
    assert isinstance(merged.namespace_warnings, list)
    assert isinstance(merged.result_source_namespaces, list)
    assert isinstance(merged.results, list)
    assert isinstance(merged.strategy_contributions, dict)


def test_merge_single_namespace_fallback():
    """Single-namespace merge produces equivalent results."""
    target = _make_target("grace", "mother")
    r = _make_ranked_result(grace_id="g1", entity_type="Legal_Entity")
    results = {
        "grace": _make_response([r]),
    }

    merged = merge_results(results, [target], _default_config())

    assert len(merged.results) == 1
    assert merged.results[0].grace_id == "g1"
    assert merged.source_namespaces == ["grace"]


# ---------------------------------------------------------------------------
# Adapter tests (3)
# ---------------------------------------------------------------------------


def test_adapter_ranked_to_candidate_score_strategy_rank():
    """Adapter: score, strategy, rank correctly mapped."""
    result = _make_ranked_result(grace_id="g1", rrf_score=0.9)
    c = _ranked_to_candidate(result, "ns1", 5)

    assert c.score == 0.9
    assert c.strategy == "ns1"
    assert c.rank == 5


def test_adapter_fused_to_ranked_shape():
    """Adapter: FusedCandidate -> RankedResult backward-compatible."""
    fused = FusedCandidate(
        grace_id="g1",
        entity_type="Task",
        name="T",
        properties={},
        rrf_score=0.5,
        contributing_strategies=["a"],
        strategy_ranks={"a": 1},
    )

    ranked = _fused_to_ranked(fused)

    assert isinstance(ranked, RankedResult)
    assert ranked.grace_id == "g1"
    assert ranked.entity_type == "Task"


def test_adapter_field_pass_through():
    """Adapter: properties and name pass through unchanged."""
    result = _make_ranked_result(
        grace_id="g1",
        name="MyEntity",
        properties={"complex": {"nested": True}},
    )
    c = _ranked_to_candidate(result, "ns1", 0)

    assert c.name == "MyEntity"
    assert c.properties == {"complex": {"nested": True}}
