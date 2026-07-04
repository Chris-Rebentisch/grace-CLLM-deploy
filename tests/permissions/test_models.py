"""Pydantic model tests for the Guided Permissions engine (Chunk 42, CP1)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.permissions.models import (
    AccessRule,
    Allow,
    Deny,
    DriftBand,
    DriftClassification,
    EnforcementReason,
    EvidenceBundle,
    EvidenceSection,
    HypothesisConfidenceBand,
    NullHypothesis,
    PermissionMatrix,
    RoleCluster,
    RoleClusterHypothesisSet,
    RoleClusterMember,
    SegmentedHypothesis,
    SensitivityTag,
    VisibilityRule,
)


def _make_cluster() -> RoleCluster:
    return RoleCluster(
        cluster_id="ops_team",
        display_name="Operations",
        description="Ops cluster",
        members=[
            RoleClusterMember(person_grace_id="p-1", display_name="Pat"),
        ],
        access_rules=[
            AccessRule(
                resource_kind="ontology_module",
                resource_label="ops",
                action="view",
                decision="allow",
            )
        ],
        visibility_rules=[
            VisibilityRule(
                artifact_kind="change_directive",
                default_mode="permission_matrix_default",
            )
        ],
        sensitivity_tags=[SensitivityTag(name="internal")],
    )


def test_permission_matrix_round_trip_via_model_dump_validate():
    matrix = PermissionMatrix(role_clusters=[_make_cluster()])
    payload = matrix.model_dump(mode="json")
    rebuilt = PermissionMatrix.model_validate(payload)
    assert rebuilt == matrix


def test_permission_matrix_json_schema_generation():
    schema = PermissionMatrix.model_json_schema()
    assert schema["type"] == "object"
    # SensitivityTag carrier is on RoleCluster (Chunk 43 re-render path).
    assert "role_clusters" in schema["properties"]


def test_permission_matrix_extra_forbid():
    with pytest.raises(ValidationError):
        PermissionMatrix.model_validate(
            {"role_clusters": [], "unexpected_field": "x"}
        )


def test_role_cluster_has_sensitivity_tags_field():
    cluster = _make_cluster()
    assert isinstance(cluster.sensitivity_tags, list)
    assert cluster.sensitivity_tags[0].name == "internal"


def test_hypothesis_confidence_band_literal_values():
    # Literal accepts only the three values; runtime check via SegmentedHypothesis.
    for band in ("strong", "moderate", "weak"):
        h = SegmentedHypothesis(cluster=_make_cluster(), confidence_band=band)
        assert h.confidence_band == band

    with pytest.raises(ValidationError):
        SegmentedHypothesis(cluster=_make_cluster(), confidence_band="excellent")


def test_drift_band_literal_values():
    for band in ("high", "medium", "low"):
        d = DriftClassification(
            person_grace_id="p-1",
            proposed_cluster_id="c-1",
            drift_band=band,
        )
        assert d.drift_band == band

    with pytest.raises(ValidationError):
        DriftClassification(
            person_grace_id="p-1", proposed_cluster_id="c-1", drift_band="critical"
        )


def test_role_cluster_hypothesis_set_discriminated_union_dispatch():
    rid = uuid4()
    eid = uuid4()
    s = SegmentedHypothesis(cluster=_make_cluster(), confidence_band="strong")
    n = NullHypothesis(rationale="No discrete clusters supported")
    result = RoleClusterHypothesisSet(
        run_id=rid,
        evidence_id=eid,
        hypotheses=[s, n],
    )
    payload = result.model_dump(mode="json")
    rebuilt = RoleClusterHypothesisSet.model_validate(payload)
    # Discriminator routes correctly.
    assert rebuilt.hypotheses[0].kind == "segmented"
    assert rebuilt.hypotheses[1].kind == "null"


def test_evidence_bundle_round_trip():
    bundle = EvidenceBundle(
        sections=[
            EvidenceSection(source="document_authorship", rows=[{"person": "p-1"}]),
            EvidenceSection(source="communications", rows=[], is_empty_placeholder=True),
        ]
    )
    payload = bundle.model_dump(mode="json")
    rebuilt = EvidenceBundle.model_validate(payload)
    assert rebuilt == bundle
    # Communications section carried as typed-but-empty placeholder.
    comm_section = next(s for s in rebuilt.sections if s.source == "communications")
    assert comm_section.is_empty_placeholder is True
    assert comm_section.rows == []


def test_allow_deny_discriminated_union():
    allow_payload = {"decision": "allow"}
    deny_payload = {
        "decision": "deny",
        "reason": {"code": "no_active_matrix", "detail": None},
    }
    a = Allow.model_validate(allow_payload)
    d = Deny.model_validate(deny_payload)
    assert a.decision == "allow"
    assert d.decision == "deny"
    assert d.reason.code == "no_active_matrix"


def test_pydantic_yaml_round_trip_for_permission_matrix():
    pydantic_yaml = pytest.importorskip("pydantic_yaml")
    matrix = PermissionMatrix(role_clusters=[_make_cluster()])
    yaml_str = pydantic_yaml.to_yaml_str(matrix)
    rebuilt = pydantic_yaml.parse_yaml_raw_as(PermissionMatrix, yaml_str)
    assert rebuilt == matrix
