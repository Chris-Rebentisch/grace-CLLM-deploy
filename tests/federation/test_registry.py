"""Tests for the canonical entity resolution registry (Chunk 51 CP6, D404)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.federation.models import CanonicalEntity
from src.federation.registry import CanonicalEntityRegistry


@pytest.fixture()
def mock_session():
    return MagicMock()


@pytest.fixture()
def registry(mock_session):
    return CanonicalEntityRegistry(
        session=mock_session,
        ollama_base_url="http://localhost:11434",
        embedding_model="nomic-embed-text",
        similarity_threshold=0.85,
    )


def _make_entity(**overrides) -> CanonicalEntity:
    defaults = {
        "canonical_grace_id": uuid4(),
        "canonical_name": "Acme Corp",
        "canonical_type": "Legal_Entity",
    }
    defaults.update(overrides)
    return CanonicalEntity(**defaults)


class TestRegisterCanonical:

    @pytest.mark.asyncio
    async def test_register_computes_embedding(self, registry, mock_session):
        entity = _make_entity()

        with patch(
            "src.federation.registry.embed_texts",
            new_callable=AsyncMock,
            return_value=[[0.1, 0.2, 0.3]],
        ):
            result = await registry.register_canonical(entity)

        assert result.embedding_vector == [0.1, 0.2, 0.3]
        assert result.id is not None
        assert result.created_at is not None

    @pytest.mark.asyncio
    async def test_register_calls_embed_texts_with_base_url(self, registry):
        entity = _make_entity()

        with patch(
            "src.federation.registry.embed_texts",
            new_callable=AsyncMock,
            return_value=[[0.1]],
        ) as mock_embed:
            await registry.register_canonical(entity)

        mock_embed.assert_called_once_with(
            ["Acme Corp"],
            base_url="http://localhost:11434",
            model="nomic-embed-text",
        )


class TestResolve:

    @pytest.mark.asyncio
    async def test_exact_match_returns_entity(self, registry, mock_session):
        gid = uuid4()
        mock_session.execute.return_value.mappings.return_value.first.return_value = {
            "id": uuid4(),
            "canonical_grace_id": gid,
            "canonical_name": "Acme Corp",
            "canonical_type": "Legal_Entity",
            "aliases": {},
            "embedding_vector": None,
            "namespace_source": None,
            "created_at": None,
            "updated_at": None,
        }

        entity, method = await registry.resolve("Acme Corp", "Legal_Entity")
        assert method == "exact"
        assert entity is not None
        assert entity.canonical_name == "Acme Corp"

    @pytest.mark.asyncio
    async def test_no_match_returns_unresolved(self, registry, mock_session):
        mock_session.execute.return_value.mappings.return_value.first.return_value = None
        mock_session.execute.return_value.mappings.return_value.all.return_value = []

        with patch(
            "src.federation.registry.embed_texts",
            new_callable=AsyncMock,
            return_value=[[0.1, 0.2]],
        ):
            entity, method = await registry.resolve("Unknown", "Legal_Entity")

        assert entity is None
        assert method == "unresolved"

    @pytest.mark.asyncio
    async def test_embedding_tier_returns_entity_above_threshold(self, registry, mock_session):
        """Tier 2: embedding similarity above threshold."""
        gid = uuid4()
        # First call (exact match) returns None.
        # Second call (candidates) returns a match.
        mock_session.execute.return_value.mappings.return_value.first.return_value = None
        mock_session.execute.return_value.mappings.return_value.all.return_value = [
            {
                "id": uuid4(),
                "canonical_grace_id": gid,
                "canonical_name": "Acme Corporation",
                "canonical_type": "Legal_Entity",
                "aliases": {},
                "embedding_vector": [0.9, 0.1, 0.0],
                "namespace_source": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

        with patch(
            "src.federation.registry.embed_texts",
            new_callable=AsyncMock,
            return_value=[[0.9, 0.1, 0.0]],  # identical = similarity 1.0
        ):
            entity, method = await registry.resolve("Acme Corp", "Legal_Entity")

        assert method == "embedding"
        assert entity is not None
        assert entity.canonical_name == "Acme Corporation"

    @pytest.mark.asyncio
    async def test_embedding_tier_below_threshold_returns_unresolved(self, registry, mock_session):
        """Tier 2: embedding similarity below threshold returns unresolved."""
        mock_session.execute.return_value.mappings.return_value.first.return_value = None
        mock_session.execute.return_value.mappings.return_value.all.return_value = [
            {
                "id": uuid4(),
                "canonical_grace_id": uuid4(),
                "canonical_name": "Totally Different",
                "canonical_type": "Legal_Entity",
                "aliases": {},
                "embedding_vector": [0.0, 0.0, 1.0],
                "namespace_source": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

        with patch(
            "src.federation.registry.embed_texts",
            new_callable=AsyncMock,
            return_value=[[1.0, 0.0, 0.0]],  # orthogonal = similarity 0.0
        ):
            entity, method = await registry.resolve("Something", "Legal_Entity")

        assert entity is None
        assert method == "unresolved"


class TestListCanonicals:

    @pytest.mark.asyncio
    async def test_list_returns_entities(self, registry, mock_session):
        gid = uuid4()
        mock_session.execute.return_value.mappings.return_value.all.return_value = [
            {
                "id": uuid4(),
                "canonical_grace_id": gid,
                "canonical_name": "Acme",
                "canonical_type": "Legal_Entity",
                "aliases": {},
                "embedding_vector": None,
                "namespace_source": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

        results = await registry.list_canonicals()
        assert len(results) == 1
        assert results[0].canonical_name == "Acme"

    @pytest.mark.asyncio
    async def test_list_with_type_filter(self, registry, mock_session):
        mock_session.execute.return_value.mappings.return_value.all.return_value = []

        results = await registry.list_canonicals(type_filter="Legal_Entity")
        assert results == []
        # Verify the execute was called (filter was applied).
        mock_session.execute.assert_called()
        call_args = mock_session.execute.call_args
        # The params dict is the second positional arg.
        params = call_args[0][1] if len(call_args[0]) > 1 else {}
        assert params.get("type") == "Legal_Entity"


class TestGetByGraceId:

    @pytest.mark.asyncio
    async def test_get_found(self, registry, mock_session):
        gid = uuid4()
        mock_session.execute.return_value.mappings.return_value.first.return_value = {
            "id": uuid4(),
            "canonical_grace_id": gid,
            "canonical_name": "Acme",
            "canonical_type": "Legal_Entity",
            "aliases": {},
            "embedding_vector": None,
            "namespace_source": None,
            "created_at": None,
            "updated_at": None,
        }

        result = await registry.get_by_grace_id(gid)
        assert result is not None
        assert result.canonical_grace_id == gid

    @pytest.mark.asyncio
    async def test_get_not_found(self, registry, mock_session):
        mock_session.execute.return_value.mappings.return_value.first.return_value = None

        result = await registry.get_by_grace_id(uuid4())
        assert result is None


class TestConstructor:

    def test_constructor_accepts_ollama_config(self):
        session = MagicMock()
        reg = CanonicalEntityRegistry(
            session=session,
            ollama_base_url="http://custom:11434",
            embedding_model="custom-model",
            similarity_threshold=0.9,
        )
        assert reg._ollama_base_url == "http://custom:11434"
        assert reg._embedding_model == "custom-model"
        assert reg._similarity_threshold == 0.9

    def test_constructor_default_model(self):
        session = MagicMock()
        reg = CanonicalEntityRegistry(
            session=session,
            ollama_base_url="http://localhost:11434",
        )
        assert reg._embedding_model == "nomic-embed-text"
        assert reg._similarity_threshold == 0.85
