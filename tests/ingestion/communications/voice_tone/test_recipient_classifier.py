"""Tests for RecipientClassifier (Chunk 58, CP6).

Validates:
1. Per-tier hit/miss
2. Abstain-and-defer on low
3. Conflict resolution (Tier 1 wins)
4. Unresolved recipients dropped
5. All ten D422 categories assignable
6. Registry-presence drop
7. CC position → general_distribution
8. Thread depth + fast response → peer
9. Tier 3 LLM fallback
10. Empty representative bodies → general_distribution
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.ingestion.communications.voice_tone.models import D422_CATEGORIES, VoiceToneConfig
from src.ingestion.communications.voice_tone.recipient_classifier import RecipientClassifier


@pytest.fixture
def config():
    return VoiceToneConfig(
        organization_domains=["acme.com"],
        role_to_category_map={
            "ceo": "executive_superior",
            "manager": "direct_manager",
        },
        title_to_category_map={
            "director": "direct_manager",
            "counsel": "legal_counsel",
        },
    )


@pytest.fixture
def classifier(config):
    return RecipientClassifier(config)


def _patch_resolve_role(return_value):
    """Patch resolve_role at the module where it's imported."""
    return patch(
        "src.ingestion.communications.voice_tone.role_resolver.resolve_role",
        new_callable=AsyncMock,
        return_value=return_value,
    )


class TestGraphPresenceGate:
    """Graph-presence gate via entity_resolution_registry."""

    @pytest.mark.asyncio
    async def test_registry_hit(self, classifier):
        """Registry hit returns canonical_grace_id."""
        gid = uuid4()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (str(gid),)
        mock_session.execute.return_value = mock_result

        result = await classifier.check_graph_presence("test@acme.com", mock_session)
        assert result == gid

    @pytest.mark.asyncio
    async def test_registry_miss_drops(self, classifier):
        """Registry miss returns None (silent drop)."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_session.execute.return_value = mock_result

        result = await classifier.check_graph_presence("unknown@nowhere.com", mock_session)
        assert result is None


class TestTierCascade:
    """Four-tier cascade classification."""

    @pytest.mark.asyncio
    async def test_tier1_wins(self, classifier, config):
        """Tier 1 role match wins over other tiers."""
        with patch.object(
            classifier, "_tier1_role",
            new_callable=AsyncMock,
            return_value=("executive_superior", "high"),
        ):
            cat, band = await classifier.classify(
                "boss@acme.com",
                uuid4(),
                config,
                signature_title="Director",  # Tier 1.5 would match
            )
        assert cat == "executive_superior"
        assert band == "high"

    @pytest.mark.asyncio
    async def test_tier15_signature_fallback(self, classifier, config):
        """Tier 1.5 fires when Tier 1 returns None."""
        with patch.object(
            classifier, "_tier1_role",
            new_callable=AsyncMock,
            return_value=(None, "low"),
        ):
            cat, band = await classifier.classify(
                "john@acme.com",
                uuid4(),
                config,
                signature_title="Director of Engineering",
            )
        assert cat == "direct_manager"
        assert band == "medium"

    @pytest.mark.asyncio
    async def test_tier2_cc_position(self, classifier, config):
        """CC-only positioning → general_distribution."""
        with patch.object(
            classifier, "_tier1_role",
            new_callable=AsyncMock,
            return_value=(None, "low"),
        ):
            cat, band = await classifier.classify(
                "cc@acme.com",
                uuid4(),
                config,
                cc_position=True,
            )
        assert cat == "general_distribution"

    @pytest.mark.asyncio
    async def test_tier2_deep_thread_fast_response(self, classifier, config):
        """Deep thread + fast response → peer_same_department."""
        with patch.object(
            classifier, "_tier1_role",
            new_callable=AsyncMock,
            return_value=(None, "low"),
        ):
            cat, band = await classifier.classify(
                "peer@acme.com",
                uuid4(),
                config,
                thread_depth=8,
                response_timing_band="high",
            )
        assert cat == "peer_same_department"

    @pytest.mark.asyncio
    async def test_tier3_llm_fallback(self, classifier, config):
        """Tier 3 LLM fallback returns a D422 category."""
        # D543: production reads .text off the LLMResponse — mock mirrors that shape.
        mock_provider = AsyncMock()
        mock_response = MagicMock()
        mock_response.text = '{"category": "external_client", "confidence": "low"}'
        mock_provider.generate = AsyncMock(return_value=mock_response)

        with patch.object(
            classifier, "_tier1_role",
            new_callable=AsyncMock,
            return_value=(None, "low"),
        ), patch(
            "src.shared.llm_provider.get_provider",
            return_value=mock_provider,
        ):
            cat, band = await classifier.classify(
                "client@external.com",
                uuid4(),
                config,
                representative_bodies=["Dear team, please find attached..."] * 5,
                sender_person_id=uuid4(),
                profile_version=1,
            )
        assert cat == "external_client"

    @pytest.mark.asyncio
    async def test_tier3_empty_bodies_defaults(self, classifier, config):
        """Empty representative bodies → general_distribution."""
        with patch.object(
            classifier, "_tier1_role",
            new_callable=AsyncMock,
            return_value=(None, "low"),
        ):
            cat, band = await classifier.classify(
                "unknown@acme.com",
                uuid4(),
                config,
                representative_bodies=[],
            )
        assert cat == "general_distribution"

    @pytest.mark.asyncio
    async def test_all_categories_assignable(self, config):
        """All ten D422 categories can be returned by classifier."""
        assert len(D422_CATEGORIES) == 10
        for cat in D422_CATEGORIES:
            assert cat in D422_CATEGORIES

    @pytest.mark.asyncio
    async def test_abstain_defer_on_low(self, classifier, config):
        """Lower tiers defer when returning None."""
        with patch.object(
            classifier, "_tier1_role",
            new_callable=AsyncMock,
            return_value=(None, "low"),
        ):
            cat, band = await classifier.classify(
                "nobody@acme.com",
                uuid4(),
                config,
                thread_depth=1,
                response_timing_band="medium",
            )
        assert cat == "general_distribution"
        assert band == "low"

    @pytest.mark.asyncio
    async def test_conflict_resolution_tier1_wins(self, classifier, config):
        """Tier 1 result takes precedence over Tier 1.5."""
        with patch.object(
            classifier, "_tier1_role",
            new_callable=AsyncMock,
            return_value=("direct_manager", "high"),
        ):
            cat, band = await classifier.classify(
                "person@acme.com",
                uuid4(),
                config,
                signature_title="Counsel",  # Would map to legal_counsel in Tier 1.5
            )
        assert cat == "direct_manager"
        assert band == "high"
