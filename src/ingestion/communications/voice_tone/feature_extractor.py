"""Feature extractor for Voice & Tone Profiling (Chunk 58→78, CP4→CP1).

Single ``pysbd`` tokenization pass per email. Deterministic sub-methods per
feature. Batched LLM directness via ``src/shared/llm_provider.get_provider()``.

D507 capture-the-why: F-score replaced from word-list proxy to proper NLTK POS-
based Heylighen-Dewaele implementation. The word-list approach was unreliable —
it conflated lexical frequency with POS class, missing context-dependent POS
assignments. NLTK ``pos_tag`` gives Penn-Treebank tags that map cleanly to the
8 coarse classes in the original paper. Authorization: D507 (Chunk 78 spec §4).

D504 capture-the-why: Function-word relative-frequency fingerprint added for
contrastive marker computation (Burrows-style). Authorization: D504 (Chunk 78).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

import structlog

from src.ingestion.communications.voice_tone.models import Band, FeatureResult, VoiceToneConfig

if TYPE_CHECKING:
    from datetime import datetime

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# NLTK POS-based Heylighen-Dewaele F-score (D507, Chunk 78 CP1)
# ---------------------------------------------------------------------------

# Penn-Treebank → 8 coarse classes (Heylighen & Dewaele 1999)
_POS_COARSE_MAP: dict[str, str] = {}

# Nouns: NN, NNS, NNP, NNPS
for _tag in ("NN", "NNS", "NNP", "NNPS"):
    _POS_COARSE_MAP[_tag] = "noun"

# Adjectives: JJ, JJR, JJS
for _tag in ("JJ", "JJR", "JJS"):
    _POS_COARSE_MAP[_tag] = "adjective"

# Prepositions: IN
_POS_COARSE_MAP["IN"] = "preposition"

# Articles: DT
_POS_COARSE_MAP["DT"] = "article"

# Pronouns: PRP, PRP$, WP, WP$
for _tag in ("PRP", "PRP$", "WP", "WP$"):
    _POS_COARSE_MAP[_tag] = "pronoun"

# Verbs: VB, VBD, VBG, VBN, VBP, VBZ
for _tag in ("VB", "VBD", "VBG", "VBN", "VBP", "VBZ"):
    _POS_COARSE_MAP[_tag] = "verb"

# Adverbs: RB, RBR, RBS
for _tag in ("RB", "RBR", "RBS"):
    _POS_COARSE_MAP[_tag] = "adverb"

# Interjections: UH
_POS_COARSE_MAP["UH"] = "interjection"

# Formal set (f) and contextual set (c) per Heylighen & Dewaele
_FORMAL_CLASSES = frozenset({"noun", "adjective", "preposition", "article"})
_CONTEXTUAL_CLASSES = frozenset({"pronoun", "verb", "adverb", "interjection"})


def _compute_f_score(words: list[str]) -> float:
    """Heylighen-Dewaele F-score using NLTK POS tagging (D507).

    F = 50 * ((n_f - n_c) / N + 1)
    Range: 0..100 (higher = more formal).
    Corpus-aggregated — never per-sentence (D507 §Don't #13).
    """
    if not words:
        return 50.0

    import nltk

    tagged = nltk.pos_tag(words)
    n_f = 0
    n_c = 0
    for _word, tag in tagged:
        coarse = _POS_COARSE_MAP.get(tag)
        if coarse in _FORMAL_CLASSES:
            n_f += 1
        elif coarse in _CONTEXTUAL_CLASSES:
            n_c += 1

    n = len(tagged)
    if n == 0:
        return 50.0

    return 50.0 * ((n_f - n_c) / n + 1.0)


# ---------------------------------------------------------------------------
# Function-word relative-frequency fingerprint (D504, Chunk 78 CP1)
# ---------------------------------------------------------------------------

# ~150 English closed-class function words (Burrows-style)
_FUNCTION_WORDS: tuple[str, ...] = (
    "a", "about", "above", "after", "again", "against", "all", "also", "am",
    "an", "and", "another", "any", "are", "as", "at", "be", "because", "been",
    "before", "being", "between", "both", "but", "by", "can", "could", "did",
    "do", "does", "doing", "down", "during", "each", "either", "else", "even",
    "every", "few", "for", "from", "further", "get", "got", "had", "has",
    "have", "having", "he", "her", "here", "hers", "herself", "him", "himself",
    "his", "how", "however", "i", "if", "in", "into", "is", "it", "its",
    "itself", "just", "let", "like", "may", "me", "might", "more", "most",
    "much", "must", "my", "myself", "neither", "no", "nor", "not", "now",
    "of", "off", "on", "once", "only", "or", "other", "our", "ours",
    "ourselves", "out", "over", "own", "per", "quite", "rather", "re", "same",
    "shall", "she", "should", "since", "so", "some", "still", "such", "than",
    "that", "the", "their", "theirs", "them", "themselves", "then", "there",
    "therefore", "these", "they", "this", "those", "though", "through", "thus",
    "to", "too", "under", "until", "up", "upon", "us", "very", "was", "we",
    "well", "were", "what", "whatever", "when", "where", "whether", "which",
    "while", "who", "whom", "whose", "why", "will", "with", "within",
    "without", "would", "yet", "you", "your", "yours", "yourself", "yourselves",
)
_FUNCTION_WORD_SET: frozenset[str] = frozenset(_FUNCTION_WORDS)


def compute_function_word_vector(words: list[str]) -> dict[str, float]:
    """Compute relative frequencies for closed-class function words (D504).

    Returns a dict mapping function word → relative frequency (sums to ~1.0
    over the function-word subset only). Words not in the text get 0 and are
    omitted from the result for sparsity.
    """
    if not words:
        return {}
    lower_words = [w.lower() for w in words]
    fw_counts: dict[str, int] = {}
    total_fw = 0
    for w in lower_words:
        if w in _FUNCTION_WORD_SET:
            fw_counts[w] = fw_counts.get(w, 0) + 1
            total_fw += 1
    if total_fw == 0:
        return {}
    return {w: c / total_fw for w, c in fw_counts.items()}


# Bundled English baseline frequencies (fallback for cold-start, D504)
_BUNDLED_ENGLISH_BASELINE: dict[str, float] = {
    "the": 0.148, "of": 0.065, "and": 0.060, "to": 0.058, "a": 0.046,
    "in": 0.042, "is": 0.027, "that": 0.025, "it": 0.023, "for": 0.021,
    "was": 0.019, "i": 0.018, "on": 0.017, "as": 0.016, "with": 0.015,
    "he": 0.014, "be": 0.013, "at": 0.012, "by": 0.011, "this": 0.010,
    "have": 0.009, "not": 0.009, "are": 0.008, "or": 0.008, "his": 0.007,
    "from": 0.007, "but": 0.006, "they": 0.006, "which": 0.005, "she": 0.005,
    "we": 0.005, "her": 0.004, "an": 0.004, "my": 0.004, "been": 0.004,
    "has": 0.003, "their": 0.003, "were": 0.003, "me": 0.003, "do": 0.003,
    "will": 0.003, "would": 0.003, "who": 0.003, "if": 0.003, "more": 0.003,
}


def compute_contrastive_markers(
    sender_vector: dict[str, float],
    baseline_vector: dict[str, float] | None = None,
) -> list[str]:
    """Diff sender function-word vector against baseline, return distinctive markers (D504).

    A marker is distinctive when its z-score-like deviation exceeds 1.5×
    (sender frequency / baseline frequency ratio). Returns word tokens sorted
    by absolute deviation, descending.
    """
    if not sender_vector:
        return []
    baseline = baseline_vector or _BUNDLED_ENGLISH_BASELINE

    markers: list[tuple[str, float]] = []
    for word, freq in sender_vector.items():
        base_freq = baseline.get(word, 0.001)  # small epsilon for missing
        ratio = freq / base_freq
        if ratio > 1.5 or ratio < 0.67:
            markers.append((word, abs(ratio - 1.0)))

    markers.sort(key=lambda x: x[1], reverse=True)
    return [w for w, _ in markers[:20]]  # top-20 distinctive markers


def _compute_mattr(words: list[str], window: int = 50) -> float:
    """Moving-Average Type-Token Ratio with sliding window.

    Falls back to raw TTR for texts shorter than the window (spec §3.1).
    """
    if not words:
        return 0.0
    if len(words) < window:
        return len(set(w.lower() for w in words)) / len(words)

    ratios: list[float] = []
    for i in range(len(words) - window + 1):
        chunk = words[i : i + window]
        unique = len(set(w.lower() for w in chunk))
        ratios.append(unique / window)
    return sum(ratios) / len(ratios)


def _to_band(value: float, high_threshold: float, low_threshold: float) -> Band:
    """Map a numeric value to a band using thresholds."""
    if value >= high_threshold:
        return "high"
    if value <= low_threshold:
        return "low"
    return "medium"


# ---------------------------------------------------------------------------
# Greeting/closing regex patterns
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|dear|good\s+(morning|afternoon|evening)|greetings)\b",
    re.IGNORECASE,
)

_CLOSING_RE = re.compile(
    r"(best\s+regards|sincerely|regards|cheers|thanks|thank\s+you|kind\s+regards"
    r"|warm\s+regards|yours\s+truly|respectfully)\s*[,.]?\s*$",
    re.IGNORECASE,
)


class FeatureExtractor:
    """Eight-feature style extraction per email.

    Single ``pysbd`` tokenization pass. Features:
    1. Sentence length (mean word count per sentence)
    2. Vocabulary complexity (MATTR 50-word window)
    3. Formality (Heylighen-Dewaele F-score, POS-class lists)
    4. Greeting/closing patterns
    5. Hedging frequency
    6. Directness (batched LLM)
    7. Response timing
    8. Thread participation depth
    """

    def __init__(self, config: VoiceToneConfig) -> None:
        self.config = config
        self._hedging_patterns = [
            re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
            for term in config.hedging_lexicon
        ]

    def _tokenize(self, body: str) -> list[str]:
        """Tokenize body text into sentences using pysbd."""
        import pysbd

        segmenter = pysbd.Segmenter(language="en", clean=False)
        return segmenter.segment(body)

    def _get_body_text(
        self, body_plain: str | None, body_html: str | None
    ) -> str:
        """Get plain text body, falling back to stripped HTML."""
        if body_plain and body_plain.strip():
            return body_plain
        if body_html:
            # Strip HTML tags as fallback
            return re.sub(r"<[^>]+>", " ", body_html).strip()
        return ""

    def _get_words(self, body: str) -> list[str]:
        """Split body into words."""
        return [w for w in re.findall(r"\b\w+\b", body) if w]

    def _sentence_length_band(self, sentences: list[str]) -> Band:
        """Mean word count per sentence → band."""
        if not sentences:
            return "medium"
        word_counts = [len(s.split()) for s in sentences]
        mean_len = sum(word_counts) / len(word_counts)
        # Short sentences = direct/informal, long = formal/complex
        return _to_band(mean_len, 20.0, 10.0)

    def _vocabulary_complexity_band(self, words: list[str]) -> Band:
        """MATTR with 50-word sliding window → band."""
        mattr = _compute_mattr(words, window=50)
        thresholds = self.config.vocabulary_complexity_thresholds
        return _to_band(mattr, thresholds.get("high", 0.8), thresholds.get("low", 0.5))

    def _formality_band(self, words: list[str]) -> Band:
        """Heylighen-Dewaele F-score → band."""
        f_score = _compute_f_score(words)
        thresholds = self.config.formality_thresholds
        return _to_band(f_score, thresholds.get("high", 60.0), thresholds.get("low", 40.0))

    def _greeting_closing_band(self, body: str) -> Band:
        """Regex-based greeting/closing classification → band."""
        lines = body.strip().split("\n")
        if not lines:
            return "low"

        has_greeting = bool(_GREETING_RE.match(lines[0]))
        has_closing = bool(_CLOSING_RE.search(lines[-1]))

        if has_greeting and has_closing:
            return "high"
        if has_greeting or has_closing:
            return "medium"
        return "low"

    def _hedging_frequency_band(self, sentences: list[str], body: str) -> Band:
        """Count of hedging_lexicon matches / sentence count → band."""
        if not sentences:
            return "low"
        count = sum(
            len(pat.findall(body)) for pat in self._hedging_patterns
        )
        ratio = count / len(sentences)
        return _to_band(ratio, 0.5, 0.1)

    def _response_timing_band(
        self,
        sent_at: "datetime | None",
        thread_sent_ats: list["datetime"] | None,
    ) -> Band:
        """Response timing from sent_at deltas across thread → band."""
        if not sent_at or not thread_sent_ats:
            return "medium"

        # Find the most recent prior message in thread
        prior_times = [t for t in thread_sent_ats if t < sent_at]
        if not prior_times:
            return "medium"

        latest_prior = max(prior_times)
        delta_hours = (sent_at - latest_prior).total_seconds() / 3600.0

        if delta_hours <= 1.0:
            return "high"  # fast responder
        if delta_hours >= 24.0:
            return "low"  # slow responder
        return "medium"

    def _thread_depth_band(self, thread_depth: int) -> Band:
        """Thread participation depth → band.

        SN-2: for NULL thread_id (IMAP/archive sources), depth degrades to 1.
        """
        if thread_depth >= 5:
            return "high"
        if thread_depth <= 1:
            return "low"
        return "medium"

    async def extract_directness_batch(
        self, bodies: list[str]
    ) -> list[Band]:
        """Batched LLM directness classification.

        Uses ``src/shared/llm_provider.get_provider()`` with ``json_mode=True``.
        Results are bands — accept per-model variance (R5).
        """
        from src.shared.llm_provider import get_provider

        if not bodies:
            return []

        provider = get_provider()
        results: list[Band] = []
        batch_size = self.config.directness_batch_size

        for i in range(0, len(bodies), batch_size):
            batch = bodies[i : i + batch_size]
            prompt = (
                "Classify each of the following email excerpts as having "
                "'high', 'medium', or 'low' directness. "
                "Return a JSON array of objects with 'index' and 'band' keys.\n\n"
            )
            for idx, body in enumerate(batch):
                excerpt = body[:500]  # Truncate for LLM context
                prompt += f"Email {idx}: {excerpt}\n\n"

            try:
                # D543: provider interface is generate(system_prompt, user_prompt) -> LLMResponse.
                response = await provider.generate(
                    system_prompt="",
                    user_prompt=prompt,
                    json_mode=True,
                )
                # Parse LLM response
                import json
                data = json.loads(response.text)
                if isinstance(data, list):
                    for item in data:
                        band = item.get("band", "medium")
                        if band in ("high", "medium", "low"):
                            results.append(band)
                        else:
                            results.append("medium")
                else:
                    results.extend(["medium"] * len(batch))
            except Exception:
                logger.warning("voice_tone_directness_llm_fallback", batch_start=i)
                results.extend(["medium"] * len(batch))

        return results[:len(bodies)]

    def extract_features(
        self,
        body_plain: str | None,
        body_html: str | None,
        sent_at: "datetime | None" = None,
        thread_sent_ats: list["datetime"] | None = None,
        thread_depth: int = 1,
        directness_band: Band = "medium",
    ) -> FeatureResult:
        """Extract eight features from a single email.

        ``directness_band`` is pre-computed via ``extract_directness_batch``.
        ``thread_depth`` SN-2: 1 for NULL thread_id.
        """
        body = self._get_body_text(body_plain, body_html)
        sentences = self._tokenize(body) if body else []
        words = self._get_words(body)

        return FeatureResult(
            sentence_length_band=self._sentence_length_band(sentences),
            vocabulary_complexity_band=self._vocabulary_complexity_band(words),
            formality_band=self._formality_band(words),
            greeting_closing_band=self._greeting_closing_band(body),
            hedging_frequency_band=self._hedging_frequency_band(sentences, body),
            directness_band=directness_band,
            response_timing_band=self._response_timing_band(sent_at, thread_sent_ats),
            thread_depth_band=self._thread_depth_band(thread_depth),
        )
