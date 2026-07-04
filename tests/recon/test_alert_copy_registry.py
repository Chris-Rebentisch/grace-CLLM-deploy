"""Tests for ``src/analytics/alert_copy.py`` (Chunk 37, D289 / EC-12)."""

from __future__ import annotations

from src.analytics.alert_copy import ALERT_COPY_REGISTRY


_RECON_FORBIDDEN_TOKENS = (
    "drift",
    "blind spot",
    "mistake",
    "wrong",
    "reality gap",
    "incorrect",
    "failure",
    "deficit",
)


def test_alert_copy_registry_is_non_empty():
    assert isinstance(ALERT_COPY_REGISTRY, dict)
    assert len(ALERT_COPY_REGISTRY) > 0


def test_alert_copy_registry_has_no_forbidden_tokens():
    """EC-12 forbidden-vocabulary discipline. Every entry's
    ``summary`` + ``recommendation`` is concatenated and scanned
    case-insensitively for any forbidden substring."""
    violations: list[tuple[str, str]] = []
    for key, copy in ALERT_COPY_REGISTRY.items():
        body = (copy["summary"] + " " + copy["recommendation"]).lower()
        for token in _RECON_FORBIDDEN_TOKENS:
            if token in body:
                violations.append((key, token))
    assert not violations, (
        "EC-12 violation — forbidden vocabulary in alert copy: "
        f"{violations}"
    )


def test_alert_copy_entries_are_well_formed():
    """Each entry must have non-empty ``summary`` and ``recommendation``."""
    for key, copy in ALERT_COPY_REGISTRY.items():
        assert "summary" in copy and copy["summary"].strip(), key
        assert "recommendation" in copy and copy["recommendation"].strip(), key
