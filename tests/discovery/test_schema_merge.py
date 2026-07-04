"""Tests for four-input schema merge pipeline."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.discovery.cq_database import bulk_create_cqs
from src.discovery.cq_models import CQSource, CQStatus, CQType, CompetencyQuestion
from src.discovery.database import create_document
from src.discovery.models import FileType, ProcessedDocument, ProcessingStatus
from src.discovery.schema_merge import (
    _merge_entity_cluster_simple,
    _merge_rel_cluster_simple,
    assemble_seed_schema,
    build_coverage_matrix,
    collect_schema_elements,
    compute_confidence,
    compute_provenance,
    run_schema_merge,
)
from src.discovery.schema_merge_models import (
    CQCoverageEntry,
    MergedEntityType,
    MergedRelationship,
    SeedSchema,
)
from src.discovery.schema_merge_prompts import (
    build_edge_detection_prompt,
    build_entity_canonicalization_prompt,
)
from src.discovery.schema_models import (
    PassOutput,
    ProposedEntityType,
    ProposedProperty,
    ProposedRelationship,
    SchemaExtractionRun,
)
from src.discovery.seed_models import (
    SeedEntityType,
    SeedProperty,
    SeedReference,
    SeedRelationship,
)
from src.shared.database import get_engine
from src.shared.llm_provider import LLMResponse


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. Authorization: D485 / spec §6 Step 2.


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text("TRUNCATE competency_questions, cq_clusters, processed_documents CASCADE"))
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def _make_extraction_run() -> SchemaExtractionRun:
    """Create a mock extraction run with pass outputs."""
    return SchemaExtractionRun(
        run_id="test-extraction-run",
        status="completed",
        pass_outputs=[
            PassOutput(
                pass_name="top_down",
                domain="insurance",
                entity_types=[
                    ProposedEntityType(
                        name="Legal_Entity",
                        description="An entity with legal standing",
                        domain="corporate_structure",
                        answerable_cqs=["cq_001"],
                        evidence_documents=["corp.pdf"],
                        seed_alignment="LegalEntity",
                    ),
                    ProposedEntityType(
                        name="Insurance_Policy",
                        description="An insurance policy",
                        domain="insurance",
                        answerable_cqs=["cq_002"],
                    ),
                ],
                relationships=[
                    ProposedRelationship(
                        name="covers",
                        source_type="Insurance_Policy",
                        target_type="Property",
                        description="Policy covers property",
                        richness_hint="simple",
                        answerable_cqs=["cq_002"],
                    ),
                ],
                success=True,
            ),
            PassOutput(
                pass_name="bottom_up",
                domain="insurance",
                entity_types=[
                    ProposedEntityType(
                        name="Corporate_Entity",
                        description="A corporation or legal entity",
                        domain="corporate_structure",
                        answerable_cqs=["cq_001"],
                    ),
                    ProposedEntityType(
                        name="Policy",
                        description="An insurance policy document",
                        domain="insurance",
                        answerable_cqs=["cq_002", "cq_003"],
                    ),
                ],
                relationships=[
                    ProposedRelationship(
                        name="covers",
                        source_type="Policy",
                        target_type="Property",
                        description="Covers a property",
                        richness_hint="attributed",
                        edge_properties=[
                            ProposedProperty(name="coverage_amount", data_type="float"),
                        ],
                        answerable_cqs=["cq_002"],
                    ),
                ],
                success=True,
            ),
            PassOutput(
                pass_name="middle_out",
                domain="insurance",
                entity_types=[
                    ProposedEntityType(
                        name="Insurance_Policy",
                        description="Policy for insurance coverage",
                        domain="insurance",
                        answerable_cqs=["cq_002"],
                    ),
                ],
                relationships=[],
                success=True,
            ),
        ],
    )


def _make_seed_ref() -> SeedReference:
    """Create a mock seed reference."""
    return SeedReference(
        entity_types=[
            SeedEntityType(
                name="LegalEntity",
                source_ontology="fibo",
                source_uri="http://fibo.org/LegalEntity",
                description="A legal entity",
                properties=[
                    SeedProperty(name="jurisdiction", uri="http://fibo.org/jurisdiction", range_type="xsd:string"),
                ],
            ),
        ],
        relationships=[
            SeedRelationship(
                name="isOwnerOf",
                source_ontology="fibo",
                source_uri="http://fibo.org/isOwnerOf",
                domain_type="LegalEntity",
                range_type="Asset",
                description="Ownership relationship",
            ),
        ],
        source_files=["fibo_legal_entities.owl"],
        industry_profile="real_estate",
        registry_version="1.0.0",
        total_entity_types=1,
        total_relationships=1,
    )


# --- Provenance tests ---


def test_compute_provenance_all_combinations():
    """All seven provenance values computed correctly."""
    assert compute_provenance(["seed", "top_down", "bottom_up", "middle_out"]) == "seed+3pass"
    assert compute_provenance(["seed", "top_down", "bottom_up"]) == "seed+2pass"
    assert compute_provenance(["seed", "top_down"]) == "seed+1pass"
    assert compute_provenance(["seed"]) == "seed_only"
    assert compute_provenance(["top_down", "bottom_up", "middle_out"]) == "3pass_novel"
    assert compute_provenance(["top_down", "bottom_up"]) == "2pass_novel"
    assert compute_provenance(["top_down"]) == "1pass_only"


def test_compute_confidence():
    """Confidence scoring with seed boost."""
    assert compute_confidence(["top_down", "bottom_up", "middle_out"]) == 1.0
    assert compute_confidence(["top_down", "bottom_up"]) == 0.67
    assert compute_confidence(["top_down"]) == 0.33
    assert compute_confidence(["seed", "top_down"]) == 0.43  # 0.33 + 0.1
    assert compute_confidence(["seed", "top_down", "bottom_up", "middle_out"]) == 1.0  # capped


# --- Stage A tests ---


def test_collect_schema_elements_with_seed():
    """Collect flattens pass outputs + seed into dicts."""
    run = _make_extraction_run()
    seed_ref = _make_seed_ref()
    entity_dicts, rel_dicts = collect_schema_elements(run, seed_ref)

    # 2 + 2 + 1 from passes + 1 from seed = 6
    assert len(entity_dicts) == 6
    # 1 + 1 from passes + 1 from seed = 3
    assert len(rel_dicts) == 3

    # Check seed tagging
    seed_entities = [d for d in entity_dicts if d["source_pass"] == "seed"]
    assert len(seed_entities) == 1
    assert seed_entities[0]["name"] == "LegalEntity"

    seed_rels = [d for d in rel_dicts if d["source_pass"] == "seed"]
    assert len(seed_rels) == 1
    assert seed_rels[0]["name"] == "isOwnerOf"


def test_collect_schema_elements_without_seed():
    """Collect works without seed reference."""
    run = _make_extraction_run()
    entity_dicts, rel_dicts = collect_schema_elements(run, seed_ref=None)
    assert len(entity_dicts) == 5  # no seed entities
    assert len(rel_dicts) == 2  # no seed rels


# --- Stage B tests ---


def test_merge_entity_cluster_simple():
    """Simple merge picks canonical name, unions properties, computes provenance."""
    members = [
        {
            "name": "Legal_Entity",
            "source_pass": "top_down",
            "parent_type": None,
            "description": "An entity with legal standing",
            "domain": "corporate_structure",
            "properties": [{"name": "jurisdiction", "data_type": "string"}],
            "answerable_cqs": ["cq_001"],
            "evidence_documents": ["corp.pdf"],
            "seed_alignment": "LegalEntity",
        },
        {
            "name": "LegalEntity",
            "source_pass": "seed",
            "parent_type": None,
            "description": "A legal entity",
            "domain": "other",
            "properties": [{"name": "jurisdiction", "data_type": "string"}],
            "answerable_cqs": [],
            "evidence_documents": [],
            "source_ontology": "fibo",
        },
    ]
    merged = _merge_entity_cluster_simple(0, members)
    assert merged.name == "LegalEntity"  # seed preferred
    assert "Legal_Entity" in merged.alternative_names
    assert merged.provenance == "seed+1pass"
    assert merged.confidence == 0.43
    assert merged.seed_source == "fibo"
    assert len(merged.properties) >= 1  # jurisdiction deduplicated


def test_merge_rel_cluster_simple():
    """Simple merge for relationships unions edge properties."""
    members = [
        {
            "name": "covers",
            "source_pass": "top_down",
            "source_type": "Policy",
            "target_type": "Property",
            "description": "Policy covers property",
            "richness_hint": "simple",
            "edge_properties": [],
            "answerable_cqs": ["cq_002"],
            "evidence_documents": ["policy.pdf"],
        },
        {
            "name": "covers",
            "source_pass": "bottom_up",
            "source_type": "Policy",
            "target_type": "Property",
            "description": "Covers a property",
            "richness_hint": "attributed",
            "edge_properties": [{"name": "coverage_amount", "data_type": "float"}],
            "answerable_cqs": ["cq_002"],
            "evidence_documents": [],
        },
    ]
    merged = _merge_rel_cluster_simple(0, members)
    assert merged.name == "covers"
    assert merged.provenance == "2pass_novel"
    assert len(merged.edge_properties) == 1
    assert merged.edge_properties[0].name == "coverage_amount"


# --- Four-way agreement tests ---


def test_four_way_agreement_scoring():
    """Types from all four inputs get highest provenance."""
    members = [
        {"name": "Legal_Entity", "source_pass": "top_down", "description": "desc", "properties": [], "answerable_cqs": [], "evidence_documents": [], "domain": "other"},
        {"name": "Legal_Entity", "source_pass": "bottom_up", "description": "desc", "properties": [], "answerable_cqs": [], "evidence_documents": [], "domain": "other"},
        {"name": "Legal_Entity", "source_pass": "middle_out", "description": "desc", "properties": [], "answerable_cqs": [], "evidence_documents": [], "domain": "other"},
        {"name": "LegalEntity", "source_pass": "seed", "description": "desc", "properties": [], "answerable_cqs": [], "evidence_documents": [], "domain": "other", "source_ontology": "fibo"},
    ]
    merged = _merge_entity_cluster_simple(0, members)
    assert merged.provenance == "seed+3pass"
    assert merged.confidence == 1.0


# --- Coverage matrix tests ---


def test_coverage_matrix_covered():
    """CQ addressed by both type and relationship = covered."""
    types = [MergedEntityType(name="Policy", description="test", provenance="1pass_only", confidence=0.33, answerable_cqs=["cq_001"])]
    rels = [MergedRelationship(name="covers", source_type="Policy", target_type="Prop", description="test", richness_tier="simple", provenance="1pass_only", confidence=0.33, answerable_cqs=["cq_001"])]
    cqs = [CompetencyQuestion(id=uuid4(), canonical_text="Q?", raw_user_input="Q?", cq_type=CQType.SCOPING, source=CQSource.LLM_TOP_DOWN, status=CQStatus.ACCEPTED)]
    # Override ID to match
    short_id = str(cqs[0].id)[:8]
    types[0].answerable_cqs = [short_id]
    rels[0].answerable_cqs = [short_id]
    matrix = build_coverage_matrix(types, rels, cqs)
    assert len(matrix) == 1
    assert matrix[0].coverage_status == "covered"


def test_coverage_matrix_partial():
    """CQ addressed by type only = partial."""
    cq = CompetencyQuestion(id=uuid4(), canonical_text="Q?", raw_user_input="Q?", cq_type=CQType.SCOPING, source=CQSource.LLM_TOP_DOWN, status=CQStatus.ACCEPTED)
    short_id = str(cq.id)[:8]
    types = [MergedEntityType(name="Policy", description="test", provenance="1pass_only", confidence=0.33, answerable_cqs=[short_id])]
    rels = [MergedRelationship(name="covers", source_type="A", target_type="B", description="test", richness_tier="simple", provenance="1pass_only", confidence=0.33)]
    matrix = build_coverage_matrix(types, rels, [cq])
    assert matrix[0].coverage_status == "partial"


def test_coverage_matrix_uncovered():
    """CQ with no type or rel coverage = uncovered."""
    cq = CompetencyQuestion(id=uuid4(), canonical_text="Q?", raw_user_input="Q?", cq_type=CQType.SCOPING, source=CQSource.LLM_TOP_DOWN, status=CQStatus.ACCEPTED)
    matrix = build_coverage_matrix([], [], [cq])
    assert matrix[0].coverage_status == "uncovered"


def test_orphan_type_detection():
    """Types with 0 answerable_cqs flagged as orphans in gap_report."""
    types = [
        MergedEntityType(name="Orphan", description="test", provenance="1pass_only", confidence=0.33, answerable_cqs=[]),
        MergedEntityType(name="Good", description="test", provenance="1pass_only", confidence=0.33, answerable_cqs=["cq_001"]),
    ]
    schema = assemble_seed_schema(types, [], [], "run-1", "real_estate")
    assert "Orphan" in schema.gap_report["orphan_types"]
    assert "Good" not in schema.gap_report["orphan_types"]


# --- Prompt construction tests ---


def test_entity_canonicalization_prompt():
    """Prompt includes cluster members with source passes."""
    clusters = {
        0: [
            {"name": "Legal_Entity", "source_pass": "top_down", "parent_type": None, "seed_alignment": "LegalEntity", "properties": []},
            {"name": "Corp_Entity", "source_pass": "bottom_up", "parent_type": None, "seed_alignment": None, "properties": []},
        ],
    }
    system, user = build_entity_canonicalization_prompt(clusters)
    assert "ontology engineer" in system
    assert "Cluster 0" in user
    assert "top_down" in user
    assert "Legal_Entity" in user


def test_edge_detection_prompt():
    """Stage D prompt includes relationship clusters."""
    clusters = {
        0: [
            {"name": "covers", "source_pass": "top_down", "source_type": "Policy", "target_type": "Property", "richness_hint": "simple", "edge_properties": []},
        ],
    }
    system, user = build_edge_detection_prompt(clusters)
    assert "richness tier" in system
    assert "VARIABILITY TEST" in system
    assert "covers" in user
    assert "Policy" in user


def test_edge_detection_response_parsing():
    """LLM edge detection response parsed correctly."""
    response = json.dumps({
        "classifications": [
            {
                "cluster_label": 0,
                "canonical_name": "covers",
                "source_type": "Policy",
                "target_type": "Property",
                "richness_tier": "attributed",
                "richness_rationale": "Coverage amount varies",
                "edge_properties": [{"name": "coverage_amount", "data_type": "float"}],
                "reification_recommendation": None,
            }
        ]
    })
    from src.discovery.ollama_client import _parse_json_robust
    parsed = _parse_json_robust(response)
    assert parsed["classifications"][0]["richness_tier"] == "attributed"


def test_entity_canonicalization_response_parsing():
    """LLM entity canonicalization response parsed correctly."""
    response = json.dumps({
        "resolved_types": [
            {
                "cluster_label": 0,
                "canonical_name": "Legal_Entity",
                "parent_type": None,
                "description": "An entity with legal standing",
                "properties": [{"name": "jurisdiction", "data_type": "string"}],
                "hierarchy_rationale": "Broadest legal category",
            }
        ]
    })
    from src.discovery.ollama_client import _parse_json_robust
    parsed = _parse_json_robust(response)
    assert parsed["resolved_types"][0]["canonical_name"] == "Legal_Entity"


# --- Assembly tests ---


def test_assemble_seed_schema():
    """Assembly produces valid SeedSchema with quality metrics."""
    types = [
        MergedEntityType(name="Policy", description="test", provenance="3pass_novel", confidence=1.0, answerable_cqs=["cq_001"]),
        MergedEntityType(name="Property", description="test", provenance="2pass_novel", confidence=0.67, answerable_cqs=["cq_002"]),
    ]
    rels = [
        MergedRelationship(name="covers", source_type="Policy", target_type="Property", description="test", richness_tier="simple", provenance="2pass_novel", confidence=0.67, answerable_cqs=["cq_001"]),
    ]
    cq = CompetencyQuestion(id=uuid4(), canonical_text="Q?", raw_user_input="Q?", cq_type=CQType.SCOPING, source=CQSource.LLM_TOP_DOWN, status=CQStatus.ACCEPTED)
    short_id = str(cq.id)[:8]
    types[0].answerable_cqs = [short_id]
    rels[0].answerable_cqs = [short_id]
    coverage = build_coverage_matrix(types, rels, [cq])

    schema = assemble_seed_schema(types, rels, coverage, "run-1", "real_estate")
    assert isinstance(schema, SeedSchema)
    assert len(schema.entity_types) == 2
    assert len(schema.relationships) == 1
    assert schema.quality_metrics["total_entity_types"] == 2
    assert schema.quality_metrics["cross_pass_agreement_rate"] == 1.0  # all types have 2pass or 3pass
    assert schema.provenance_summary["3pass_novel"] == 1
    assert schema.industry_profile == "real_estate"


# --- Pipeline integration tests ---


def _make_mock_embeddings(n: int) -> list[list[float]]:
    """Create mock 384-dim embeddings."""
    import random
    random.seed(42)
    return [[random.random() for _ in range(384)] for _ in range(n)]


def _insert_cqs(db, domain="insurance", count=3, status=CQStatus.ACCEPTED):
    cqs = []
    for i in range(count):
        cq = CompetencyQuestion(
            canonical_text=f"What is the coverage of policy {i}?",
            raw_user_input=f"What is the coverage of policy {i}?",
            cq_type=CQType.SCOPING,
            domain=domain,
            source=CQSource.LLM_TOP_DOWN,
            source_pass="top_down",
            status=status,
            generation_confidence=0.8,
        )
        cqs.append(cq)
    bulk_create_cqs(db, cqs)
    return cqs


@pytest.mark.asyncio
async def test_dry_run_skips_llm(db_session):
    """Dry run skips LLM calls, still produces SeedSchema."""
    _insert_cqs(db_session, count=2)

    extraction_run = _make_extraction_run()
    from src.discovery.schema_extractor import _schema_runs
    _schema_runs[extraction_run.run_id] = extraction_run

    mock_embeddings = _make_mock_embeddings(6)

    async def mock_embed(texts, model="nomic-embed-text"):
        return mock_embeddings[:len(texts)]

    with (
        patch("src.discovery.schema_merge.embed_texts", side_effect=mock_embed),
        patch("src.discovery.schema_merge.get_provider") as mock_get_provider,
        patch("src.discovery.schema_merge._load_seed_reference", return_value=(None, "")),
        patch("src.discovery.schema_merge._store_merge_run_db"),
    ):
        mock_provider = MagicMock()
        mock_provider.provider_name = "ollama"
        mock_provider.model = "qwen2.5:7b"
        mock_get_provider.return_value = mock_provider

        result = await run_schema_merge(
            extraction_run_id=extraction_run.run_id,
            db=db_session,
            dry_run=True,
        )

    assert result.status == "completed"
    assert result.merged_entity_types > 0
    assert result.seed_schema_json is not None

    # Cleanup
    del _schema_runs[extraction_run.run_id]


@pytest.mark.asyncio
async def test_relationship_clustering_respects_endpoints(db_session):
    """Relationships with same name but different source/target stay separate."""
    run = SchemaExtractionRun(
        run_id="test-rel-endpoints",
        status="completed",
        pass_outputs=[
            PassOutput(
                pass_name="top_down",
                domain="test",
                entity_types=[],
                relationships=[
                    ProposedRelationship(
                        name="owns",
                        source_type="Person",
                        target_type="Property",
                        description="Person owns property",
                    ),
                    ProposedRelationship(
                        name="owns",
                        source_type="Company",
                        target_type="Vehicle",
                        description="Company owns vehicle",
                    ),
                ],
                success=True,
            ),
        ],
    )

    from src.discovery.schema_extractor import _schema_runs
    _schema_runs[run.run_id] = run

    _insert_cqs(db_session, count=1)

    async def mock_embed(texts, model="nomic-embed-text"):
        return _make_mock_embeddings(len(texts))

    with (
        patch("src.discovery.schema_merge.embed_texts", side_effect=mock_embed),
        patch("src.discovery.schema_merge.get_provider") as mock_get_provider,
        patch("src.discovery.schema_merge._load_seed_reference", return_value=(None, "")),
        patch("src.discovery.schema_merge._store_merge_run_db"),
    ):
        mock_provider = MagicMock()
        mock_provider.provider_name = "ollama"
        mock_provider.model = "qwen2.5:7b"
        mock_get_provider.return_value = mock_provider

        result = await run_schema_merge(
            extraction_run_id=run.run_id,
            db=db_session,
            dry_run=True,
        )

    assert result.status == "completed"
    # Two "owns" relationships with different endpoints should remain separate
    assert result.merged_relationships == 2

    del _schema_runs[run.run_id]
