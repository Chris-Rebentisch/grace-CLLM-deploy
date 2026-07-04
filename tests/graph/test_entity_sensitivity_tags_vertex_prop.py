"""Tests for D519 — sensitivity_tags vertex property on domain entities.

Verifies:
- EntityCreate carries sensitivity_tags with default empty string.
- Bar-form round-trip serialization.
- VERTEX_SYSTEM_PROPERTIES includes sensitivity_tags (count 18).
- EDGE_SYSTEM_PROPERTIES does NOT include sensitivity_tags (vertex-only).

D356 capture-the-why: D519 — access-control vertex property for privilege
governance; format per D344/D440; D466 Document_Chunk precedent mirrored
for domain entities.
"""

from src.graph.entity_models import EntityCreate
from src.graph.system_properties import EDGE_SYSTEM_PROPERTIES, VERTEX_SYSTEM_PROPERTIES


def test_sensitivity_tags_default_empty():
    """EntityCreate() defaults sensitivity_tags to empty string."""
    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={"name": "Acme Corp"},
    )
    assert entity.sensitivity_tags == ""


def test_sensitivity_tags_bar_form_roundtrip():
    """EntityCreate accepts bar-form and preserves it."""
    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={"name": "Acme Corp"},
        sensitivity_tags="|privileged|",
    )
    assert entity.sensitivity_tags == "|privileged|"

    # Multi-tag
    entity2 = EntityCreate(
        entity_type="Legal_Entity",
        properties={"name": "Acme Corp"},
        sensitivity_tags="|pii_dense|privileged|",
    )
    assert entity2.sensitivity_tags == "|pii_dense|privileged|"


def test_system_properties_count():
    """VERTEX_SYSTEM_PROPERTIES has exactly 20 entries.

    F-0047b / ISS-0055 Layer 1 (2026-07-03) — invariant-count change
    18 -> 20: `sensitivity_tag_sources` + `sensitivity_source_total`
    provide the write-time tag provenance that makes the D520 union
    reversible/inspectable (capture-the-why in system_properties.py).
    """
    assert len(VERTEX_SYSTEM_PROPERTIES) == 20


def test_system_properties_includes_sensitivity_tags():
    """sensitivity_tags + Layer-1 provenance props declared in VERTEX_SYSTEM_PROPERTIES."""
    names = [p["name"] for p in VERTEX_SYSTEM_PROPERTIES]
    assert "sensitivity_tags" in names
    # ISS-0055 Layer 1 provenance pair.
    assert "sensitivity_tag_sources" in names
    assert "sensitivity_source_total" in names


def test_edge_system_properties_includes_sensitivity_tags():
    """EDGE_SYSTEM_PROPERTIES now includes sensitivity_tags.

    F-0047b / ISS-0055 Layer 2 (2026-07-03) — invariant-count change
    16 -> 17: edges inherit source sensitivity tags (D519 was vertex-only;
    untagged privileged edges would leak once endpoints become visible
    under the evidence_scoped posture).
    """
    names = [p["name"] for p in EDGE_SYSTEM_PROPERTIES]
    assert "sensitivity_tags" in names
    assert len(EDGE_SYSTEM_PROPERTIES) == 17
