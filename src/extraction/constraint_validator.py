"""Pre-write constraint validation of claims against the production ontology schema.

Validates entity and relationship claims before graph write. Returns
ConstraintViolation lists per claim. ERROR-severity violations cause
quarantine; WARNING allows write with annotation; INFO is logged only.
"""

from __future__ import annotations

import structlog

from src.extraction.claim_models import (
    Claim,
    ClaimStatus,
    ConstraintSeverity,
    ConstraintViolation,
)
from src.extraction.schema_utils import extract_allowed_types

log = structlog.get_logger()


def validate_claim(
    claim: Claim,
    schema: dict,
    active_schema_version: int | None = None,
    batch_claims: list[Claim] | None = None,
    low_confidence_threshold: float = 0.5,
) -> list[ConstraintViolation]:
    """Validate a single claim against the ontology schema.

    Returns a list of violations (empty means valid).
    """
    violations: list[ConstraintViolation] = []
    entity_types, predicates = extract_allowed_types(schema)

    # Rule 1: invalid_entity_type
    if claim.entity_type and entity_types and claim.entity_type not in entity_types:
        violations.append(ConstraintViolation(
            severity=ConstraintSeverity.ERROR,
            rule="invalid_entity_type",
            message=f"Entity type '{claim.entity_type}' not in schema entity types",
        ))

    # Rule 2: invalid_relationship_type
    if claim.relationship_type and predicates and claim.relationship_type not in predicates:
        violations.append(ConstraintViolation(
            severity=ConstraintSeverity.ERROR,
            rule="invalid_relationship_type",
            message=f"Relationship type '{claim.relationship_type}' not in schema relationships",
        ))

    # Rules 3-4: domain/range violations (only if schema has relationships key)
    if claim.relationship_type and "relationships" in schema:
        rel_def = schema["relationships"].get(claim.relationship_type)
        if rel_def:
            source_type = rel_def.get("source_type")
            target_type = rel_def.get("target_type")
            # Rule 3: domain_violation
            if source_type and claim.subject_type and claim.subject_type != source_type:
                violations.append(ConstraintViolation(
                    severity=ConstraintSeverity.ERROR,
                    rule="domain_violation",
                    message=f"Subject type '{claim.subject_type}' doesn't match "
                            f"expected source_type '{source_type}' for '{claim.relationship_type}'",
                ))
            # Rule 4: range_violation
            if target_type and claim.object_type and claim.object_type != target_type:
                violations.append(ConstraintViolation(
                    severity=ConstraintSeverity.ERROR,
                    rule="range_violation",
                    message=f"Object type '{claim.object_type}' doesn't match "
                            f"expected target_type '{target_type}' for '{claim.relationship_type}'",
                ))
    elif claim.relationship_type and "relationships" not in schema:
        log.info(
            "constraint_validator.skip_domain_range",
            msg="Schema has no 'relationships' key; skipping domain/range checks",
        )

    # Rule 5: temporal_inversion
    # Checks valid_from/valid_to passed via tagged_temporal dict on the claim
    # (set by pipeline Step 12 before graph write). Falls back to properties_json.
    vf = claim.properties_json.get("_tagged_valid_from")
    vt = claim.properties_json.get("_tagged_valid_to")
    if vf is not None and vt is not None and vf > vt:
        violations.append(ConstraintViolation(
            severity=ConstraintSeverity.ERROR,
            rule="temporal_inversion",
            message="valid_from is after valid_to",
        ))

    # Rule 6: schema_version_mismatch
    if (
        active_schema_version is not None
        and claim.schema_version is not None
        and claim.schema_version != active_schema_version
    ):
        violations.append(ConstraintViolation(
            severity=ConstraintSeverity.ERROR,
            rule="schema_version_mismatch",
            message=f"Claim schema_version {claim.schema_version} differs from "
                    f"active version {active_schema_version}",
        ))

    # Rule 7: unresolvable_endpoint — owned by graph writer, not validator

    # Rule 8: missing_name (entity claims only)
    if claim.entity_type and "name" not in claim.properties_json:
        violations.append(ConstraintViolation(
            severity=ConstraintSeverity.WARNING,
            rule="missing_name",
            message="Entity claim has no 'name' in properties_json",
        ))

    # Rule 9: short_label
    name = claim.properties_json.get("name", claim.subject_name)
    if claim.entity_type and name and len(name) < 2:
        violations.append(ConstraintViolation(
            severity=ConstraintSeverity.WARNING,
            rule="short_label",
            message=f"Entity name '{name}' is shorter than 2 characters",
        ))

    # Rule 10: low_confidence
    if claim.confidence is not None and claim.confidence < low_confidence_threshold:
        violations.append(ConstraintViolation(
            severity=ConstraintSeverity.WARNING,
            rule="low_confidence",
            message=f"Confidence {claim.confidence:.3f} below threshold {low_confidence_threshold}",
        ))

    # Rule 11: missing_expected_properties (entity claims only)
    if claim.entity_type and "entity_types" in schema:
        type_def = schema["entity_types"].get(claim.entity_type, {})
        required_props = type_def.get("required", [])
        if required_props:
            missing = [p for p in required_props if p not in claim.properties_json]
            if missing:
                violations.append(ConstraintViolation(
                    severity=ConstraintSeverity.WARNING,
                    rule="missing_expected_properties",
                    message=f"Missing required properties: {missing}",
                ))

    # Rule 13: enrichment_opportunity (entity claims only)
    if claim.entity_type and "entity_types" in schema:
        type_def = schema["entity_types"].get(claim.entity_type, {})
        all_props = set(type_def.get("properties", {}).keys())
        populated = set(claim.properties_json.keys())
        optional_missing = all_props - populated
        if optional_missing:
            violations.append(ConstraintViolation(
                severity=ConstraintSeverity.INFO,
                rule="enrichment_opportunity",
                message=f"Optional properties not populated: {sorted(optional_missing)}",
            ))

    # Rule 14: generic_description
    desc = claim.properties_json.get("description", "")
    if claim.entity_type and isinstance(desc, str) and 0 < len(desc) < 10:
        violations.append(ConstraintViolation(
            severity=ConstraintSeverity.INFO,
            rule="generic_description",
            message=f"Entity description is only {len(desc)} characters",
        ))

    # Rule 15: deprecated_entity_type (F-17, validation run, 2026-07-01)
    # A claim instantiating a DEPRECATED ontology type used to auto-accept
    # silently — the validator only checked type-inventory membership, so
    # detection was deferred entirely to Signal D's slow trend analytics and the
    # deprecated fact flowed into the graph unreviewed. Raise an ERROR (which
    # quarantines) so a human confirms the intentional use of a deprecated type.
    # Deprecation may be marked as a dedicated flag (`deprecated` / `_deprecated`)
    # or noted in the type description ("description or dedicated flag").
    if claim.entity_type and "entity_types" in schema:
        type_def = schema["entity_types"].get(claim.entity_type, {})
        if isinstance(type_def, dict) and _is_deprecated_type(type_def):
            violations.append(ConstraintViolation(
                severity=ConstraintSeverity.ERROR,
                rule="deprecated_entity_type",
                message=f"Entity type '{claim.entity_type}' is marked deprecated "
                        f"in the active schema; use requires human confirmation",
            ))

    return violations


