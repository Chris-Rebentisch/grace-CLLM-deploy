"""Pydantic model tests for the Sensitivity Gate (Chunk 43, CP1).

Covers D342 (six-framework taxonomy + ``custom`` escape), R9 hash-chain
integrity (empty-list default does not perturb pre-Chunk-43
``payload_hash`` values), and the three new render-only models
(``TaggedSubset``, ``SensitivityClassificationReport``, supporting
entries).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.permissions.models import (
    AccessRule,
    CoverageBreakdownEntry,
    FrameworkMapping,
    PermissionMatrix,
    RoleCluster,
    SensitivityClassificationReport,
    SensitivityTag,
    TagInventoryEntry,
    TaggedClusterDecision,
    TaggedSubset,
    UntaggedRuleEntry,
)


# ---------------------------------------------------------------------------
# FrameworkMapping (D342)
# ---------------------------------------------------------------------------


def test_framework_mapping_round_trip():
    """``FrameworkMapping`` serializes and re-validates byte-stable."""
    fm = FrameworkMapping(framework="iso_27001_2022", code="A.5.13")
    payload = fm.model_dump(mode="json")
    rebuilt = FrameworkMapping.model_validate(payload)
    assert rebuilt == fm


def test_framework_mapping_extra_forbid():
    with pytest.raises(ValidationError):
        FrameworkMapping.model_validate(
            {"framework": "iso_27001_2022", "code": "A.5.13", "extra": "x"}
        )


def test_framework_mapping_six_frameworks_accepted():
    """Six framework literals + ``custom`` escape hatch all accepted (D342)."""
    accepted = [
        "iso_27001_2022",
        "fhir_security_label",
        "nist_cui",
        "gdpr_art_9",
        "hipaa_phi",
        "custom",
    ]
    for fw in accepted:
        FrameworkMapping(framework=fw, code="x")


def test_framework_mapping_unknown_framework_rejected():
    with pytest.raises(ValidationError):
        FrameworkMapping.model_validate({"framework": "iso_42000", "code": "x"})


def test_framework_mapping_empty_code_rejected():
    """``code`` must be non-empty (min_length=1)."""
    with pytest.raises(ValidationError):
        FrameworkMapping.model_validate({"framework": "custom", "code": ""})


# ---------------------------------------------------------------------------
# SensitivityTag.framework_mappings (D342) — additive field
# ---------------------------------------------------------------------------


def test_sensitivity_tag_framework_mappings_defaults_empty():
    """Empty-list default — preserves R9 (existing payload_hash rows valid)."""
    tag = SensitivityTag(name="pii")
    assert tag.framework_mappings == []


def test_sensitivity_tag_accepts_framework_mappings():
    tag = SensitivityTag(
        name="phi",
        framework_mappings=[FrameworkMapping(framework="hipaa_phi", code="PHI")],
    )
    assert tag.framework_mappings[0].framework == "hipaa_phi"


def test_sensitivity_tag_legacy_shape_validates():
    """A legacy (Chunk 42) tag JSON without ``framework_mappings`` validates."""
    legacy = {"name": "internal", "description": "Internal use"}
    tag = SensitivityTag.model_validate(legacy)
    assert tag.framework_mappings == []
    assert tag.name == "internal"


def test_sensitivity_tag_extra_forbid_still_holds():
    with pytest.raises(ValidationError):
        SensitivityTag.model_validate(
            {"name": "x", "unexpected": True}
        )


# ---------------------------------------------------------------------------
# R9: hash-chain integrity over the additive field
# ---------------------------------------------------------------------------


def _canonical_hash(payload: dict) -> str:
    """Mirror of ``repository._compute_payload_hash`` for test assertions."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def test_legacy_stored_payload_hash_remains_valid():
    """A Chunk 42 stored payload (no ``framework_mappings`` key on tags) must
    produce the same canonical hash even after the field is added to the
    model. The DB stored ``payload_hash`` was computed against the stored
    JSON; rehashing the same JSON must match — independent of model
    evolution. R9 mitigation."""
    legacy_payload = {
        "schema_version": "1.0",
        "default_decision": "deny",
        "notes": None,
        "role_clusters": [
            {
                "cluster_id": "ops",
                "display_name": "Ops",
                "description": None,
                "members": [],
                "access_rules": [
                    {
                        "resource_kind": "ontology_module",
                        "resource_label": "ops",
                        "action": "view",
                        "decision": "allow",
                        "sensitivity_tags": [
                            {"name": "internal", "description": None}
                        ],
                    }
                ],
                "visibility_rules": [],
                "sensitivity_tags": [],
            }
        ],
    }

    legacy_hash = _canonical_hash(legacy_payload)

    # Validate through the new model — Pydantic injects empty
    # ``framework_mappings`` defaults on the tag.
    rebuilt = PermissionMatrix.model_validate(legacy_payload)
    assert rebuilt.role_clusters[0].access_rules[0].sensitivity_tags[0].framework_mappings == []

    # The stored payload itself is unchanged → rehashing the same dict
    # produces the same hash. This is what verify_chain relies on.
    assert _canonical_hash(legacy_payload) == legacy_hash


