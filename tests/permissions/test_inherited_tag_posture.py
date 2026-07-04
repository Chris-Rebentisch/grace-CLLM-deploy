"""F-0047b / ISS-0055 Layer 2 — `inherited_tag_posture` knob + posture resolver.

Pure unit tests. Covers:
- PermissionMatrix additive optional field (default "deny", old payloads
  validate — hash-chain-safe).
- resolve_enforcement_posture: deny unless the matrix opts in AND the
  principal resolves to a role-cluster; anonymous/unresolvable principals
  ALWAYS get deny (F-031 / ISS-0013 most-restrictive fallback discipline).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.permissions.models import PermissionMatrix, RoleCluster, RoleClusterMember
from src.permissions.principal_context import User
from src.permissions.sensitivity_resolver import resolve_enforcement_posture

UID = uuid4()


def _cluster(member_uid=None) -> RoleCluster:
    members = []
    if member_uid is not None:
        members = [RoleClusterMember(person_grace_id=str(member_uid))]
    return RoleCluster(cluster_id="c1", display_name="C1", members=members)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def test_matrix_default_posture_is_deny():
    assert PermissionMatrix().inherited_tag_posture == "deny"


def test_old_payload_without_key_validates_to_deny():
    """Hash-chain safety: pre-ISS-0055 JSONB payloads lack the key."""
    matrix = PermissionMatrix.model_validate(
        {"schema_version": "1.0", "role_clusters": [], "default_decision": "deny"}
    )
    assert matrix.inherited_tag_posture == "deny"


def test_matrix_accepts_evidence_scoped():
    matrix = PermissionMatrix(inherited_tag_posture="evidence_scoped")
    assert matrix.inherited_tag_posture == "evidence_scoped"


def test_matrix_rejects_unknown_posture():
    with pytest.raises(ValidationError):
        PermissionMatrix(inherited_tag_posture="allow_all")


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def test_no_matrix_is_deny():
    assert resolve_enforcement_posture(User(user_id=UID), None) == "deny"


def test_non_matrix_object_is_deny():
    assert resolve_enforcement_posture(User(user_id=UID), object()) == "deny"


def test_deny_matrix_with_member_is_deny():
    matrix = PermissionMatrix(role_clusters=[_cluster(UID)])
    assert resolve_enforcement_posture(User(user_id=UID), matrix) == "deny"


def test_evidence_scoped_matrix_with_resolved_member():
    matrix = PermissionMatrix(
        role_clusters=[_cluster(UID)], inherited_tag_posture="evidence_scoped"
    )
    assert resolve_enforcement_posture(User(user_id=UID), matrix) == "evidence_scoped"


def test_anonymous_principal_always_deny():
    """user_id=None (localhost bypass) never gets evidence_scoped."""
    matrix = PermissionMatrix(
        role_clusters=[_cluster(UID)], inherited_tag_posture="evidence_scoped"
    )
    assert resolve_enforcement_posture(User(user_id=None), matrix) == "deny"


def test_unresolvable_principal_always_deny():
    """A user_id matching no cluster gets deny regardless of the knob."""
    matrix = PermissionMatrix(
        role_clusters=[_cluster(UID)], inherited_tag_posture="evidence_scoped"
    )
    assert resolve_enforcement_posture(User(user_id=uuid4()), matrix) == "deny"


def test_evidence_scoped_matrix_without_clusters_is_deny():
    matrix = PermissionMatrix(inherited_tag_posture="evidence_scoped")
    assert resolve_enforcement_posture(User(user_id=UID), matrix) == "deny"
