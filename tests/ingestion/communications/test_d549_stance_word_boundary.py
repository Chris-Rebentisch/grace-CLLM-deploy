"""D549 guard — stance cue matching must be word-boundary, not substring.

Regression for Finding #17 (surfaced live by the bounded-heat apply-gate): the
contradict cue "no" matched as a SUBSTRING inside "Notice"/"Alice", so plainly
affirming deal emails were classified `contradict` → s_plus collapsed to 0 →
nothing ever promoted to first_class.

Heat-free: rule-based path only (use_llm_fallback defaults False).
"""

from dataclasses import dataclass, field

from src.ingestion.communications.corroboration_scorer import (
    _cue_in_text,
    classify_stance,
)


@dataclass
class _Cfg:
    contradict_cues: list = field(default_factory=lambda: ["no", "incorrect", "not true", "I disagree"])
    affirm_cues: list = field(default_factory=lambda: ["confirmed", "agree", "correct"])


def test_short_cue_does_not_match_inside_words():
    # "no" must NOT match inside "Notice", "Alice", "cannot"
    assert not _cue_in_text("no", "please prepare the capital call notice")
    assert not _cue_in_text("no", "alice, for the summit close")
    assert not _cue_in_text("no", "we cannot proceed")  # 'cannot' contains 'no'


def test_standalone_cue_still_matches():
    assert _cue_in_text("no", "no, that is wrong")
    assert _cue_in_text("not true", "that is not true at all")
    assert _cue_in_text("incorrect", "the figure is incorrect")


def test_affirming_deal_email_is_not_contradict():
    """The exact Finding #17 bodies must classify as affirm/incidental, not contradict."""
    cfg = _Cfg()
    deal_001 = (
        "Robert,\n\nThe Acme Family Trust will commit $5,000,000 to the Summit "
        "Logistics acquisition. Please prepare the capital call notice for the trust "
        "and circulate the subscription agreement to our counsel.\n\nBest,\nAlice"
    )
    deal_006 = (
        "Alice,\n\nFor the Summit Logistics close, please confirm the wire from "
        "the trust's account.\n"
    )
    assert classify_stance(deal_001, cfg) != "contradict"
    assert classify_stance(deal_006, cfg) != "contradict"


def test_genuine_contradiction_still_detected():
    cfg = _Cfg()
    assert classify_stance("No, that figure is incorrect.", cfg) == "contradict"
