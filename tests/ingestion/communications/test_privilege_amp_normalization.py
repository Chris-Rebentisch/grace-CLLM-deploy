"""F-25 regression: the privilege-phrase matcher must normalize '&' <-> 'and'
(and case) so "PRIVILEGED & CONFIDENTIAL" matches a config phrase written with
"and" (and vice versa). Only 1/3 litigation emails tagged before this fix.
"""

from __future__ import annotations

from src.ingestion.communications.sensitivity_tagger import _detect_privileged, _norm_amp


def test_ampersand_subject_matches_and_phrase():
    cfg = {"privilege_phrases": ["privileged and confidential"]}
    tags = _detect_privileged(
        {"subject": "PRIVILEGED & CONFIDENTIAL — ATTORNEY-CLIENT", "body_plain": ""},
        cfg,
    )
    assert "privileged" in tags


def test_and_subject_matches_ampersand_phrase():
    cfg = {"privilege_phrases": ["privileged & confidential"]}
    tags = _detect_privileged(
        {"subject": "Privileged and Confidential matter", "body_plain": ""}, cfg
    )
    assert "privileged" in tags


def test_unrelated_subject_not_tagged():
    cfg = {"privilege_phrases": ["privileged and confidential"]}
    tags = _detect_privileged({"subject": "lunch tomorrow", "body_plain": "hi"}, cfg)
    assert "privileged" not in tags


def test_norm_amp_canonicalization():
    assert _norm_amp("PRIVILEGED  &   Confidential") == "privileged and confidential"
