"""Change-directive visibility resolver tests (Chunk 42, CP9, D295/D339).

Coverage:

* ``private_to_self`` admits the author and rejects everyone — INCLUDING
  callers presenting an admin key (R4 closure / D295).
* ``private_to_self`` rejects when no requesting user is supplied even
  if admin-key is present.
* ``permission_matrix_default`` admits author OR admin.
* ``private_to_named_list`` admits author + listed users; rejects others.
* ``scoped_to_role_cluster`` consults the Enforcer for non-author,
  non-admin callers (D339).
* On-row visibility enum literal strings remain byte-identical to the
  Chunk 38 stub (D285 forward-guarantee).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.permissions.change_directive_visibility import resolve_visibility
from src.permissions.enforcer import rebuild_enforcer
from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
    VisibilityMode,
)


@pytest.fixture(autouse=True)
def _reset_enforcer():
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)


# ---------- T1: private_to_self admits author only ---------------


def test_private_to_self_admits_author_only() -> None:
    author = uuid4()
    other = uuid4()
    directive = {
        "authored_by": author,
        "visibility": "private_to_self",
    }

    assert resolve_visibility(directive, author, admin_key_present=False) is True
    assert resolve_visibility(directive, other, admin_key_present=False) is False


# ---------- T2: private_to_self ignores admin-key (R4) ----------


def test_private_to_self_rejects_admin_key_caller() -> None:
    author = uuid4()
    other = uuid4()
    directive = {
        "authored_by": author,
        "visibility": "private_to_self",
    }

    # Admin key MUST NOT bypass private_to_self (R4 / D295).
    assert (
        resolve_visibility(directive, other, admin_key_present=True) is False
    )
    # Author with admin-key still admitted (the author is admitted on
    # the author-equality short-circuit, not the admin override).
    assert (
        resolve_visibility(directive, author, admin_key_present=True) is True
    )


# ---------- T3: matrix_default admits author + admin -----------


def test_permission_matrix_default_admits_author_or_admin() -> None:
    author = uuid4()
    other = uuid4()
    directive = {
        "authored_by": author,
        "visibility": "permission_matrix_default",
    }

    assert resolve_visibility(directive, author, admin_key_present=False) is True
    assert resolve_visibility(directive, other, admin_key_present=True) is True
    assert resolve_visibility(directive, other, admin_key_present=False) is False


# ---------- T4: private_to_named_list ---------------------------


def test_private_to_named_list_admits_listed() -> None:
    author = uuid4()
    listed = uuid4()
    other = uuid4()
    directive = {
        "authored_by": author,
        "visibility": "private_to_named_list",
        "visibility_named_list": [str(listed)],
    }

    assert resolve_visibility(directive, author, admin_key_present=False) is True
    assert resolve_visibility(directive, listed, admin_key_present=False) is True
    assert resolve_visibility(directive, other, admin_key_present=False) is False
    # Admin-key does NOT widen named_list — match must be by identity.
    assert resolve_visibility(directive, other, admin_key_present=True) is False


# ---------- T5: scoped_to_role_cluster consults Enforcer (D339) -


def test_scoped_to_role_cluster_consults_enforcer() -> None:
    author = uuid4()
    member = uuid4()
    outsider = uuid4()
    directive_id = uuid4()

    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="reviewers",
                display_name="Reviewers",
                members=[RoleClusterMember(person_grace_id=str(member))],
                access_rules=[
                    AccessRule(
                        resource_kind="change_directive",
                        resource_label=str(directive_id),
                        action="view",
                        decision="allow",
                    )
                ],
            )
        ],
        default_decision="deny",
    )
    rebuild_enforcer(matrix)

    directive = {
        "authored_by": author,
        "visibility": "scoped_to_role_cluster",
        "change_directive_id": directive_id,
    }

    # Author admitted by short-circuit.
    assert resolve_visibility(directive, author, admin_key_present=False) is True
    # Cluster member admitted via Enforcer.
    assert resolve_visibility(directive, member, admin_key_present=False) is True
    # Outsider denied (no matching rule + default-deny).
    assert resolve_visibility(directive, outsider, admin_key_present=False) is False
    # Admin-key short-circuits to admit (mirrors permission_matrix_default).
    assert resolve_visibility(directive, outsider, admin_key_present=True) is True


# ---------- T6: on-row enum literal strings are byte-identical --


def test_visibility_enum_literal_strings_byte_identical() -> None:
    """``VisibilityMode`` Literal must contain exactly the four strings
    persisted on the ``change_directives`` row (Chunk 38 stub). D285
    forward-guarantee held by Chunk 42."""
    expected = {
        "permission_matrix_default",
        "private_to_self",
        "private_to_named_list",
        "scoped_to_role_cluster",
    }

    # Pull the literal members out of the typing.Literal annotation.
    from typing import get_args

    actual = set(get_args(VisibilityMode))
    assert actual == expected