def _is_deprecated_type(type_def: dict) -> bool:
    """True when a schema type-def marks the type deprecated (F-17).

    Accepts a dedicated boolean flag (``deprecated`` or ``_deprecated``) or a
    ``[deprecated]`` / "deprecated" marker in the description text.
    """
    if type_def.get("deprecated") is True or type_def.get("_deprecated") is True:
        return True
    desc = type_def.get("description")
    return isinstance(desc, str) and "deprecated" in desc.lower()


def validate_batch(
    claims: list[Claim],
    schema: dict,
    active_schema_version: int | None = None,
    low_confidence_threshold: float = 0.5,
) -> dict[str, list[ConstraintViolation]]:
    """Validate all claims in a batch.

    Returns {claim_id: [violations]}. Calls validate_claim per claim,
    then runs batch-level temporal overlap detection (Rule 12).
    Also quarantines claims with ERROR-severity violations.
    """
    result: dict[str, list[ConstraintViolation]] = {}

    for claim in claims:
        violations = validate_claim(
            claim, schema, active_schema_version,
            batch_claims=claims,
            low_confidence_threshold=low_confidence_threshold,
        )
        result[claim.claim_id] = violations

        # Quarantine claims with ERROR violations
        has_error = any(v.severity == ConstraintSeverity.ERROR for v in violations)
        if has_error:
            claim.status = ClaimStatus.QUARANTINED
            claim.decision_source = "validator"
        # Attach violations to claim
        claim.constraint_violations = violations

    # Rule 12: temporal_overlap_requires_review (batch-level)
    _detect_temporal_overlaps(claims, result)

    return result


