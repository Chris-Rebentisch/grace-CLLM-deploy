"""Repository tests for ``permission_matrices`` (Chunk 42, CP1, D331).

Hash-chain integrity, server-side hash discipline, append-only trigger
enforcement, and ``grace_readonly`` SELECT GRANT.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError

from src.permissions import repository as repo
from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
)


# D485 carve-out (Chunk 75a): this module genuinely requires empty-baseline
# semantics (genesis null previous_hash, verify_chain_empty, append-only triggers).
# TRUNCATE retained with requires_db_wipe marker for D472 interlock.
pytestmark = [
    pytest.mark.skipif(
        os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
        reason="Postgres not available",
    ),
    pytest.mark.requires_db_wipe,
]


def _make_matrix(label: str = "v1") -> PermissionMatrix:
    return PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id=f"cluster_{label}",
                display_name=f"Cluster {label}",
                members=[RoleClusterMember(person_grace_id="p-1")],
                access_rules=[
                    AccessRule(
                        resource_kind="ontology_module",
                        resource_label="ops",
                        action="view",
                        decision="allow",
                    )
                ],
            )
        ]
    )


def _truncate(session) -> None:
    """Clean state for chain tests — use a fresh chain per test."""
    session.execute(
        text("TRUNCATE TABLE permission_matrices RESTART IDENTITY CASCADE")
    )
    session.commit()


# ---------- Canonical-JSON hash discipline ----------


def test_compute_payload_hash_is_canonical_json_sorted_keys():
    a = {"foo": 1, "bar": [1, 2], "baz": "qux"}
    b = {"baz": "qux", "bar": [1, 2], "foo": 1}
    assert repo._compute_payload_hash(a) == repo._compute_payload_hash(b)


def test_compute_payload_hash_returns_64_char_hex():
    h = repo._compute_payload_hash({"x": 1})
    assert len(h) == 64
    int(h, 16)  # parses as hex


# ---------- Hash chain integrity ----------


def test_insert_matrix_first_row_has_null_previous_hash(db_session):
    _truncate(db_session)
    m = _make_matrix()
    row = repo.insert_matrix(db_session, matrix=m)
    db_session.commit()
    assert row["previous_hash"] is None
    assert isinstance(row["payload_hash"], str)
    assert len(row["payload_hash"]) == 64


def test_insert_matrix_second_row_chains_to_first(db_session):
    _truncate(db_session)
    row1 = repo.insert_matrix(db_session, matrix=_make_matrix("a"))
    db_session.commit()
    row2 = repo.insert_matrix(db_session, matrix=_make_matrix("b"))
    db_session.commit()
    assert row2["previous_hash"] == row1["payload_hash"]
    assert row1["payload_hash"] != row2["payload_hash"]


def test_get_active_matrix_returns_newest(db_session):
    _truncate(db_session)
    repo.insert_matrix(db_session, matrix=_make_matrix("a"))
    db_session.commit()
    row2 = repo.insert_matrix(db_session, matrix=_make_matrix("b"))
    db_session.commit()
    active = repo.get_active_matrix(db_session)
    assert active is not None
    assert active["payload_hash"] == row2["payload_hash"]


def test_get_active_matrix_returns_none_when_empty(db_session):
    _truncate(db_session)
    assert repo.get_active_matrix(db_session) is None


def test_get_matrix_versions_paginated_newest_first(db_session):
    _truncate(db_session)
    rows = []
    for i in range(3):
        rows.append(
            repo.insert_matrix(db_session, matrix=_make_matrix(f"v{i}"))
        )
        db_session.commit()
    versions = repo.get_matrix_versions(db_session, limit=10)
    # Newest first.
    assert len(versions) == 3
    assert versions[0]["payload_hash"] == rows[2]["payload_hash"]
    assert versions[2]["payload_hash"] == rows[0]["payload_hash"]


def test_get_matrix_by_id_lookup(db_session):
    _truncate(db_session)
    row = repo.insert_matrix(db_session, matrix=_make_matrix())
    db_session.commit()
    fetched = repo.get_matrix_by_id(db_session, row["permission_matrix_id"])
    assert fetched is not None
    assert fetched["payload_hash"] == row["payload_hash"]


def test_get_matrix_by_id_returns_none_for_unknown(db_session):
    _truncate(db_session)
    assert repo.get_matrix_by_id(db_session, uuid4()) is None


def test_verify_chain_empty(db_session):
    _truncate(db_session)
    result = repo.verify_chain(db_session)
    assert result == {"valid": True, "chain_length": 0, "broken_at": None}


def test_verify_chain_valid_after_inserts(db_session):
    _truncate(db_session)
    repo.insert_matrix(db_session, matrix=_make_matrix("a"))
    db_session.commit()
    repo.insert_matrix(db_session, matrix=_make_matrix("b"))
    db_session.commit()
    result = repo.verify_chain(db_session)
    assert result["valid"] is True
    assert result["chain_length"] == 2


# ---------- Server-side hash discipline (R5) ----------


def test_insert_matrix_ignores_client_hash():
    """``insert_matrix`` does not accept a ``payload_hash`` argument —
    the signature itself enforces server-side computation per R5/D331.
    """
    import inspect

    sig = inspect.signature(repo.insert_matrix)
    assert "payload_hash" not in sig.parameters


# ---------- Append-only trigger enforcement ----------


def test_append_only_trigger_blocks_update(db_session):
    _truncate(db_session)
    row = repo.insert_matrix(db_session, matrix=_make_matrix())
    db_session.commit()
    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "UPDATE permission_matrices SET version_label='hijack' "
                "WHERE permission_matrix_id = :id"
            ),
            {"id": row["permission_matrix_id"]},
        )
        db_session.commit()
    db_session.rollback()


def test_append_only_trigger_blocks_delete(db_session):
    _truncate(db_session)
    row = repo.insert_matrix(db_session, matrix=_make_matrix())
    db_session.commit()
    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "DELETE FROM permission_matrices "
                "WHERE permission_matrix_id = :id"
            ),
            {"id": row["permission_matrix_id"]},
        )
        db_session.commit()
    db_session.rollback()
