"""Tests for CQ template engine."""

import pytest

from src.discovery.cq_models import CQType
from src.discovery.cq_templates import (
    get_templates_by_type,
    get_templates_for_domain,
    load_templates,
    render_template,
    suggest_templates,
)


def test_load_templates():
    """Templates load from YAML, all have required fields."""
    templates = load_templates()
    assert len(templates) > 0
    for t in templates:
        assert t.id
        assert t.cq_type
        assert t.pattern
        assert t.placeholders
        assert t.example
        assert t.hint


def test_get_templates_for_domain():
    """Domain-specific + universal templates returned."""
    insurance = get_templates_for_domain("insurance")
    assert len(insurance) > 0
    # Should include universal templates (empty domain_affinity) and insurance-specific
    ids = {t.id for t in insurance}
    assert "scoping_what_types" in ids  # universal
    assert "relationship_what_covers" in ids  # insurance affinity


def test_get_templates_by_type():
    """Filter by CQ type returns correct templates."""
    scoping = get_templates_by_type(CQType.SCOPING)
    assert len(scoping) > 0
    for t in scoping:
        assert t.cq_type == CQType.SCOPING


def test_render_template():
    """Fill placeholders, verify output matches expected CQ text."""
    result = render_template(
        "scoping_what_types",
        {"entity": "insurance policies"},
    )
    assert result == "What types of insurance policies does the organization have?"


def test_render_template_missing_placeholder():
    """Missing placeholder raises error."""
    with pytest.raises(ValueError, match="Missing placeholders"):
        render_template("scoping_what_exists", {"entity_plural": "companies"})
        # missing "context" placeholder


def test_suggest_templates():
    """Raw input about 'insurance coverage' suggests insurance-related templates."""
    suggestions = suggest_templates("insurance coverage policies")
    assert len(suggestions) > 0
    # At least one should be insurance-related
    ids = {t.id for t in suggestions}
    assert any("cover" in tid or "insurance" in tid or "scoping" in tid for tid in ids)


def test_template_count():
    """At least 15 templates loaded."""
    templates = load_templates()
    assert len(templates) >= 15
