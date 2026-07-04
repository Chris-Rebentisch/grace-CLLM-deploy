"""F-33 regression: the expanded hedging lexicon must let a densely-hedging
voice band distinctly from a direct one (profiles were non-discriminative — all
4 seeded voices produced identical bands; "may"/"I would generally expect"/
"I hesitate" were missing from the lexicon)."""

from __future__ import annotations

from src.ingestion.communications.voice_tone.feature_extractor import FeatureExtractor
from src.ingestion.communications.voice_tone.models import VoiceToneConfig


def _band(text):
    fx = FeatureExtractor(VoiceToneConfig())
    return fx.extract_features(
        body_plain=text, body_html=None, sent_at=None,
        thread_sent_ats=None, thread_depth=1, directness_band="medium",
    ).hedging_frequency_band


def test_hedging_voice_bands_higher_than_direct_voice():
    hedgy = (
        "I would generally expect this may work. I hesitate to commit. "
        "Perhaps we should wait; I'm not sure, and I suppose it could change."
    )
    direct = "Ship it now. The deal closes Friday. Send the wire immediately."
    assert _band(hedgy) != _band(direct)
    assert _band(hedgy) == "high"


def test_newly_added_hedges_are_present():
    lex = {h.lower() for h in VoiceToneConfig().hedging_lexicon}
    for term in ("may", "i would generally expect", "i hesitate"):
        assert term in lex
