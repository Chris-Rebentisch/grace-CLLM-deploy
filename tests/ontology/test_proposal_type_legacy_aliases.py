"""D477: Tests for ProposalType legacy alias tolerance.

Eight tests verifying that ``ProposalType._missing_()`` resolves known
legacy values (e.g. ``schema_evolution``) to canonical enum members
while rejecting unknown values and leaving the normal path unaffected.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.ontology.models import ProposalType, _PROPOSAL_TYPE_LEGACY_ALIASES, _warned_aliases


@pytest.fixture(autouse=True)
def _clear_warned_aliases():
    """Reset the rate-limiter set before each test."""
    _warned_aliases.clear()
    yield
    _warned_aliases.clear()


def test_alias_resolution():
    """ProposalType('schema_evolution') returns ProposalType.MODIFY_PROPERTY."""
    result = ProposalType("schema_evolution")
    assert result is ProposalType.MODIFY_PROPERTY


def test_unknown_value_rejected():
    """ProposalType('nonsense') raises ValueError."""
    with pytest.raises(ValueError, match="nonsense"):
        ProposalType("nonsense")


def test_counter_increment():
    """OTel counter increments on alias resolution."""
    from src.analytics.metrics import grace_proposal_type_legacy_alias_resolved_total

    with patch.object(grace_proposal_type_legacy_alias_resolved_total, "add") as mock_add:
        ProposalType("schema_evolution")
    mock_add.assert_called_once_with(1, {"legacy_value": "schema_evolution"})


def test_idempotency():
    """Second call with same alias returns same result."""
    r1 = ProposalType("schema_evolution")
    r2 = ProposalType("schema_evolution")
    assert r1 is r2
    assert r1 is ProposalType.MODIFY_PROPERTY


def test_normal_path_unaffected():
    """All 10 canonical enum values resolve without triggering alias path."""
    canonical_values = [
        "add_entity_type", "add_relationship", "add_property",
        "split_type", "merge_types", "deprecate_type",
        "move_hierarchy", "add_synonym", "modify_property",
        "change_domain_range",
    ]
    for val in canonical_values:
        result = ProposalType(val)
        assert result.value == val
    # _warned_aliases should be empty — no alias was resolved
    assert len(_warned_aliases) == 0


def test_missing_returns_none_for_unknown():
    """_missing_ returns None for truly unknown values."""
    result = ProposalType._missing_("totally_bogus")
    assert result is None


def test_warning_emitted_once():
    """structlog warning emitted on first call; suppressed on repeat."""
    with patch("src.ontology.models._proposal_type_log") as mock_log:
        ProposalType("schema_evolution")
        assert mock_log.warning.call_count == 1

        ProposalType("schema_evolution")
        # Still 1 — second call suppressed by rate limiter
        assert mock_log.warning.call_count == 1


def test_multiple_distinct_aliases():
    """If additional aliases are added, each resolves independently.

    This test verifies the framework works for multiple entries in
    _PROPOSAL_TYPE_LEGACY_ALIASES. We temporarily add a second alias.
    """
    original = dict(_PROPOSAL_TYPE_LEGACY_ALIASES)
    try:
        _PROPOSAL_TYPE_LEGACY_ALIASES["old_add_type"] = "add_entity_type"
        r1 = ProposalType("schema_evolution")
        r2 = ProposalType("old_add_type")
        assert r1 is ProposalType.MODIFY_PROPERTY
        assert r2 is ProposalType.ADD_ENTITY_TYPE
    finally:
        _PROPOSAL_TYPE_LEGACY_ALIASES.clear()
        _PROPOSAL_TYPE_LEGACY_ALIASES.update(original)
