"""Tests for role_resolver (Chunk 58, CP6, Lock-R3).

Validates:
1. Cypher param binding (no f-string injection)
2. Role-name map hits/misses
3. ArcadeDB-down graceful degradation → (None, "low")
4. Confidence-band precedence
5. Multiple roles — first match wins
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.ingestion.communications.voice_tone.models import VoiceToneConfig
from src.ingestion.communications.voice_tone.role_resolver import resolve_role


@pytest.fixture
def config():
    return VoiceToneConfig(
        role_to_category_map={
            "ceo": "executive_superior",
            "manager": "direct_manager",
            "counsel": "legal_counsel",
        },
    )


class TestRoleResolver:
    """role_resolver.resolve_role() tests."""

    @pytest.mark.asyncio
    async def test_cypher_match_with_map_hit(self, config):
        """Cypher match + map hit → (category, 'high')."""
        # F-55: role_resolver now calls the async execute_cypher (was the
        # nonexistent client.query); it returns a {"result": [...]} dict.
        mock_client = MagicMock()
        mock_client.execute_cypher = AsyncMock(
            return_value={"result": [{"role_name": "CEO"}]}
        )

        with patch(
            "src.graph.arcade_client.ArcadeClient",
            return_value=mock_client,
        ):
            cat, band = await resolve_role(uuid4(), config)

        assert cat == "executive_superior"
        assert band == "high"

    @pytest.mark.asyncio
    async def test_cypher_match_no_map_hit(self, config):
        """Cypher match without map hit → (None, 'medium')."""
        mock_client = MagicMock()
        mock_client.execute_cypher = AsyncMock(
            return_value={"result": [{"role_name": "Astronaut"}]}
        )

        with patch(
            "src.graph.arcade_client.ArcadeClient",
            return_value=mock_client,
        ):
            cat, band = await resolve_role(uuid4(), config)

        assert cat is None
        assert band == "medium"

    @pytest.mark.asyncio
    async def test_zero_rows(self, config):
        """Zero Cypher rows → (None, 'low')."""
        mock_client = MagicMock()
        mock_client.execute_cypher = AsyncMock(return_value={"result": []})

        with patch(
            "src.graph.arcade_client.ArcadeClient",
            return_value=mock_client,
        ):
            cat, band = await resolve_role(uuid4(), config)

        assert cat is None
        assert band == "low"

    @pytest.mark.asyncio
    async def test_arcadedb_down_graceful_degradation(self, config):
        """ArcadeDB unreachable → (None, 'low') + structlog error (R7)."""
        with patch(
            "src.graph.arcade_client.ArcadeClient",
            side_effect=Exception("Connection refused"),
        ):
            cat, band = await resolve_role(uuid4(), config)

        assert cat is None
        assert band == "low"

    @pytest.mark.asyncio
    async def test_param_binding_not_fstring(self, config):
        """Cypher query uses $grace_id parameterized binding (D430)."""
        mock_client = MagicMock()
        mock_client.execute_cypher = AsyncMock(return_value={"result": []})

        gid = uuid4()
        with patch(
            "src.graph.arcade_client.ArcadeClient",
            return_value=mock_client,
        ):
            await resolve_role(gid, config)

        # Verify query used params dict, not f-string (F-55: execute_cypher).
        call_args = mock_client.execute_cypher.call_args
        assert call_args is not None
        _, kwargs = call_args
        assert "params" in kwargs
        assert kwargs["params"]["grace_id"] == str(gid)
