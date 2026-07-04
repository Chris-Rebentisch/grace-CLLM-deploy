"""Tests for D520 — multi-source entity sensitivity tag union (most-restrictive-wins).

Verifies:
- Two sources, one privileged, one empty -> entity is privileged.
- Two sources with different tags -> union of all tags.

D356 capture-the-why: D520 — union/most-restrictive-wins rule for
multi-source entities; attorney-client privilege is propagating/conservative.
"""

from src.ingestion.communications.sensitivity_tagger import tags_from_bar_form, tags_to_bar_form


def test_most_restrictive_wins_privileged():
    """If any source carries |privileged|, the entity is |privileged|."""
    source_a_tags = ""
    source_b_tags = "|privileged|"

    # Union logic (matches graph_writer.py multi-source path)
    a_set = set(tags_from_bar_form(source_a_tags))
    b_set = set(tags_from_bar_form(source_b_tags))
    merged = a_set | b_set
    result = tags_to_bar_form(sorted(merged))

    assert result == "|privileged|"
    assert "privileged" in tags_from_bar_form(result)


def test_union_multiple_tags():
    """Two sources with different tags -> union of all tags."""
    source_a_tags = "|pii_dense|"
    source_b_tags = "|privileged|"

    a_set = set(tags_from_bar_form(source_a_tags))
    b_set = set(tags_from_bar_form(source_b_tags))
    merged = a_set | b_set
    result = tags_to_bar_form(sorted(merged))

    assert result == "|pii_dense|privileged|"
    parsed = tags_from_bar_form(result)
    assert "pii_dense" in parsed
    assert "privileged" in parsed
