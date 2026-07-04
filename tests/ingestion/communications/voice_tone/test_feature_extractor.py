"""Tests for FeatureExtractor (Chunk 58, CP4).

Validates:
1. Per-feature unit tests against fixture emails
2. Batched LLM mock returns expected directness bands
3. F-score computation matches Heylighen-Dewaele expected values
4. MATTR 50-word window
5. Thread-depth degradation for NULL thread_id
6. POS-class word list coverage
7. Greeting/closing detection
8. Hedging frequency
9. Response timing bands
10. HTML fallback
11. Empty body handling
12. Sentence length bands
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.ingestion.communications.voice_tone.feature_extractor import (
    FeatureExtractor,
    _POS_COARSE_MAP,
    _compute_f_score,
    _compute_mattr,
    compute_contrastive_markers,
    compute_function_word_vector,
)
from src.ingestion.communications.voice_tone.models import VoiceToneConfig


@pytest.fixture
def config():
    return VoiceToneConfig()


@pytest.fixture
def extractor(config):
    return FeatureExtractor(config)


@pytest.mark.requires_nltk
class TestFScore:
    """Heylighen-Dewaele F-score computation (POS-tag path needs nltk)."""

    def test_formal_text_high_fscore(self):
        """Formal text with articles/prepositions scores high."""
        words = "the report of the committee regarding the matter before the board".split()
        score = _compute_f_score(words)
        assert score > 55.0, f"Expected high formality, got {score}"

    def test_informal_text_low_fscore(self):
        """Informal text with pronouns/adverbs scores low."""
        words = "hey i just really wanted you to know i totally love it".split()
        score = _compute_f_score(words)
        assert score < 45.0, f"Expected low formality, got {score}"

    def test_empty_words_neutral(self):
        """Empty word list returns neutral 50.0."""
        assert _compute_f_score([]) == 50.0


class TestMATTR:
    """MATTR computation."""

    def test_mattr_50_word_window(self):
        """MATTR with 50-word window returns valid ratio."""
        words = [f"word{i % 25}" for i in range(100)]  # 25 unique across 100
        mattr = _compute_mattr(words, window=50)
        assert 0.0 < mattr < 1.0

    def test_short_text_raw_ttr(self):
        """Texts shorter than window use raw TTR fallback."""
        words = ["hello", "world", "hello"]
        ttr = _compute_mattr(words, window=50)
        assert ttr == pytest.approx(2 / 3, rel=0.01)

    def test_empty_words(self):
        assert _compute_mattr([]) == 0.0


@pytest.mark.requires_nltk
class TestFeatureExtractor:
    """FeatureExtractor per-feature tests (extract() imports nltk at runtime)."""

    def test_sentence_length_band(self, extractor):
        """Short sentences → low band."""
        result = extractor.extract_features(
            body_plain="Hi. Yes. No. OK.",
            body_html=None,
        )
        # Very short sentences
        assert result.sentence_length_band in ("low", "medium")

    def test_greeting_closing_both_present(self, extractor):
        """Both greeting and closing → high band."""
        body = "Hello John,\n\nPlease review the document.\n\nBest regards,"
        result = extractor.extract_features(body_plain=body, body_html=None)
        assert result.greeting_closing_band == "high"

    def test_greeting_only(self, extractor):
        """Greeting without closing → medium band."""
        body = "Hi there,\n\nPlease review the document.\n\nSent from my phone"
        result = extractor.extract_features(body_plain=body, body_html=None)
        assert result.greeting_closing_band == "medium"

    def test_no_greeting_no_closing(self, extractor):
        """No greeting or closing → low band."""
        body = "The quarterly results are attached for your reference."
        result = extractor.extract_features(body_plain=body, body_html=None)
        assert result.greeting_closing_band == "low"

    def test_hedging_frequency(self, extractor):
        """Text with hedging terms → higher hedging band."""
        body = (
            "Perhaps we could consider this. Maybe it might work. "
            "I think possibly this could be somewhat helpful."
        )
        result = extractor.extract_features(body_plain=body, body_html=None)
        assert result.hedging_frequency_band in ("medium", "high")

    def test_thread_depth_degradation_null(self, extractor):
        """Thread depth 1 (NULL thread_id) → low band."""
        result = extractor.extract_features(
            body_plain="Test email body.",
            body_html=None,
            thread_depth=1,
        )
        assert result.thread_depth_band == "low"

    def test_thread_depth_high(self, extractor):
        """Deep thread → high band."""
        result = extractor.extract_features(
            body_plain="Test email body.",
            body_html=None,
            thread_depth=10,
        )
        assert result.thread_depth_band == "high"

    def test_response_timing_fast(self, extractor):
        """Fast response → high timing band."""
        now = datetime.now(tz=timezone.utc)
        prior = now - timedelta(minutes=30)
        result = extractor.extract_features(
            body_plain="Quick reply.",
            body_html=None,
            sent_at=now,
            thread_sent_ats=[prior],
        )
        assert result.response_timing_band == "high"

    def test_response_timing_slow(self, extractor):
        """Slow response → low timing band."""
        now = datetime.now(tz=timezone.utc)
        prior = now - timedelta(hours=48)
        result = extractor.extract_features(
            body_plain="Delayed reply.",
            body_html=None,
            sent_at=now,
            thread_sent_ats=[prior],
        )
        assert result.response_timing_band == "low"

    def test_html_fallback(self, extractor):
        """HTML body is stripped when plain is empty."""
        result = extractor.extract_features(
            body_plain=None,
            body_html="<p>Hello world, this is a test email.</p>",
        )
        assert result.sentence_length_band is not None

    def test_empty_body(self, extractor):
        """Empty body produces valid default bands."""
        result = extractor.extract_features(
            body_plain="",
            body_html=None,
        )
        assert result.sentence_length_band == "medium"

    @pytest.mark.asyncio
    async def test_directness_batch_mock(self, extractor):
        """Batched LLM directness returns expected bands."""
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(
            return_value='[{"index": 0, "band": "high"}, {"index": 1, "band": "low"}]'
        )

        with patch(
            "src.shared.llm_provider.get_provider",
            return_value=mock_provider,
        ):
            bands = await extractor.extract_directness_batch(
                ["Direct email", "Vague email"]
            )

        assert bands == ["high", "low"]


# ---------------------------------------------------------------------------
# CP1 tests — NLTK POS F-score + function-word vector (Chunk 78, D507/D504)
# ---------------------------------------------------------------------------


@pytest.mark.requires_nltk
class TestNltkFScore:
    """NLTK POS-based Heylighen-Dewaele F-score (D507)."""

    def test_f_score_range(self):
        """Tagged corpus yields F in [0, 100]."""
        words = "The quarterly report of the committee was submitted for review".split()
        score = _compute_f_score(words)
        assert 0.0 <= score <= 100.0, f"F-score {score} out of range"

    def test_f_score_formal_text(self):
        """Formal text (high noun/adj density) produces F > 50."""
        words = (
            "The comprehensive assessment of the organizational structure "
            "regarding the institutional framework within the regulatory "
            "environment of the financial sector"
        ).split()
        score = _compute_f_score(words)
        assert score > 50.0, f"Expected formal F > 50, got {score}"

    def test_f_score_informal_text(self):
        """Informal text (high pronoun/verb density) produces F < 50."""
        words = (
            "hey i just really wanted to tell you that we should totally "
            "go there and they would probably like it too"
        ).split()
        score = _compute_f_score(words)
        assert score < 50.0, f"Expected informal F < 50, got {score}"

    def test_pos_coarse_map_coverage(self):
        """All key Penn-Treebank tags map to one of the 8 coarse classes."""
        expected_tags = {
            "NN", "NNS", "NNP", "NNPS",  # noun
            "JJ", "JJR", "JJS",  # adjective
            "IN",  # preposition
            "DT",  # article
            "PRP", "PRP$", "WP", "WP$",  # pronoun
            "VB", "VBD", "VBG", "VBN", "VBP", "VBZ",  # verb
            "RB", "RBR", "RBS",  # adverb
            "UH",  # interjection
        }
        for tag in expected_tags:
            assert tag in _POS_COARSE_MAP, f"Penn-Treebank tag {tag} missing from coarse map"
        # All coarse values are valid
        valid_classes = {"noun", "adjective", "preposition", "article",
                         "pronoun", "verb", "adverb", "interjection"}
        for tag, cls in _POS_COARSE_MAP.items():
            assert cls in valid_classes, f"Tag {tag} maps to invalid class {cls}"


class TestFunctionWordVector:
    """Function-word relative-frequency fingerprint (D504)."""

    def test_function_word_vector_deterministic(self):
        """Same input → same frequency vector."""
        words = "the report is a summary of the key findings in the data".split()
        v1 = compute_function_word_vector(words)
        v2 = compute_function_word_vector(words)
        assert v1 == v2

    def test_function_word_vector_frequencies_sum_to_one(self):
        """Vector values sum to ~1.0."""
        words = "the report is a summary of the key findings in the data from our team".split()
        vec = compute_function_word_vector(words)
        if vec:
            total = sum(vec.values())
            assert abs(total - 1.0) < 0.01, f"Function-word freqs sum to {total}, expected ~1.0"

    def test_contrastive_markers_nonempty(self):
        """Sender vector diffed against distinct baseline yields non-empty markers."""
        # Sender who overuses "i" and "we" relative to English baseline
        sender_words = (
            "i think we should i believe we can i want we need "
            "i suggest we consider i hope we agree"
        ).split()
        sender_vec = compute_function_word_vector(sender_words)
        markers = compute_contrastive_markers(sender_vec)
        assert len(markers) > 0, "Expected non-empty contrastive markers"

    def test_existing_mattr_unchanged(self):
        """Existing MATTR computation still works correctly."""
        words = [f"word{i % 25}" for i in range(100)]
        mattr = _compute_mattr(words, window=50)
        assert 0.0 < mattr < 1.0
