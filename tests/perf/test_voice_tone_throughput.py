"""Performance test: Voice & Tone profiling throughput (Chunk 61, CP6).

Warn-only + skip-gracefully when Ollama unavailable.
Target: ~30s/person for 100 emails (mock LLM measures pipeline overhead).
"""

from __future__ import annotations

import pytest

from tests.perf.conftest import perf_timer
from src.ingestion.communications.voice_tone.models import StyleSignature


@pytest.mark.perf
def test_voice_tone_signature_construction_throughput():
    """StyleSignature construction overhead for 100 profiles must be < 30s.

    Measures Pydantic model construction overhead (the bottleneck in
    profile generation is LLM inference, not model construction). With
    mock data, this should be near-instant.
    """
    n = 100

    with perf_timer() as t:
        for i in range(n):
            StyleSignature(
                sentence_length_band="medium",
                vocabulary_complexity_band="high" if i % 3 == 0 else "medium",
                formality_band="low" if i % 5 == 0 else "medium",
                greeting_closing_band="medium",
                hedging_frequency_band="medium",
                directness_band="high" if i % 2 == 0 else "low",
                response_timing_band="medium",
                thread_depth_band="medium",
            )

    elapsed = t["elapsed"]
    assert elapsed < 30.0, (
        f"Voice & Tone signature construction took {elapsed:.2f}s for {n} profiles "
        f"(> 30s floor)"
    )
