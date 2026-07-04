"""F-31 regression tests: voice sender resolution falls back to the graph.

Before this fix, voice profiling required an entity_resolution_registry row
keyed canonical_name=<sender email> — a registry only connector/federation
code populates — so voice was silently dead without connectors.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingestion.communications.voice_tone.role_resolver import (
    resolve_sender_person,
)


def _client_returning(rows_by_needle: dict):
    client = MagicMock()

    async def fake_execute(query, params=None):
        return {"result": rows_by_needle.get((params or {}).get("needle"), [])}

    client.execute_cypher = AsyncMock(side_effect=fake_execute)
    return client


@pytest.mark.asyncio
async def test_resolves_by_email_alias():
    client = _client_returning(
        {"diane@whitfield.example": [{"gid": "person-uuid-1"}]}
    )
    with patch(
        "src.graph.arcade_client.get_arcade_client", return_value=client
    ):
        gid = await resolve_sender_person("diane@whitfield.example", "Diane Whitfield")
    assert gid == "person-uuid-1"


@pytest.mark.asyncio
async def test_falls_back_to_display_name():
    client = _client_returning(
        {"Diane Whitfield": [{"gid": "person-uuid-2"}]}
    )
    with patch(
        "src.graph.arcade_client.get_arcade_client", return_value=client
    ):
        gid = await resolve_sender_person("diane@whitfield.example", "Diane Whitfield")
    assert gid == "person-uuid-2"


@pytest.mark.asyncio
async def test_ambiguous_match_returns_none():
    """Never attach a style profile to the wrong person."""
    client = _client_returning(
        {"diane@whitfield.example": [{"gid": "a"}, {"gid": "b"}]}
    )
    with patch(
        "src.graph.arcade_client.get_arcade_client", return_value=client
    ):
        gid = await resolve_sender_person("diane@whitfield.example", None)
    assert gid is None


@pytest.mark.asyncio
async def test_no_match_returns_none():
    client = _client_returning({})
    with patch(
        "src.graph.arcade_client.get_arcade_client", return_value=client
    ):
        gid = await resolve_sender_person("stranger@example.com", "Nobody")
    assert gid is None


def test_generator_uses_graph_fallback_source():
    """profile_generator wires the fallback: registry miss -> resolve_sender_person."""
    import inspect

    from src.ingestion.communications.voice_tone import profile_generator

    src = inspect.getsource(profile_generator)
    assert "resolve_sender_person" in src
    assert "voice_tone_sender_resolved_via_graph" in src
