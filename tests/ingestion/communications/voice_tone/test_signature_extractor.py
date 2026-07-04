"""Tests for SignatureExtractor (Chunk 58, CP5).

Validates:
1. Signature block detection on fixture emails
2. Regex title extraction for common corporate titles
3. LLM fallback on unconventional signatures
4. Organization domain-match (Lock-R1)
5. Empty organization_domains degrades to external-for-all
6. Internal/external classification
7. Title-to-category mapping
8. RFC "--" delimiter detection
9. Multiple delimiter patterns
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingestion.communications.voice_tone.models import VoiceToneConfig
from src.ingestion.communications.voice_tone.signature_extractor import (
    SignatureExtractor,
    _regex_detect_signature,
)


@pytest.fixture
def config():
    return VoiceToneConfig(
        organization_domains=["acme.com", "acme.co.uk"],
        title_to_category_map={
            "director": "direct_manager",
            "manager": "direct_manager",
            "ceo": "executive_superior",
            "intern": "new_hire_onboarding",
            "counsel": "legal_counsel",
        },
    )


@pytest.fixture
def extractor(config):
    return SignatureExtractor(config)


class TestSignatureDetection:
    """Signature block detection tests."""

    def test_rfc_delimiter(self, extractor):
        """RFC '-- ' delimiter is detected."""
        body = "Hello,\n\nPlease review.\n\n-- \nJohn Smith\nDirector\nAcme Corp"
        sig = extractor.detect_signature(body)
        assert sig is not None
        assert "John Smith" in sig

    def test_underscore_delimiter(self, extractor):
        """Underscore delimiter is detected."""
        body = "Meeting notes attached.\n___\nJane Doe\nSenior Manager"
        sig = extractor.detect_signature(body)
        assert sig is not None
        assert "Jane Doe" in sig

    def test_dash_delimiter(self, extractor):
        """Dash delimiter is detected."""
        body = "Thanks for the update.\n---\nBob Wilson\nCEO"
        sig = extractor.detect_signature(body)
        assert sig is not None
        assert "CEO" in sig

    def test_no_signature(self, extractor):
        """Email without signature returns None."""
        body = "Quick note about the project."
        sig = extractor.detect_signature(body)
        assert sig is None


class TestTitleExtraction:
    """Title extraction tests."""

    def test_director_title(self, extractor):
        """Extracts 'Director' title."""
        title = extractor.extract_title("John Smith\nDirector of Engineering")
        assert title is not None
        assert "director" in title.lower()

    def test_ceo_title(self, extractor):
        """Extracts 'CEO' title."""
        title = extractor.extract_title("Jane Doe\nCEO")
        assert title is not None
        assert "ceo" in title.lower()

    def test_no_title(self, extractor):
        """Returns None when no title found."""
        title = extractor.extract_title("John Smith\n555-1234")
        assert title is None


class TestOrganization:
    """Organization detection and classification tests."""

    def test_domain_match_internal(self, extractor):
        """Internal classification via domain match."""
        result = extractor.classify_internal_external("john@acme.com")
        assert result == "internal"

    def test_external_classification(self, extractor):
        """External classification for non-matching domain."""
        result = extractor.classify_internal_external("john@external.com")
        assert result == "external"

    def test_empty_domains_degrades_to_external(self):
        """Empty organization_domains → external-for-all with confidence_band: low."""
        config = VoiceToneConfig(organization_domains=[])
        ext = SignatureExtractor(config)
        result = ext.classify_internal_external("john@anything.com")
        assert result == "external"

    def test_org_extraction_from_domain(self, extractor):
        """Organization extracted from email domain matching config."""
        org = extractor.extract_organization(
            "John Smith\nDirector", "john@acme.com"
        )
        assert org == "acme.com"


class TestTitleToCategory:
    """Title-to-category mapping tests."""

    def test_director_maps_to_direct_manager(self, extractor):
        """Director maps to direct_manager."""
        cat, band = extractor.title_to_category("Director of Engineering")
        assert cat == "direct_manager"
        assert band == "medium"

    def test_no_match_returns_none(self, extractor):
        """Unknown title returns None with low band."""
        cat, band = extractor.title_to_category("Chief Cat Wrangler")
        assert cat is None
        assert band == "low"


class TestLLMFallback:
    """LLM fallback for unconventional signatures."""

    @pytest.mark.asyncio
    async def test_llm_fallback_success(self, extractor):
        """LLM fallback extracts title from unconventional signature."""
        # D543: production reads .text off the LLMResponse — mock mirrors that shape.
        mock_provider = AsyncMock()
        mock_response = MagicMock()
        mock_response.text = '{"title": "VP of Strategy", "organization": "Acme", "phone": null}'
        mock_provider.generate = AsyncMock(return_value=mock_response)

        with patch(
            "src.shared.llm_provider.get_provider",
            return_value=mock_provider,
        ):
            result = await extractor.extract_with_llm_fallback(
                "Alice B.\nVP of Strategy | Acme | Reshaping the Future"
            )

        assert result["title"] == "VP of Strategy"
        assert result["organization"] == "Acme"
