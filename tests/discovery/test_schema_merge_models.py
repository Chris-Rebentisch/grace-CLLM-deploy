"""Tests for schema merge Pydantic models."""

from src.discovery.schema_merge_models import (
    CQCoverageEntry,
    MergedEntityType,
    MergedProperty,
    MergedRelationship,
    SchemaMergeRun,
    SeedSchema,
)


def test_merged_property_with_source_passes():
    """MergedProperty tracks which passes proposed it."""
    prop = MergedProperty(
        name="effective_date",
        data_type="datetime",
        description="When policy becomes effective",
        required=True,
        answerable_cqs=["abc12345"],
        source_passes=["top_down", "bottom_up"],
    )
    assert prop.name == "effective_date"
    assert len(prop.source_passes) == 2
    assert "top_down" in prop.source_passes


def test_merged_entity_type_all_provenance_values():
    """Test MergedEntityType with each of the seven provenance values."""
    provenance_values = [
        "seed+3pass", "seed+2pass", "seed+1pass", "seed_only",
        "3pass_novel", "2pass_novel", "1pass_only",
    ]
    for prov in provenance_values:
        et = MergedEntityType(
            name="Test_Type",
            description="Test",
            provenance=prov,
            confidence=0.5,
        )
        assert et.provenance == prov


def test_merged_entity_type_full_fields():
    """MergedEntityType with all fields populated."""
    et = MergedEntityType(
        name="Legal_Entity",
        alternative_names=["Corporate_Entity", "Organization"],
        parent_type=None,
        description="An entity with legal standing",
        domain="corporate_structure",
        properties=[
            MergedProperty(name="jurisdiction", data_type="string", source_passes=["top_down", "seed"]),
        ],
        provenance="seed+3pass",
        confidence=1.0,
        source_passes=["top_down", "bottom_up", "middle_out", "seed"],
        seed_source="fibo",
        seed_type_name="LegalEntity",
        answerable_cqs=["cq_001", "cq_005"],
        evidence_document_count=3,
    )
    assert et.name == "Legal_Entity"
    assert len(et.alternative_names) == 2
    assert et.seed_source == "fibo"
    assert et.evidence_document_count == 3


def test_merged_relationship_richness_tiers():
    """Test MergedRelationship with each richness tier."""
    for tier in ["simple", "attributed", "reified"]:
        rel = MergedRelationship(
            name="covers",
            source_type="Policy",
            target_type="Property",
            description="Coverage relationship",
            richness_tier=tier,
            provenance="2pass_novel",
            confidence=0.67,
        )
        assert rel.richness_tier == tier


def test_merged_relationship_with_edge_properties():
    """Attributed relationships include edge properties."""
    rel = MergedRelationship(
        name="employs",
        source_type="Company",
        target_type="Person",
        description="Employment",
        richness_tier="attributed",
        richness_rationale="Start date and role vary per connection",
        edge_properties=[
            MergedProperty(name="start_date", data_type="datetime", source_passes=["llm_stage_d"]),
            MergedProperty(name="role", data_type="string", source_passes=["top_down"]),
        ],
        provenance="seed+2pass",
        confidence=0.77,
    )
    assert len(rel.edge_properties) == 2
    assert rel.richness_rationale


def test_cq_coverage_entry_statuses():
    """Test CQCoverageEntry with all coverage_status values."""
    for status in ["covered", "partial", "uncovered"]:
        entry = CQCoverageEntry(
            cq_id="abc12345",
            cq_text="What policies exist?",
            domain="insurance",
            coverage_status=status,
        )
        assert entry.coverage_status == status


def test_seed_schema_construction():
    """SeedSchema with full structure."""
    schema = SeedSchema(
        entity_types=[
            MergedEntityType(
                name="Policy",
                description="Insurance policy",
                provenance="3pass_novel",
                confidence=1.0,
                answerable_cqs=["cq_001"],
            ),
        ],
        relationships=[
            MergedRelationship(
                name="covers",
                source_type="Policy",
                target_type="Property",
                description="Covers",
                richness_tier="simple",
                provenance="2pass_novel",
                confidence=0.67,
            ),
        ],
        coverage_matrix=[
            CQCoverageEntry(
                cq_id="cq_001",
                cq_text="What policies exist?",
                coverage_status="covered",
                covered_by_types=["Policy"],
                covered_by_relationships=["covers"],
            ),
        ],
        provenance_summary={"3pass_novel": 1, "2pass_novel": 1},
        quality_metrics={
            "total_entity_types": 1,
            "total_relationships": 1,
            "cq_coverage_rate": 1.0,
        },
        gap_report={"uncovered_cqs": [], "orphan_types": []},
        extraction_run_id="test-run-123",
        industry_profile="real_estate",
    )
    assert len(schema.entity_types) == 1
    assert len(schema.relationships) == 1
    assert schema.provenance_summary["3pass_novel"] == 1
    assert schema.quality_metrics["cq_coverage_rate"] == 1.0


def test_schema_merge_run_construction():
    """SchemaMergeRun with populated fields."""
    run = SchemaMergeRun(
        extraction_run_id="ext-123",
        model="qwen2.5:7b",
        provider="ollama",
        input_entity_types=30,
        input_relationships=20,
        input_cqs=50,
        seed_types_count=10,
        merged_entity_types=12,
        merged_relationships=8,
        cq_coverage_rate=0.85,
        cross_pass_agreement_rate=0.7,
        provenance_distribution={"seed+3pass": 3, "2pass_novel": 5},
        richness_distribution={"simple": 5, "attributed": 2, "reified": 1},
    )
    assert run.extraction_run_id == "ext-123"
    assert run.merged_entity_types == 12
    assert run.cq_coverage_rate == 0.85
    assert run.status == "running"
    assert run.run_id  # auto-generated


def test_quality_metrics_dict_structure():
    """Quality metrics dict contains expected keys."""
    schema = SeedSchema(
        entity_types=[],
        relationships=[],
        coverage_matrix=[],
        quality_metrics={
            "total_entity_types": 0,
            "total_relationships": 0,
            "cq_coverage_rate": 0.0,
            "cross_pass_agreement_rate": 0.0,
            "orphan_type_count": 0,
            "orphan_relationship_count": 0,
            "richness_distribution": {"simple": 0, "attributed": 0, "reified": 0},
        },
    )
    qm = schema.quality_metrics
    assert "total_entity_types" in qm
    assert "cq_coverage_rate" in qm
    assert "richness_distribution" in qm
    assert qm["richness_distribution"]["simple"] == 0
