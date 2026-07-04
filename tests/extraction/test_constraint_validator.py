"""Tests for pre-write constraint validation of claims."""

from datetime import datetime, timezone

from src.extraction.claim_models import (
    Claim,
    ClaimStatus,
    ConstraintSeverity,
)
from src.extraction.constraint_validator import validate_batch, validate_claim

# -- Shared fixtures --

SCHEMA = {
    "entity_types": {
        "Legal_Entity": {
            "properties": {
                "name": {"data_type": "string"},
                "jurisdiction": {"data_type": "string"},
                "description": {"data_type": "string"},
            },
            "required": ["name"],
        },
        "Person": {
            "properties": {
                "name": {"data_type": "string"},
            },
        },
    },
    "relationships": {
        "owns": {
            "source_type": "Legal_Entity",
            "target_type": "Legal_Entity",
            "properties": {"ownership_percentage": {"data_type": "float"}},
        },
        "employs": {
            "source_type": "Legal_Entity",
            "target_type": "Person",
            "properties": {},
        },
    },
}


def _entity_claim(**overrides) -> Claim:
    defaults = dict(
        entity_type="Legal_Entity",
        subject_name="Acme Corp",
        subject_type="Legal_Entity",
        predicate="entity",
        properties_json={"name": "Acme Corp"},
        confidence=0.85,
        schema_version=1,
    )
    defaults.update(overrides)
    return Claim(**defaults)


def _rel_claim(**overrides) -> Claim:
    defaults = dict(
        relationship_type="owns",
        subject_name="Acme Corp",
        subject_type="Legal_Entity",
        predicate="owns",
        object_name="Sub Corp",
        object_type="Legal_Entity",
        properties_json={},
        confidence=0.85,
        schema_version=1,
    )
    defaults.update(overrides)
    return Claim(**defaults)


class TestDeprecatedTypeRule:
    """F-17: a claim on a deprecated ontology type must ERROR (→ quarantine),
    not silently auto-accept."""

    def test_deprecated_flag_triggers_error(self):
        schema = {
            "entity_types": {
                "Property": {"properties": {"name": {}}, "required": ["name"], "deprecated": True},
            }
        }
        claim = _entity_claim(entity_type="Property", subject_type="Property")
        violations = validate_claim(claim, schema)
        rules = {v.rule: v for v in violations}
        assert "deprecated_entity_type" in rules
        assert rules["deprecated_entity_type"].severity == ConstraintSeverity.ERROR

    def test_deprecated_in_description_triggers_error(self):
        schema = {
            "entity_types": {
                "Property": {
                    "properties": {"name": {}},
                    "required": ["name"],
                    "description": "[DEPRECATED] superseded by Real_Property",
                },
            }
        }
        claim = _entity_claim(entity_type="Property", subject_type="Property")
        errors = [
            v for v in validate_claim(claim, schema)
            if v.rule == "deprecated_entity_type"
        ]
        assert len(errors) == 1

    def test_non_deprecated_type_no_deprecation_error(self):
        claim = _entity_claim()  # Legal_Entity, not deprecated
        errors = [
            v for v in validate_claim(claim, SCHEMA)
            if v.rule == "deprecated_entity_type"
        ]
        assert errors == []


class TestValidateClaim:
    def test_valid_entity_claim_passes(self):
        claim = _entity_claim()
        violations = validate_claim(claim, SCHEMA, active_schema_version=1)
        errors = [v for v in violations if v.severity == ConstraintSeverity.ERROR]
        assert len(errors) == 0

    def test_invalid_entity_type_error(self):
        claim = _entity_claim(entity_type="Nonexistent_Type")
        violations = validate_claim(claim, SCHEMA)
        error_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.ERROR]
        assert "invalid_entity_type" in error_rules

    def test_invalid_relationship_type_error(self):
        claim = _rel_claim(relationship_type="nonexistent_rel")
        violations = validate_claim(claim, SCHEMA)
        error_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.ERROR]
        assert "invalid_relationship_type" in error_rules

    def test_domain_violation_error(self):
        claim = _rel_claim(subject_type="Person")
        violations = validate_claim(claim, SCHEMA)
        error_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.ERROR]
        assert "domain_violation" in error_rules

    def test_range_violation_error(self):
        claim = _rel_claim(relationship_type="employs", object_type="Legal_Entity")
        violations = validate_claim(claim, SCHEMA)
        error_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.ERROR]
        assert "range_violation" in error_rules

    def test_temporal_inversion_error(self):
        claim = _entity_claim(properties_json={
            "name": "Acme Corp",
            "_tagged_valid_from": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "_tagged_valid_to": datetime(2024, 1, 1, tzinfo=timezone.utc),
        })
        violations = validate_claim(claim, SCHEMA)
        error_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.ERROR]
        assert "temporal_inversion" in error_rules

    def test_schema_version_mismatch_error(self):
        claim = _entity_claim(schema_version=2)
        violations = validate_claim(claim, SCHEMA, active_schema_version=1)
        error_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.ERROR]
        assert "schema_version_mismatch" in error_rules

    def test_missing_name_warning(self):
        claim = _entity_claim(properties_json={"jurisdiction": "US"})
        violations = validate_claim(claim, SCHEMA)
        warn_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.WARNING]
        assert "missing_name" in warn_rules

    def test_short_label_warning(self):
        claim = _entity_claim(
            subject_name="X",
            properties_json={"name": "X"},
        )
        violations = validate_claim(claim, SCHEMA)
        warn_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.WARNING]
        assert "short_label" in warn_rules

    def test_low_confidence_warning(self):
        claim = _entity_claim(confidence=0.3)
        violations = validate_claim(claim, SCHEMA)
        warn_rules = [v.rule for v in violations if v.severity == ConstraintSeverity.WARNING]
        assert "low_confidence" in warn_rules


class TestTemporalOverlap:
    def test_temporal_overlap_warning(self):
        c1 = _rel_claim(properties_json={
            "_tagged_valid_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "_tagged_valid_to": datetime(2024, 12, 31, tzinfo=timezone.utc),
        })

        c2 = _rel_claim(properties_json={
            "_tagged_valid_from": datetime(2024, 6, 1, tzinfo=timezone.utc),
            "_tagged_valid_to": datetime(2025, 6, 1, tzinfo=timezone.utc),
        })

        result = validate_batch([c1, c2], SCHEMA)

        # Both should have temporal_overlap_requires_review warning
        c1_overlap = [
            v for v in result[c1.claim_id]
            if v.rule == "temporal_overlap_requires_review"
        ]
        c2_overlap = [
            v for v in result[c2.claim_id]
            if v.rule == "temporal_overlap_requires_review"
        ]
        assert len(c1_overlap) > 0
        assert len(c2_overlap) > 0
        # Both should remain auto_accepted
        assert c1.status == ClaimStatus.AUTO_ACCEPTED
        assert c2.status == ClaimStatus.AUTO_ACCEPTED


class TestValidateBatch:
    def test_validate_batch_aggregates(self):
        good_claim = _entity_claim()
        bad_claim = _entity_claim(entity_type="Nonexistent")
        result = validate_batch([good_claim, bad_claim], SCHEMA)

        assert good_claim.claim_id in result
        assert bad_claim.claim_id in result
        # Bad claim should be quarantined
        assert bad_claim.status == ClaimStatus.QUARANTINED
        assert bad_claim.decision_source == "validator"
        # Good claim stays accepted
        assert good_claim.status == ClaimStatus.AUTO_ACCEPTED
