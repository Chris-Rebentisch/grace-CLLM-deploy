"""F-51 regression: the enforcer must hydrate its active matrix from the DB
at boot, not only inside the in-process ratify route.

Before the fix, a restarted uvicorn / the MCP server ran with ``matrix=None``
(``no_active_matrix``): retrieval sensitivity enforcement silently reverted to
no-matrix behavior after a restart, and writable MCP tools were permanently
denied despite a ratified matrix in ``permission_matrices``.

These tests use a stubbed session factory so no live DB is required.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.permissions.enforcer import (
    get_enforcer,
    hydrate_enforcer_from_db,
    rebuild_enforcer,
)
from src.permissions.models import PermissionMatrix


@pytest.fixture(autouse=True)
def _reset_enforcer():
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)


def _stub_factory(active_row):
    """Return a session-factory callable whose sessions are context managers.

    ``hydrate_enforcer_from_db`` calls ``repository.get_active_matrix(session)``;
    we monkeypatch that to return ``active_row`` regardless of the session.
    """

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def factory():
        return _Session()

    return factory


def test_hydrate_loads_matrix_from_db(monkeypatch):
    matrix = PermissionMatrix(role_clusters=[], default_decision="deny")
    monkeypatch.setattr(
        "src.permissions.repository.get_active_matrix",
        lambda _session: {"payload": matrix.model_dump()},
    )
    assert get_enforcer().matrix is None  # fresh process

    loaded = hydrate_enforcer_from_db(session_factory=_stub_factory(None))

    assert loaded is True
    active = get_enforcer().matrix
    assert active is not None
    assert active.default_decision == "deny"


def test_hydrate_no_matrix_leaves_dormant(monkeypatch):
    monkeypatch.setattr(
        "src.permissions.repository.get_active_matrix",
        lambda _session: None,
    )
    loaded = hydrate_enforcer_from_db(session_factory=_stub_factory(None))
    assert loaded is False
    assert get_enforcer().matrix is None


def test_hydrate_swallows_db_error(monkeypatch):
    def _boom(_session):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "src.permissions.repository.get_active_matrix", _boom
    )
    # Boot must not crash on a permissions-store error.
    loaded = hydrate_enforcer_from_db(session_factory=_stub_factory(None))
    assert loaded is False
    assert get_enforcer().matrix is None
