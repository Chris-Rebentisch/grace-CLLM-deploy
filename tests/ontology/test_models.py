"""Tests for Ontology Management Pydantic models and enums."""

from uuid import uuid4

import pytest

from src.ontology.evidence_bundle import EvidenceBundle
from src.ontology.models import (
    CalibrationRecord,
    HumanDecision,
    OntologyVersion,
    ProposalPriority,
    ProposalStatus,
    ProposalType,
    SchemaPromotionEvent,
    SchemaProposal,
    SignalType,
    VersionSource,
    classify_tier,
)


# --- Helpers ---

def _make_version(**overrides) -> OntologyVersion:
    """Create an OntologyVersion with sensible defaults."""
    defaults = {
        "version_number": 1,
        "schema_json": {"type": "object", "properties": {}},
        "schema_modules": {"core": {}},
        "hash_chain": "abc123def456",
        "source": VersionSource.DISCOVERY,
    }
    defaults.update(overrides)
    return OntologyVersion(**defaults)


def _make_proposal(**overrides) -> SchemaProposal:
    """Create a SchemaProposal with sensible defaults."""
    defaults = {
        "proposal_type": ProposalType.ADD_ENTITY_TYPE,
        "change_tier": 2,
        "kgcl_command": "create class LegalEntity",
        "proposed_diff": {"add": ["LegalEntity"]},
        "evidence": EvidenceBundle(
            source_signal_ids=[uuid4()],
            signal_type="A",
            signal_strength=0.75,
            affected_entity_types=["LegalEntity"],
            ontology_module="test",
        ),
        "signal_type": SignalType.SIGNAL_A,
        "raw_confidence": 0.85,
        "priority": ProposalPriority.MEDIUM,
        "current_schema_version_id": uuid4(),
        "ontology_module": "test",
        "dedup_hash": "abc123",
        "overflow": False,
    }
    defaults.update(overrides)
    return SchemaProposal(**defaults)


# --- OntologyVersion Tests ---


def test_ontology_version_valid_construction():
    """OntologyVersion with all required fields constructs successfully."""
    v = _make_version()
    assert v.version_number == 1
    assert v.schema_json == {"type": "object", "properties": {}}
    assert v.hash_chain == "abc123def456"
    assert v.source == VersionSource.DISCOVERY


def test_ontology_version_defaults():
    """OntologyVersion applies default values correctly."""
    v = _make_version()
    assert v.is_active is True
    assert v.metadata_extra == {}
    assert v.patch_json is None
    assert v.diff_summary is None
    assert v.reviewer is None
    assert v.changelog is None
    assert v.kgcl_commands is None
    assert v.proposal_id is None
    assert v.previous_version_id is None


def test_ontology_version_schema_json_must_be_dict():
    """schema_json must accept a dict."""
    v = _make_version(schema_json={"definitions": {}})
    assert isinstance(v.schema_json, dict)


def test_ontology_version_hash_chain_required():
    """hash_chain is required — cannot be omitted."""
    with pytest.raises(Exception):
        OntologyVersion(
            version_number=1,
            schema_json={"type": "object"},
            schema_modules={},
            source=VersionSource.DISCOVERY,
            # hash_chain intentionally omitted
        )


def test_ontology_version_optional_fields_accept_none():
    """Optional fields accept None values."""
    v = _make_version(
        patch_json=None,
        diff_summary=None,
        reviewer=None,
        changelog=None,
        kgcl_commands=None,
        cq_coverage_snapshot=None,
        entity_type_count=None,
        relationship_type_count=None,
        promotion_gate_passed=None,
        promotion_gate_details=None,
    )
    assert v.patch_json is None
    assert v.entity_type_count is None
    assert v.promotion_gate_passed is None


# --- SchemaProposal Tests ---


def test_schema_proposal_valid_construction():
    """SchemaProposal with all required fields constructs successfully."""
    p = _make_proposal()
    assert p.proposal_type == ProposalType.ADD_ENTITY_TYPE
    assert p.change_tier == 2
    assert p.raw_confidence == 0.85


def test_schema_proposal_change_tier_bounds():
    """change_tier must be 1, 2, or 3."""
    with pytest.raises(Exception):
        _make_proposal(change_tier=0)
    with pytest.raises(Exception):
        _make_proposal(change_tier=4)
    # Valid tiers
    for tier in [1, 2, 3]:
        p = _make_proposal(change_tier=tier)
        assert p.change_tier == tier


def test_schema_proposal_raw_confidence_bounds():
    """raw_confidence must be 0.0-1.0."""
    with pytest.raises(Exception):
        _make_proposal(raw_confidence=-0.1)
    with pytest.raises(Exception):
        _make_proposal(raw_confidence=1.1)
    # Edge values
    p_low = _make_proposal(raw_confidence=0.0)
    p_high = _make_proposal(raw_confidence=1.0)
    assert p_low.raw_confidence == 0.0
    assert p_high.raw_confidence == 1.0


