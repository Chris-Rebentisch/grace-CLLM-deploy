"""D291 — Change Directive discriminated-union round-trip tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from src.change_directives.models import (
    ChangeDirective,
    ChangeDirectivePatchBody,
    DirectiveStatus,
    EvidenceCriterion,
    OperationalAdjustment,
    StrategicInitiative,
)


_ADAPTER: TypeAdapter[ChangeDirective] = TypeAdapter(ChangeDirective)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_oa_payload() -> dict:
    return {
        "tier": "Operational_Adjustment",
        "directive_id": str(uuid4()),
        "title": "Switch retainer terms to net-60",
        "description": "Reflect updated cash policy.",
        "authored_by": str(uuid4()),
        "authored_at": _now().isoformat(),
        "status": "draft",
        "status_updated_at": _now().isoformat(),
        "visibility": "permission_matrix_default",
        "affected_segments": ["finance"],
    }


def _make_si_payload() -> dict:
    crit_id = str(uuid4())
    directive_id = str(uuid4())
    return {
        "tier": "Strategic_Initiative",
        "directive_id": directive_id,
        "title": "Move to evidence-anchored OKRs",
        "description": "Multi-quarter realignment.",
        "authored_by": str(uuid4()),
        "authored_at": _now().isoformat(),
        "status": "draft",
        "status_updated_at": _now().isoformat(),
        "visibility": "permission_matrix_default",
        "affected_segments": ["finance", "ops"],
        "target_state_description": "All quarterly reviews cite Graph evidence.",
        "evidence_criteria": [
            {
                "criterion_id": crit_id,
                "directive_id": directive_id,
                "natural_language": "Quarterly review cites at least 3 graph entities",
                "compilation_status": "proposed",
                "created_at": _now().isoformat(),
                "updated_at": _now().isoformat(),
            }
        ],
    }


def test_operational_adjustment_round_trip() -> None:
    payload = _make_oa_payload()
    obj = _ADAPTER.validate_python(payload)
    assert isinstance(obj, OperationalAdjustment)
    assert obj.tier == "Operational_Adjustment"
    assert obj.status is DirectiveStatus.DRAFT
    dumped = obj.model_dump(mode="json")
    assert dumped["tier"] == "Operational_Adjustment"


def test_strategic_initiative_round_trip_with_criterion() -> None:
    payload = _make_si_payload()
    obj = _ADAPTER.validate_python(payload)
    assert isinstance(obj, StrategicInitiative)
    assert obj.tier == "Strategic_Initiative"
    assert len(obj.evidence_criteria) == 1
    assert isinstance(obj.evidence_criteria[0], EvidenceCriterion)


def test_strategic_initiative_requires_segments_and_criteria() -> None:
    payload = _make_si_payload()
    payload["affected_segments"] = []  # SI tightens to min_length=1
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload)
    payload2 = _make_si_payload()
    payload2["evidence_criteria"] = []
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload2)


def test_extra_forbid_rejects_unknown_fields() -> None:
    payload = _make_oa_payload()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload)


def test_patch_body_forbids_status_and_visibility_fields() -> None:
    """``ChangeDirectivePatchBody.extra="forbid"`` rejects forbidden keys."""
    for forbidden_key in (
        "status",
        "visibility",
        "visibility_named_list",
        "visibility_role_cluster",
        "authored_by",
        "authored_at",
        "directive_id",
        "superseded_by_directive_id",
    ):
        with pytest.raises(ValidationError):
            ChangeDirectivePatchBody.model_validate({forbidden_key: "x"})

    # Allowlisted fields validate cleanly.
    ChangeDirectivePatchBody.model_validate(
        {"title": "Updated", "description": "Updated body"}
    )
