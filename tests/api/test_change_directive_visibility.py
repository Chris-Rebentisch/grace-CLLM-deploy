"""D296 — resolve_visibility per-mode tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.permissions.change_directive_visibility import resolve_visibility
from src.permissions.enforcer import rebuild_enforcer


@pytest.fixture(autouse=True)
def _reset_enforcer_for_v1_visibility() -> None:
    """Override the top-level permissive-matrix conftest for these tests.

    `scoped_to_role_cluster` consults the live enforcer; v1 expects
    "no matrix → deny non-author non-admin" semantics.
    """
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)


def _directive(visibility: str, *, named=None, role_cluster=None, author=None):
    return {
        "directive_id": uuid4(),
        "authored_by": author or uuid4(),
        "visibility": visibility,
        "visibility_named_list": named,
        "visibility_role_cluster": role_cluster,
    }


@pytest.mark.parametrize("admin", [True, False])
def test_author_always_admitted(admin: bool) -> None:
    author = uuid4()
    for mode in (
        "permission_matrix_default",
        "private_to_self",
        "private_to_named_list",
        "scoped_to_role_cluster",
    ):
        directive = _directive(mode, author=author, named=[])
        assert resolve_visibility(directive, author, admin_key_present=admin) is True


def test_private_to_self_strict_admin_does_not_override() -> None:
    author = uuid4()
    intruder = uuid4()
    directive = _directive("private_to_self", author=author)
    # Admin key MUST NOT override private_to_self.
    assert (
        resolve_visibility(directive, intruder, admin_key_present=True) is False
    )
    assert (
        resolve_visibility(directive, intruder, admin_key_present=False) is False
    )


def test_permission_matrix_default_admin_overrides() -> None:
    author = uuid4()
    intruder = uuid4()
    directive = _directive("permission_matrix_default", author=author)
    assert resolve_visibility(directive, intruder, admin_key_present=True) is True
    assert (
        resolve_visibility(directive, intruder, admin_key_present=False) is False
    )


def test_named_list_admits_listed_and_rejects_others() -> None:
    author = uuid4()
    listed = uuid4()
    other = uuid4()
    directive = _directive(
        "private_to_named_list",
        author=author,
        named=[str(listed)],
    )
    assert resolve_visibility(directive, listed, admin_key_present=False) is True
    assert resolve_visibility(directive, other, admin_key_present=False) is False
    # admin does NOT override named-list (only author + listed are admitted)
    assert resolve_visibility(directive, other, admin_key_present=True) is False


def test_scoped_to_role_cluster_v1_admits_admin() -> None:
    author = uuid4()
    other = uuid4()
    directive = _directive("scoped_to_role_cluster", author=author, role_cluster="ops")
    assert resolve_visibility(directive, other, admin_key_present=True) is True
    assert resolve_visibility(directive, other, admin_key_present=False) is False


def test_unknown_mode_denies() -> None:
    author = uuid4()
    directive = _directive("not_a_real_mode", author=author)
    assert (
        resolve_visibility(directive, uuid4(), admin_key_present=True) is False
    )
