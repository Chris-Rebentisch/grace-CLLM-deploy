"""F-09 regression tests: write-time ontology type enforcement.

Original defect: `CREATE (n:Contractor ...)` silently materialized a
`Contractor` vertex type in ArcadeDB although no such type was human-approved.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.graph import type_enforcement as te


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    te.invalidate_type_cache()
    monkeypatch.delenv("GRACE_TYPE_ENFORCEMENT", raising=False)
    yield
    te.invalidate_type_cache()


def _patch_allowlist(allowed: set[str], ontology_seen: bool = True):
    return patch.object(
        te, "_build_allowlist", return_value=(allowed, ontology_seen)
    )


def test_undefined_type_rejected_in_enforce_mode():
    with _patch_allowlist({"Person", "Legal_Entity", "Extraction_Event"}):
        with pytest.raises(te.UndefinedEntityTypeError) as exc_info:
            te.validate_entity_type("Contractor")
    assert "Contractor" in str(exc_info.value)


def test_ontology_and_system_types_pass():
    with _patch_allowlist({"Person", "Extraction_Event", "Decision_Principle"}):
        te.validate_entity_type("Person")
        te.validate_entity_type("Extraction_Event")
        te.validate_entity_type("Decision_Principle")


def test_warn_mode_allows_with_log(monkeypatch):
    monkeypatch.setenv("GRACE_TYPE_ENFORCEMENT", "warn")
    with _patch_allowlist({"Person"}):
        te.validate_entity_type("Contractor")  # must not raise


def test_no_active_ontology_fails_open():
    """The boundary begins at first ratification: with no active ontology
    readable, enforcement must not engage (fresh deployment / test envs)."""
    with _patch_allowlist({"Extraction_Event"}, ontology_seen=False):
        te.validate_entity_type("Anything")  # must not raise


def test_system_plane_in_real_allowlist():
    """The real builder always includes META + intent types (no DB needed —
    ontology/namespace reads fail soft)."""
    allowed, _ontology_seen = te._build_allowlist()
    assert "Extraction_Event" in allowed
    assert "Query_Event" in allowed
    assert "Image_Asset" in allowed
    assert "Document_Chunk" in allowed
    assert "Decision_Principle" in allowed


def test_route_returns_422_for_undefined_type():
    """API front door: undefined type is a clean 422, no graph write."""
    from fastapi import HTTPException

    import src.api.graph_routes as gr
    from src.graph.entity_models import EntityCreate

    entity = EntityCreate(
        entity_type="Contractor", properties={"name": "Ridgeline Grading"}
    )
    with _patch_allowlist({"Person"}):
        with patch.object(gr, "_get_client") as mock_client:
            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(gr.create_entity(entity))
    assert exc_info.value.status_code == 422
    assert "Contractor" in str(exc_info.value.detail)
    mock_client.assert_not_called()
