"""ClaimSpanDetector — sentence-fallback claim-span detection.

§7 of chunk-23-spec.md. D133: v1 ships sentence_fallback only;
llm_judged and hybrid raise NotImplementedError.
Elicitation Protocol §12.2: fallback defaults span_confidence='low' so
the contract (every claim carries a band) is never violated.
Deterministic per §7.3 (EC-1 compliance).
"""

from __future__ import annotations

import pysbd
import structlog

from src.regeneration.regeneration_config import RegenSettings
from src.regeneration.regeneration_models import ClaimSpan
from src.retrieval.retrieval_models import RetrievalResponse

logger = structlog.get_logger()

_MIN_NAME_LEN = 3


def _band_for_score(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    if score > 0.0:
        return "low"
    return "insufficient_evidence"


class ClaimSpanDetector:
    """Deterministic sentence-fallback claim-span detector."""

    def __init__(self, settings: RegenSettings) -> None:
        self.settings = settings
        self._segmenter = pysbd.Segmenter(language="en", clean=False)

    def detect(
        self,
        response_text: str,
        retrieval_response: RetrievalResponse,
    ) -> tuple[list[ClaimSpan], str | None]:
        """Return (spans, note). note is None on normal success."""
        if self.settings.span_detector_mode != "sentence_fallback":
            raise NotImplementedError(
                f"span_detector_mode={self.settings.span_detector_mode!r} "
                "not implemented in v1 (D133). Only 'sentence_fallback' "
                "is supported."
            )

        if not response_text.strip():
            return [], "no_substantive_claims_detected"

        sentences = self._split_with_offsets(response_text)
        if not sentences:
            return [], "no_substantive_claims_detected"

        name_index: dict[str, tuple[str, float]] = {}
        for result in retrieval_response.results:
            name = (result.name or "").strip().lower()
            if len(name) < _MIN_NAME_LEN:
                continue
            existing = name_index.get(name)
            if existing is None or result.rerank_score > existing[1]:
                name_index[name] = (result.grace_id, result.rerank_score)

        draft_spans: list[ClaimSpan] = []
        for idx, (sent_text, start_char, end_char) in enumerate(sentences):
            lowered = sent_text.lower()
            matched_ids: set[str] = set()
            max_score = 0.0
            for name, (grace_id, score) in name_index.items():
                if name in lowered:
                    matched_ids.add(grace_id)
                    if score > max_score:
                        max_score = score
            if matched_ids:
                band = _band_for_score(max_score)
            else:
                band = "insufficient_evidence"
            draft_spans.append(
                ClaimSpan(
                    text=sent_text,
                    sentence_indices=[idx],
                    start_char=start_char,
                    end_char=end_char,
                    certainty_band=band,
                    span_confidence="low",
                    supporting_grace_ids=sorted(matched_ids),
                )
            )

        merged = self._merge_adjacent(draft_spans)

        if all(s.certainty_band == "insufficient_evidence" for s in merged):
            return [], "no_substantive_claims_detected"

        return merged, None

    def _split_with_offsets(
        self, text: str
    ) -> list[tuple[str, int, int]]:
        """Return list of (sentence_text, start_char, end_char)."""
        spans = self._segmenter.segment(text)
        out: list[tuple[str, int, int]] = []
        cursor = 0
        for sent in spans:
            if not isinstance(sent, str):
                sent = getattr(sent, "sent", str(sent))
            stripped = sent.strip()
            if not stripped:
                continue
            idx = text.find(stripped, cursor)
            if idx == -1:
                idx = cursor
            start_char = idx
            end_char = idx + len(stripped)
            cursor = end_char
            out.append((stripped, start_char, end_char))
        return out

    def _merge_adjacent(
        self, spans: list[ClaimSpan]
    ) -> list[ClaimSpan]:
        if not spans:
            return []
        merged: list[ClaimSpan] = [spans[0]]
        for span in spans[1:]:
            last = merged[-1]
            if (
                last.supporting_grace_ids == span.supporting_grace_ids
                and last.certainty_band == span.certainty_band
            ):
                joined_text = f"{last.text} {span.text}"
                merged[-1] = ClaimSpan(
                    text=joined_text,
                    sentence_indices=last.sentence_indices
                    + span.sentence_indices,
                    start_char=last.start_char,
                    end_char=span.end_char,
                    certainty_band=last.certainty_band,
                    span_confidence="low",
                    supporting_grace_ids=list(last.supporting_grace_ids),
                )
            else:
                merged.append(span)
        return merged
