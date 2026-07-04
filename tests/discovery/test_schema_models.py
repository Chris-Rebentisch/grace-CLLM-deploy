"""Tests for schema extraction Pydantic models."""

from uuid import uuid4

from src.discovery.schema_models import (
    PassOutput,
    ProposedEntityType,
    ProposedProperty,
    ProposedRelationship,
    SchemaExtractionRun,
    Stage1Output,
    Stage1RelSummary,
    Stage1TypeSummary,
)


# --- Stage 1 model tests ---


def test_stage1_type_summary():
    """Stage1TypeSummary with all fields."""
    ts = Stage1TypeSummary(
        name="Legal_Entity",
        parent_type=None,
        description="An entity with legal standing",
        domain="corporate_structure",
        answerable_cqs=["cq_001", "cq_005"],
        seed_alignment="LegalEntity",
    )
    assert ts.name == "Legal_Entity"
    assert ts.seed_alignment == "LegalEntity"
    assert len(ts.answerable_cqs) == 2


def test_stage1_type_summary_defaults():
    """Stage1TypeSummary with minimal fields."""
    ts = Stage1TypeSummary(name="Test", description="A test type")
    assert ts.parent_type is None
    assert ts.domain == "other"
    assert ts.answerable_cqs == []
    assert ts.seed_alignment is None


def test_stage1_rel_summary():
    """Stage1RelSummary with all fields."""
    rs = Stage1RelSummary(
        name="covers",
        source_type="Policy",
        target_type="Property",
        description="Coverage relationship",
        answerable_cqs=["cq_002"],
        seed_alignment="covers",
    )
    assert rs.source_type == "Policy"


def test_stage1_output():
    """Stage1Output combines types and relationships."""
    output = Stage1Output(
        entity_types=[
            Stage1TypeSummary(name="A", description="Type A"),
            Stage1TypeSummary(name="B", description="Type B"),
        ],
        relationships=[
            Stage1RelSummary(name="links", source_type="A", target_type="B", description="Link"),
        ],
    )
    assert len(output.entity_types) == 2
    assert len(output.relationships) == 1


# --- Existing model tests ---


def test_proposed_property_validation():
    """Test ProposedProperty with all fields populated."""
    prop = ProposedProperty(
        name="effective_date",
        data_type="datetime",
        description="When the policy becomes effective",
        required=True,
        answerable_cqs=["abc12345", "def67890"],
    )
    assert prop.name == "effective_date"
    assert prop.data_type == "datetime"
    assert prop.required is True
    assert len(prop.answerable_cqs) == 2


def test_proposed_property_defaults():
    """Test ProposedProperty with minimal fields."""
    prop = ProposedProperty(name="notes", data_type="string")
    assert prop.description == ""
    assert prop.required is False
    assert prop.answerable_cqs == []


def test_proposed_entity_type_validation():
    """Test ProposedEntityType with all fields including nested properties."""
    et = ProposedEntityType(
        name="Insurance_Policy",
        parent_type="Legal_Document",
        description="An insurance policy covering specific risks",
        domain="insurance",
        properties=[
            ProposedProperty(name="policy_number", data_type="string", required=True),
            ProposedProperty(name="premium", data_type="float"),
        ],
        answerable_cqs=["abc12345"],
        evidence_documents=["policy_master.pdf"],
        seed_alignment="InsurancePolicy",
    )
    assert et.name == "Insurance_Policy"
    assert len(et.properties) == 2


def test_proposed_entity_type_defaults():
    """Test ProposedEntityType with minimal fields."""
    et = ProposedEntityType(name="Generic_Entity", description="A generic entity")
    assert et.parent_type is None
    assert et.domain == "other"
    assert et.properties == []


def test_proposed_relationship_richness_hints():
    """Test ProposedRelationship with different richness_hint values."""
    for hint in ["simple", "attributed", "reified"]:
        rel = ProposedRelationship(
            name="covers",
            source_type="Insurance_Policy",
            target_type="Property",
            description="Policy covers a property",
            richness_hint=hint,
            answerable_cqs=["cq_001"],
        )
        assert rel.richness_hint == hint


def test_proposed_relationship_with_edge_properties():
    """Test ProposedRelationship with edge_properties."""
    rel = ProposedRelationship(
        name="employs",
        source_type="Company",
        target_type="Person",
        description="Employment relationship",
        richness_hint="attributed",
        edge_properties=[
            ProposedProperty(name="start_date", data_type="datetime", required=True),
        ],
        answerable_cqs=["abc12345"],
    )
    assert len(rel.edge_properties) == 1


def test_pass_output_construction():
    """Test PassOutput with sample data."""
    po = PassOutput(
        pass_name="top_down",
        domain="insurance",
        entity_types=[
            ProposedEntityType(name="Policy", description="An insurance policy", answerable_cqs=["cq_001"]),
        ],
        relationships=[
            ProposedRelationship(name="covers", source_type="Policy", target_type="Property", description="Covers", answerable_cqs=["cq_002"]),
        ],
        total_cq_coverage=0.75,
        success=True,
    )
    assert po.pass_name == "top_down"
    assert len(po.entity_types) == 1


def test_pass_output_failure():
    """Test PassOutput representing a failed pass."""
    po = PassOutput(pass_name="bottom_up", domain="legal", success=False, error_message="Stage 1 failed")
    assert po.success is False
    assert po.entity_types == []


def test_schema_extraction_run_construction():
    """Test SchemaExtractionRun with pass outputs."""
    run = SchemaExtractionRun(model="qwen2.5:7b", provider="ollama")
    assert run.status == "running"
    assert run.run_id
    assert run.started_at


def test_answerable_cqs_accepts_string_ids():
    """Test that answerable_cqs accepts list of string IDs."""
    cq_ids = [str(uuid4())[:8] for _ in range(5)]
    et = ProposedEntityType(name="Test_Type", description="Test", answerable_cqs=cq_ids)
    assert len(et.answerable_cqs) == 5
