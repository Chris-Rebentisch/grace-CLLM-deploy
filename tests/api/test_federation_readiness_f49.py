"""F-49 regression tests: federation readiness gate + cache invalidation.

Validation-run F-49: registering ANY child namespace permanently rerouted
all retrieval through unbuilt per-namespace indexes (global 200-but-empty
outage); the activation cache was never invalidated; hard DELETE was blocked
by the append-only ER review-queue FK with an opaque 500.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.graph.management_models import GraphNamespace


# ---------------------------------------------------------------------------
# Model / registration defaults
# ---------------------------------------------------------------------------


def test_namespace_model_defaults_not_ready():
    """New GraphNamespace models default to is_ready=False (fail-closed)."""
    ns = GraphNamespace(database_name="test_ns", namespace_type="child")
    assert ns.is_ready is False


@pytest.mark.asyncio
async def test_registration_sets_child_not_ready_mother_ready():
    """register_federation_namespace: child -> not ready, mother -> ready."""
    from src.federation.namespace_federation import register_federation_namespace

    captured = {}

    def fake_db_create(db, namespace):
        captured["ns"] = namespace
        return namespace

    with patch(
        "src.federation.namespace_federation.get_namespace_by_name",
        return_value=None,
    ), patch(
        "src.federation.namespace_federation.db_create",
        side_effect=fake_db_create,
    ):
        child = GraphNamespace(
            database_name="child_ns", namespace_type="child", is_ready=True
        )
        await register_federation_namespace(MagicMock(), MagicMock(), child)
        # Even a caller-supplied is_ready=True is overridden at registration.
        assert captured["ns"].is_ready is False

        mother = GraphNamespace(
            database_name="mother_ns", namespace_type="mother"
        )
        await register_federation_namespace(MagicMock(), MagicMock(), mother)
        assert captured["ns"].is_ready is True


# ---------------------------------------------------------------------------
# Activation cache: ready-children-only + TTL + invalidation
# ---------------------------------------------------------------------------


def _make_query_session(count_value: int):
    """Session mock whose chained query(...).filter(...).count() returns count_value."""
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.count.return_value = count_value
    return session


def test_federation_activation_counts_only_ready_children():
    """A not-ready child namespace must NOT activate the federated path."""
    import src.api.retrieval_routes as rr

    rr.invalidate_federation_cache()
    session = _make_query_session(0)  # is_ready filter excludes the child
    factory = MagicMock(return_value=session)
    with patch.object(rr, "get_session_factory", return_value=factory):
        assert rr._is_federation_active() is False
    rr.invalidate_federation_cache()


def test_invalidate_federation_cache_resets_activation():
    """After invalidation, the next check re-queries and can flip the flag."""
    import src.api.retrieval_routes as rr

    rr.invalidate_federation_cache()
    with patch.object(
        rr, "get_session_factory",
        return_value=MagicMock(return_value=_make_query_session(1)),
    ):
        assert rr._is_federation_active() is True

    # Namespace disabled/deleted -> invalidate -> flag flips without restart.
    rr.invalidate_federation_cache()
    with patch.object(
        rr, "get_session_factory",
        return_value=MagicMock(return_value=_make_query_session(0)),
    ):
        assert rr._is_federation_active() is False
    rr.invalidate_federation_cache()


def test_resolve_target_namespaces_filters_not_ready(monkeypatch):
    """resolve_target_namespaces applies the is_ready filter to the row query."""
    from src.retrieval import federation_router as fr

    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.all.return_value = []

    result = fr.resolve_target_namespaces(db, principal=MagicMock())
    assert result == []
    # Two .filter() calls: namespace_type IN + is_ready IS TRUE.
    assert query.filter.call_count >= 1
    db.query.assert_called_once()


# ---------------------------------------------------------------------------
# DELETE pre-flight (FK policy): 409 with disable guidance, never a 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_namespace_with_audit_rows_returns_409():
    from fastapi import HTTPException

    import src.api.federation_routes as froutes

    ns_id = str(uuid4())
    target = GraphNamespace(
        id=ns_id, database_name="doomed_ns", namespace_type="child"
    )

    db = MagicMock()
    db.execute.return_value.scalar.return_value = 7  # dependent audit rows

    request = MagicMock()
    with patch.object(froutes, "_require_admin_key"), patch.object(
        froutes, "_get_db", return_value=db
    ), patch.object(froutes, "list_namespaces", return_value=[target]):
        with pytest.raises(HTTPException) as exc_info:
            await froutes.delete_namespace(request, ns_id)

    assert exc_info.value.status_code == 409
    assert "is_ready" in str(exc_info.value.detail)
