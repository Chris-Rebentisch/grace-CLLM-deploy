"""Scope-aware verify_hash_chain tests — F-0045 / ISS-0025 hash-chain follow-up.

Invariant under test: the ontology version history is a TREE, not a line.
The F-0045 activation guard makes child/connector-sync versions
non-activating; such a version chains from the active mother, and the NEXT
mother ratification also chains from that same mother — a legitimate
branch. ``verify_hash_chain`` must:

- verify the MOTHER chain linearly (regression: linear-only history
  unchanged);
- verify every BRANCH row against its RECORDED parent
  (``previous_version_id``) — nothing skipped or exempted;
- still detect tampering everywhere (mutated mother row fails, mutated
  branch row fails).

Pure unit tests — ``list_versions`` is patched at the
``src.ontology.schema_store`` namespace; no Postgres, no ArcadeDB.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.ontology.models import OntologyVersion, VersionSource
from src.ontology.schema_store import compute_hash, verify_hash_chain

MOTHER_SCHEMA_V1 = {
    "entity_types": {"Legal_Entity": {"properties": {}}},
    "relationships": {},
}
MOTHER_SCHEMA_V2 = {
    "entity_types": {"Legal_Entity": {"properties": {}}, "Trust": {"properties": {}}},
    "relationships": {},
}
CHILD_SCHEMA = {"entity_types": {}, "relationships": {}}


def _mk_version(
    version_number: int,
    schema: dict,
    parent: OntologyVersion | None = None,
    *,
    source: VersionSource = VersionSource.DISCOVERY,
    metadata_extra: dict | None = None,
) -> OntologyVersion:
    """Build a version whose hash_chain matches the write-time invariant.

    hash_chain = SHA256(canonical_json(schema) + parent.hash_chain), where
    ``parent`` is the version recorded in ``previous_version_id`` — exactly
    what ``ratify_version`` persists.
    """
    prev_hash = parent.hash_chain if parent else None
    return OntologyVersion(
        id=uuid4(),
        version_number=version_number,
        schema_json=schema,
        schema_modules={},
        previous_version_id=parent.id if parent else None,
        hash_chain=compute_hash(schema, prev_hash),
        source=source,
        is_active=False,
        metadata_extra=metadata_extra or {},
    )


def _verify(versions: list[OntologyVersion]) -> dict:
    """Run verify_hash_chain against an in-memory version list (no DB).

    ``list_versions`` returns DESCENDING version order; mirror that so the
    production ``.reverse()`` path is exercised.
    """
    descending = sorted(versions, key=lambda v: v.version_number, reverse=True)
    with patch(
        "src.ontology.schema_store.list_versions", return_value=descending
    ):
        return verify_hash_chain(MagicMock())


def _f0045_history() -> tuple[OntologyVersion, OntologyVersion, OntologyVersion]:
    """The exact F-0045 shape: mother v1 -> connector child v2 (branch off
    v1) -> mother v3 (ALSO chains off v1, because the child never
    activated). The old linear walk mis-flagged v3."""
    m1 = _mk_version(1, MOTHER_SCHEMA_V1, source=VersionSource.DISCOVERY)
    child = _mk_version(
        2,
        CHILD_SCHEMA,
        parent=m1,
        source=VersionSource.CONNECTOR_SYNC,
        metadata_extra={"ontology_scope": "child"},
    )
    m2 = _mk_version(3, MOTHER_SCHEMA_V2, parent=m1, source=VersionSource.GUIDED_REVIEW)
    return m1, child, m2


# ---------------------------------------------------------------------------
# 1. Regression: linear-only history verifies exactly as before.
# ---------------------------------------------------------------------------


def test_linear_only_history_verifies() -> None:
    m1 = _mk_version(1, MOTHER_SCHEMA_V1)
    m2 = _mk_version(2, MOTHER_SCHEMA_V2, parent=m1, source=VersionSource.MANUAL)

    result = _verify([m1, m2])

    assert result["valid"] is True
    assert result["versions_checked"] == 2
    assert result["first_broken_version"] is None
    # Additive fields present with sane values.
    assert result["mother_chain"]["valid"] is True
    assert result["mother_chain"]["versions_checked"] == 2
    assert result["branches_checked"] == 0
    assert result["branch_failures"] == []


def test_empty_history_verifies_with_additive_fields() -> None:
    result = _verify([])
    assert result["valid"] is True
    assert result["versions_checked"] == 0
    assert result["mother_chain"]["valid"] is True
    assert result["branches_checked"] == 0
    assert result["branch_failures"] == []


# ---------------------------------------------------------------------------
# 2. The F-0045 branch shape (child-then-mother) verifies CLEAN.
# ---------------------------------------------------------------------------


def test_child_then_mother_f0045_shape_verifies_clean() -> None:
    """The exact deferral case: a legitimate branch must not cry wolf."""
    m1, child, m2 = _f0045_history()

    result = _verify([m1, child, m2])

    assert result["valid"] is True
    assert result["first_broken_version"] is None
    assert result["versions_checked"] == 3
    assert result["mother_chain"]["valid"] is True
    assert result["mother_chain"]["versions_checked"] == 2
    assert result["branches_checked"] == 1
    assert result["branch_failures"] == []


def test_mother_plus_child_branch_verifies_clean() -> None:
    """Mother + trailing child branch (no follow-up mother) verifies."""
    m1 = _mk_version(1, MOTHER_SCHEMA_V1)
    child = _mk_version(
        2, CHILD_SCHEMA, parent=m1, source=VersionSource.CONNECTOR_SYNC
    )

    result = _verify([m1, child])

    assert result["valid"] is True
    assert result["branches_checked"] == 1
    assert result["mother_chain"]["versions_checked"] == 1


def test_child_scope_metadata_alone_classifies_branch() -> None:
    """A child-scoped version (any source) is a branch — D405 semantics."""
    m1 = _mk_version(1, MOTHER_SCHEMA_V1)
    child = _mk_version(
        2,
        CHILD_SCHEMA,
        parent=m1,
        source=VersionSource.MANUAL,
        metadata_extra={"ontology_scope": "child"},
    )
    m2 = _mk_version(3, MOTHER_SCHEMA_V2, parent=m1, source=VersionSource.MANUAL)

    result = _verify([m1, child, m2])

    assert result["valid"] is True
    assert result["branches_checked"] == 1


# ---------------------------------------------------------------------------
# 3. Tamper detection still works EVERYWHERE.
# ---------------------------------------------------------------------------


def test_tampered_child_branch_fails() -> None:
    """A mutated payload in a BRANCH row must fail verification."""
    m1, child, m2 = _f0045_history()
    # Payload tamper: stored hash_chain no longer matches recomputation.
    child.schema_json = {"entity_types": {"Injected": {}}, "relationships": {}}

    result = _verify([m1, child, m2])

    assert result["valid"] is False
    assert result["first_broken_version"] == 2
    assert result["mother_chain"]["valid"] is True  # mother untouched
    assert len(result["branch_failures"]) == 1
    failure = result["branch_failures"][0]
    assert failure["version_number"] == 2
    assert failure["parent_version_number"] == 1


def test_tampered_mother_fails() -> None:
    """A mutated mother row must fail verification (linear walk)."""
    m1, child, m2 = _f0045_history()
    m1.schema_json = {"tampered": True}

    result = _verify([m1, child, m2])

    assert result["valid"] is False
    assert result["first_broken_version"] == 1
    assert result["mother_chain"]["valid"] is False
    assert result["mother_chain"]["first_broken_version"] == 1


def test_tampered_second_mother_fails() -> None:
    """Tamper in the post-branch mother is attributed to that mother."""
    m1, child, m2 = _f0045_history()
    m2.schema_json = {"tampered": True}

    result = _verify([m1, child, m2])

    assert result["valid"] is False
    assert result["first_broken_version"] == 3
    assert result["mother_chain"]["valid"] is False
    assert result["branch_failures"] == []  # child still verifies vs m1


def test_branch_with_dangling_parent_fails() -> None:
    """A branch recording a nonexistent parent is a failure, not a skip."""
    m1 = _mk_version(1, MOTHER_SCHEMA_V1)
    phantom_parent = _mk_version(99, MOTHER_SCHEMA_V1)
    child = _mk_version(
        2, CHILD_SCHEMA, parent=phantom_parent, source=VersionSource.CONNECTOR_SYNC
    )

    result = _verify([m1, child])  # phantom_parent NOT in history

    assert result["valid"] is False
    assert result["first_broken_version"] == 2
    assert len(result["branch_failures"]) == 1
    assert "does not exist" in result["branch_failures"][0]["detail"]
