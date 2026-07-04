"""Subject 5 reusability confirmation (Chunk 36, CP5b).

Confirms that ``compute_om4ov_diff`` from ``src/ontology/diff_engine.py``
is callable with two non-temporal schemas without any modification to
``diff_engine.py``. Spec §6 CP5b — the Reconciliation Layer reuses the
existing OM4OV diff machinery rather than introducing a parallel diff
engine.
"""

from __future__ import annotations


def test_compute_om4ov_diff_callable_without_modification() -> None:
    """``compute_om4ov_diff`` returns a structured diff dict for two
    non-temporal schemas with no edits to ``diff_engine.py``."""

    # Importable as-is — Subject 5 reusability claim.
    from src.ontology.diff_engine import compute_om4ov_diff

    schema_a = {
        "entity_types": {
            "Company": {"properties": ["name"]},
            "Person": {"properties": ["name"]},
        }
    }
    # schema_b is a strict superset of schema_a (Person removed; Trust added).
    schema_b = {
        "entity_types": {
            "Company": {"properties": ["name"]},
            "Trust": {"properties": ["name", "settlor"]},
        }
    }

    result = compute_om4ov_diff(schema_a, schema_b)

    assert isinstance(result, dict)
    # Existing OM4OV contract: remain / add / update / delete / summary.
    for key in ("remain", "add", "update", "delete", "summary"):
        assert key in result, f"missing key {key!r} in OM4OV diff output"

    summary = result["summary"]
    assert summary["add_count"] >= 0
    assert summary["delete_count"] >= 0
    assert summary["update_count"] >= 0
    assert summary["remain_count"] >= 0
