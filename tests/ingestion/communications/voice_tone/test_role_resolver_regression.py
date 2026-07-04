"""Regression test for role_resolver ArcadeClient interface drift (F-55).

validation run: role_resolver called `client.query(...)` which does not
exist on ArcadeClient — the real method is async `execute_cypher(...)`.
"""

from __future__ import annotations

import inspect

import pytest

from src.graph.arcade_client import ArcadeClient
from src.ingestion.communications.voice_tone import role_resolver


def test_f55_arcadeclient_has_execute_cypher():
    """ArcadeClient must expose execute_cypher (the method role_resolver uses)."""
    assert hasattr(ArcadeClient, "execute_cypher")


def test_f55_arcadeclient_has_no_query_method():
    """ArcadeClient never had a `.query()` method — guard against re-drift."""
    assert not hasattr(ArcadeClient, "query")


def test_f55_role_resolver_does_not_call_dot_query():
    """role_resolver source must not reference a `.query(` method call."""
    src = inspect.getsource(role_resolver)
    # The actual method-call form `client.query(` must be gone (comments that
    # mention `.query()` for capture-the-why are fine).
    assert "client.query(" not in src
    assert "execute_cypher(" in src


@pytest.mark.asyncio
async def test_f55_execute_cypher_return_shape_parsed():
    """_execute_cypher must call the async execute_cypher and parse `result` rows."""

    class _FakeClient:
        def __init__(self):
            self.calls = []

        async def execute_cypher(self, query, params=None):
            self.calls.append((query, params))
            return {"result": [{"role_name": "Managing Partner"}, {"role_name": ""}]}

    client = _FakeClient()
    roles = await role_resolver._execute_cypher(client, "abc-123")
    assert roles == ["Managing Partner"]
    assert client.calls  # execute_cypher was actually invoked
    assert client.calls[0][1] == {"grace_id": "abc-123"}
