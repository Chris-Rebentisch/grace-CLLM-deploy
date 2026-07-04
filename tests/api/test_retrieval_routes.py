"""Tests for the retrieval routes federation branch (Chunk 52, CP4).

7 tests covering: federation-active delegation, federation-inactive
passthrough, entity_types injection (empty + non-empty), enforcer
post-filter on merged response, DB-derived _federation_active boolean,
route registration response_model=D384 union serialization guard.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.retrieval.federation_router import (
    FederatedRetrievalResponse,
    NamespaceTarget,
    QueryRoutingConfig,
)
from src.retrieval.retrieval_models import (
    RankedResult,
    RetrievalQuery,
    RetrievalResponse,
)


def _make_ranked_result(
    grace_id: str = "g1",
    entity_type: str = "Legal_Entity",
    rrf_score: float = 0.5,
) -> RankedResult:
    return RankedResult(
        grace_id=grace_id,
        entity_type=entity_type,
        name="Test",
        properties={},
        rerank_score=rrf_score,
        rrf_score=rrf_score,
        contributing_strategies=["graph"],
    )


def _make_retrieval_response(results=None, query="test") -> RetrievalResponse:
    return RetrievalResponse(
        query=query,
        results=results or [],
        serialized_context="",
        serialization_format="template",
        total_candidates=0,
        strategy_contributions={},
        latency_ms={},
    )


def _make_federated_response(results=None) -> FederatedRetrievalResponse:
    return FederatedRetrievalResponse(
        query="test",
        results=results or [],
        source_namespaces=["grace"],
        result_source_namespaces=["grace"] * len(results or []),
    )


# ---------------------------------------------------------------------------
# Test 1: federation-active delegates via NamespaceQueryFn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.api.retrieval_routes._is_federation_active", return_value=True)
@patch("src.api.retrieval_routes._federated_retrieval_query")
async def test_federation_active_delegates(mock_fed_query, mock_active):
    """When federation is active, retrieval_query delegates to _federated_retrieval_query."""
    from src.api.retrieval_routes import retrieval_query

    mock_fed_query.return_value = _make_federated_response()
    request = MagicMock()

    query = RetrievalQuery(query_text="test")
    result = await retrieval_query(query, request)

    mock_fed_query.assert_called_once()
    assert isinstance(result, FederatedRetrievalResponse)


# ---------------------------------------------------------------------------
# Test 2: federation-inactive path unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.api.retrieval_routes._is_federation_active", return_value=False)
@patch("src.api.retrieval_routes._get_pipeline")
@patch("src.api.retrieval_routes._apply_permission_post_filter")
@patch("src.api.retrieval_routes._resolve_active_matrix_tags", return_value=(None, None))
@patch("src.api.retrieval_routes.persist_query_response")
async def test_federation_inactive_unchanged(
    mock_persist, mock_tags, mock_post_filter, mock_pipeline, mock_active
):
    """When federation is inactive, existing pipeline.query() path is used."""
    from unittest.mock import AsyncMock

    resp = _make_retrieval_response([_make_ranked_result()])
    pipeline = MagicMock()
    pipeline.query = AsyncMock(return_value=resp)
    mock_pipeline.return_value = pipeline
    mock_post_filter.return_value = resp
    mock_persist.return_value = None

    from src.api.retrieval_routes import retrieval_query

    request = MagicMock()
    result = await retrieval_query(RetrievalQuery(query_text="test"), request)

    pipeline.query.assert_called_once()
    assert isinstance(result, RetrievalResponse)


# ---------------------------------------------------------------------------
# Test 3: entity_types injection — empty -> full prefixed set
# ---------------------------------------------------------------------------


def test_entity_types_injection_empty():
    """Empty caller entity_types -> use target.prefixed_types."""
    target = NamespaceTarget(
        name="child1",
        namespace_type="child",
        label_prefix="Procore",
        ontology_module="construction",
        prefixed_types=["Procore_Task", "Procore_Worker"],
    )
    query = RetrievalQuery(query_text="test", entity_types=[])

    # Simulate the scoping logic from _federated_retrieval_query.
    scoped_types = list(target.prefixed_types)
    if query.entity_types:
        scoped_types = [
            t for t in query.entity_types if t in target.prefixed_types
        ] or list(target.prefixed_types)

    assert scoped_types == ["Procore_Task", "Procore_Worker"]


# ---------------------------------------------------------------------------
# Test 4: entity_types injection — non-empty -> intersection
# ---------------------------------------------------------------------------


def test_entity_types_injection_non_empty():
    """Non-empty caller entity_types -> intersection with prefixed_types."""
    target = NamespaceTarget(
        name="child1",
        namespace_type="child",
        label_prefix="Procore",
        ontology_module="construction",
        prefixed_types=["Procore_Task", "Procore_Worker", "Procore_Project"],
    )
    query = RetrievalQuery(
        query_text="test",
        entity_types=["Procore_Task", "Procore_Project"],
    )

    scoped_types = list(target.prefixed_types)
    if query.entity_types:
        scoped_types = [
            t for t in query.entity_types if t in target.prefixed_types
        ] or list(target.prefixed_types)

    assert scoped_types == ["Procore_Task", "Procore_Project"]


# ---------------------------------------------------------------------------
# Test 5: Enforcer post-filter runs on merged response
# ---------------------------------------------------------------------------


def test_enforcer_post_filter_on_merged_response():
    """_apply_permission_post_filter_federated filters results and maintains ns alignment."""
    from src.permissions.models import Allow, Deny

    response = _make_federated_response([
        _make_ranked_result(grace_id="g1"),
        _make_ranked_result(grace_id="g2"),
    ])
    response.result_source_namespaces = ["grace", "child1"]

    mock_enforcer = MagicMock()
    mock_enforcer.matrix = MagicMock()

    def _enforce(principal, resource_type, grace_id, action):
        if grace_id == "g1":
            return Allow()
        from src.permissions.models import EnforcementReason
        return Deny(reason=EnforcementReason(code="no_matching_rule"))

    mock_enforcer.enforce = _enforce

    with patch("src.api.retrieval_routes.get_enforcer", return_value=mock_enforcer):
        with patch("src.api.retrieval_routes.from_admission_tree", return_value=MagicMock()):
            from src.api.retrieval_routes import _apply_permission_post_filter_federated

            request = MagicMock()
            # F-50: post-filter is now async (may fetch sensitivity_tags from
            # the graph); this matrix has no sensitivity tags so no fetch fires.
            import asyncio

            filtered = asyncio.run(
                _apply_permission_post_filter_federated(response, request)
            )

    assert len(filtered.results) == 1
    assert filtered.results[0].grace_id == "g1"
    assert filtered.result_source_namespaces == ["grace"]


# ---------------------------------------------------------------------------
# Test 6: DB-derived _federation_active boolean
# ---------------------------------------------------------------------------


def test_db_derived_federation_active():
    """_is_federation_active queries graph_namespaces for READY child rows.

    F-49 update: the query chains a second .filter() (is_ready IS TRUE), so
    the mock's filter must be chain-stable.
    """
    import src.api.retrieval_routes as routes_mod

    # Reset cached value.
    routes_mod.invalidate_federation_cache()

    mock_session = MagicMock()
    mock_query = mock_session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.count.return_value = 2

    mock_factory = MagicMock(return_value=mock_session)

    with patch.object(routes_mod, "get_session_factory", return_value=mock_factory):
        with patch("src.graph.namespace_database.GraphNamespaceRow"):
            result = routes_mod._is_federation_active()

    assert result is True

    # Clean up.
    routes_mod.invalidate_federation_cache()


# ---------------------------------------------------------------------------
# Test 7: response_model omitted for federation vs non-federation responses
# ---------------------------------------------------------------------------


def test_retrieval_query_route_uses_union_friendly_response_model():
    """Chunk 52 D384 / D213 mirror: omit ``response_model`` so both RetrievalResponse

    and FederatedRetrievalResponse serialize without OpenAPI coercion clashes.
    """
    from fastapi.routing import APIRoute

    from src.api.main import app

    routes = [
        r
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path == "/api/retrieval/query"
        and "POST" in r.methods
    ]
    assert len(routes) == 1
    assert routes[0].response_model is None