def _detect_temporal_overlaps(
    claims: list[Claim],
    violations_map: dict[str, list[ConstraintViolation]],
) -> None:
    """Detect temporal overlaps within a batch (Rule 12, D101).

    Groups claims by (subject_name, predicate, object_name). For groups
    with 2+ claims that have non-null temporal windows, check for overlap.
    Both claims get WARNING. Status stays auto_accepted.
    """
    from collections import defaultdict

    groups: dict[tuple, list[Claim]] = defaultdict(list)
    for claim in claims:
        key = (claim.subject_name, claim.predicate, claim.object_name)
        groups[key].append(claim)

    for key, group in groups.items():
        temporal_claims = [
            c for c in group
            if (c.properties_json.get("_tagged_valid_from") is not None
                or c.properties_json.get("_tagged_valid_to") is not None)
        ]
        if len(temporal_claims) < 2:
            continue

        for i in range(len(temporal_claims)):
            for j in range(i + 1, len(temporal_claims)):
                a, b = temporal_claims[i], temporal_claims[j]
                if _windows_overlap(a, b):
                    violation = ConstraintViolation(
                        severity=ConstraintSeverity.WARNING,
                        rule="temporal_overlap_requires_review",
                        message=f"Temporal overlap with claim {b.claim_id}" if a != b else "",
                    )
                    violations_map.setdefault(a.claim_id, []).append(violation)
                    a.constraint_violations.append(violation)

                    violation_b = ConstraintViolation(
                        severity=ConstraintSeverity.WARNING,
                        rule="temporal_overlap_requires_review",
                        message=f"Temporal overlap with claim {a.claim_id}",
                    )
                    violations_map.setdefault(b.claim_id, []).append(violation_b)
                    b.constraint_violations.append(violation_b)


def _windows_overlap(a: Claim, b: Claim) -> bool:
    """Check if two claims' temporal windows overlap."""
    a_from = a.properties_json.get("_tagged_valid_from")
    a_to = a.properties_json.get("_tagged_valid_to")
    b_from = b.properties_json.get("_tagged_valid_from")
    b_to = b.properties_json.get("_tagged_valid_to")

    # Both need at least one temporal bound to overlap
    if a_from is None and a_to is None:
        return False
    if b_from is None and b_to is None:
        return False

    # Open-ended ranges: treat None as -inf / +inf
    # Check: a_start <= b_end AND b_start <= a_end
    a_start = a_from if a_from is not None else b_from  # treat as before b
    a_end = a_to if a_to is not None else b_to  # treat as after b
    b_start = b_from if b_from is not None else a_from
    b_end = b_to if b_to is not None else a_to

    if a_start is None or a_end is None or b_start is None or b_end is None:
        # If we still have None, both have the same bound set — they overlap
        return True

    return a_start <= b_end and b_start <= a_end