def test_schema_proposal_raw_confidence_accepts_none():
    """F-0042 / ISS-0053 deferral closure: raw_confidence is nullable —
    human-initiated / signal-less proposals carry None, never a fabricated
    numeric confidence (D120/D217; migration r4a_raw_confidence_nullable)."""
    p = _make_proposal(raw_confidence=None)
    assert p.raw_confidence is None


def test_schema_proposal_modification_distance_bounds():
    """modification_distance must be 0.0-1.0 when provided."""
    with pytest.raises(Exception):
        _make_proposal(modification_distance=-0.1)
    with pytest.raises(Exception):
        _make_proposal(modification_distance=1.1)
    p = _make_proposal(modification_distance=0.5)
    assert p.modification_distance == 0.5


def test_schema_proposal_default_status():
    """Default status is PENDING."""
    p = _make_proposal()
    assert p.status == ProposalStatus.PENDING


def test_schema_proposal_default_applied_autonomously():
    """Default applied_autonomously is False."""
    p = _make_proposal()
    assert p.applied_autonomously is False


# --- CalibrationRecord Tests ---


def test_calibration_record_valid_construction():
    """CalibrationRecord with all required fields constructs successfully."""
    r = CalibrationRecord(
        change_tier=1,
        confidence_band_low=0.7,
        confidence_band_high=0.9,
        approval_rate=0.95,
        sample_count=100,
        trust_score=0.92,
        autonomy_threshold=0.85,
    )
    assert r.change_tier == 1
    assert r.approval_rate == 0.95


def test_calibration_record_default_autonomy_enabled():
    """Default autonomy_enabled is False."""
    r = CalibrationRecord(
        change_tier=1,
        confidence_band_low=0.7,
        confidence_band_high=0.9,
        approval_rate=0.95,
        sample_count=100,
        trust_score=0.92,
        autonomy_threshold=0.85,
    )
    assert r.autonomy_enabled is False


def test_calibration_record_default_window_size():
    """Default window_size is 50."""
    r = CalibrationRecord(
        change_tier=1,
        confidence_band_low=0.7,
        confidence_band_high=0.9,
        approval_rate=0.95,
        sample_count=100,
        trust_score=0.92,
        autonomy_threshold=0.85,
    )
    assert r.window_size == 50


def test_calibration_record_default_risk_tolerance():
    """Default risk_tolerance is 0.95."""
    r = CalibrationRecord(
        change_tier=1,
        confidence_band_low=0.7,
        confidence_band_high=0.9,
        approval_rate=0.95,
        sample_count=100,
        trust_score=0.92,
        autonomy_threshold=0.85,
    )
    assert r.risk_tolerance == 0.95


# --- SchemaPromotionEvent Tests ---


def test_schema_promotion_event_valid_construction():
    """SchemaPromotionEvent with all required fields constructs successfully."""
    e = SchemaPromotionEvent(
        proposal_id=uuid4(),
        schema_version_before_id=uuid4(),
        proposed_schema_json={"type": "object"},
        gate_passed=True,
    )
    assert e.gate_passed is True
    assert e.cq_pass_rate is None


def test_schema_promotion_event_gate_passed_required():
    """gate_passed is required (not nullable)."""
    with pytest.raises(Exception):
        SchemaPromotionEvent(
            proposal_id=uuid4(),
            schema_version_before_id=uuid4(),
            proposed_schema_json={"type": "object"},
            # gate_passed intentionally omitted
        )


# --- Enum Tests ---


def test_version_source_values():
    """All VersionSource values are accessible."""
    # F-44: connector_sync added so connector syncs record accurate provenance.
    expected = {
        "discovery",
        "guided_review",
        "adaptive_evolution",
        "manual",
        "connector_sync",
    }
    actual = {v.value for v in VersionSource}
    assert actual == expected


def test_proposal_type_values():
    """All 10 ProposalType values are accessible."""
    assert len(ProposalType) == 10
    expected = {
        "add_entity_type", "add_relationship", "add_property",
        "split_type", "merge_types", "deprecate_type",
        "move_hierarchy", "add_synonym", "modify_property",
        "change_domain_range",
    }
    actual = {v.value for v in ProposalType}
    assert actual == expected


# --- classify_tier Tests ---


def test_classify_tier_all_mappings():
    """classify_tier maps all 10 ProposalType values correctly."""
    assert classify_tier(ProposalType.ADD_PROPERTY) == 1
    assert classify_tier(ProposalType.ADD_SYNONYM) == 1
    assert classify_tier(ProposalType.ADD_ENTITY_TYPE) == 2
    assert classify_tier(ProposalType.ADD_RELATIONSHIP) == 2
    assert classify_tier(ProposalType.MODIFY_PROPERTY) == 2
    assert classify_tier(ProposalType.SPLIT_TYPE) == 3
    assert classify_tier(ProposalType.MERGE_TYPES) == 3
    assert classify_tier(ProposalType.DEPRECATE_TYPE) == 3
    assert classify_tier(ProposalType.MOVE_HIERARCHY) == 3
    assert classify_tier(ProposalType.CHANGE_DOMAIN_RANGE) == 3