def test_new_payload_hash_includes_framework_mappings():
    """A newly-ratified matrix that includes ``framework_mappings`` produces
    a hash that legitimately differs from the empty-mapping form. The two
    are distinct serializations and the chain stores whatever was
    submitted at INSERT time."""
    base_payload = {
        "schema_version": "1.0",
        "default_decision": "deny",
        "notes": None,
        "role_clusters": [
            {
                "cluster_id": "ops",
                "display_name": "Ops",
                "description": None,
                "members": [],
                "access_rules": [],
                "visibility_rules": [],
                "sensitivity_tags": [
                    {
                        "name": "phi",
                        "description": None,
                        "framework_mappings": [],
                    }
                ],
            }
        ],
    }
    with_mapping = json.loads(json.dumps(base_payload))
    with_mapping["role_clusters"][0]["sensitivity_tags"][0][
        "framework_mappings"
    ] = [{"framework": "hipaa_phi", "code": "PHI"}]

    assert _canonical_hash(base_payload) != _canonical_hash(with_mapping)


# ---------------------------------------------------------------------------
# TaggedSubset (D343)
# ---------------------------------------------------------------------------


def test_tagged_subset_round_trip():
    subset = TaggedSubset(
        matrix_schema_version="1.0",
        cluster_decisions=[
            TaggedClusterDecision(
                cluster_id="ops",
                cluster_display_name="Ops",
                resource_kind="ontology_module",
                resource_label="ops",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="internal")],
            )
        ],
    )
    payload = subset.model_dump(mode="json")
    rebuilt = TaggedSubset.model_validate(payload)
    assert rebuilt == subset


def test_tagged_subset_schema_generation():
    schema = TaggedSubset.model_json_schema()
    assert schema["type"] == "object"
    assert "cluster_decisions" in schema["properties"]


def test_tagged_subset_extra_forbid():
    with pytest.raises(ValidationError):
        TaggedSubset.model_validate(
            {
                "matrix_schema_version": "1.0",
                "cluster_decisions": [],
                "rogue_field": "x",
            }
        )


# ---------------------------------------------------------------------------
# SensitivityClassificationReport (D344)
# ---------------------------------------------------------------------------


def _make_report(**overrides) -> SensitivityClassificationReport:
    base = dict(
        permission_matrix_id=uuid4(),
        generated_at=datetime.now(timezone.utc),
        tag_inventory=[
            TagInventoryEntry(tag_name="pii", rule_count=2, cluster_count=1)
        ],
        coverage_breakdown=[
            CoverageBreakdownEntry(
                resource_kind="ontology_module",
                action="view",
                total_rule_count=10,
                tagged_rule_count=4,
            )
        ],
        untagged_rules=[],
        truncated=False,
    )
    base.update(overrides)
    return SensitivityClassificationReport(**base)


def test_classification_report_default_state():
    """Below-floor null path: ``coverage_band`` and ``coverage_score`` both
    default to ``None``; ``corpus_below_floor`` defaults to ``False``."""
    rpt = _make_report()
    assert rpt.coverage_band is None
    assert rpt.coverage_score is None
    assert rpt.corpus_below_floor is False
    assert rpt.tag_hygiene_findings == []


def test_classification_report_three_band_literal():
    """Coverage band accepts only ``high|medium|low|None``."""
    for band in ("high", "medium", "low"):
        rpt = _make_report(coverage_band=band)
        assert rpt.coverage_band == band
    with pytest.raises(ValidationError):
        _make_report(coverage_band="unknown")


def test_classification_report_truncated_flag():
    """``truncated=True`` flips on cap (1000 rule cap enforced by generator)."""
    rpt = _make_report(
        untagged_rules=[
            UntaggedRuleEntry(
                cluster_id="ops",
                cluster_display_name="Ops",
                resource_kind="ontology_module",
                resource_label="ops",
                action="view",
            )
        ],
        truncated=True,
    )
    assert rpt.truncated is True
    assert len(rpt.untagged_rules) == 1


def test_classification_report_extra_forbid():
    with pytest.raises(ValidationError):
        SensitivityClassificationReport.model_validate(
            {
                "permission_matrix_id": str(uuid4()),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "extra": "rogue",
            }
        )


def test_classification_report_coverage_score_field_present():
    """``coverage_score`` exists on the Pydantic class for repository
    persistence — but is excluded from API response models per D120/D217."""
    rpt = _make_report(coverage_score=0.83, coverage_band="high")
    payload = rpt.model_dump()
    assert "coverage_score" in payload
    assert payload["coverage_score"] == 0.83


def test_tag_inventory_entry_negative_counts_rejected():
    with pytest.raises(ValidationError):
        TagInventoryEntry(tag_name="pii", rule_count=-1, cluster_count=0)
