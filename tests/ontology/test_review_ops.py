"""Tests for review operations: schema assembly, CQ impact, session lifecycle."""

from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.ontology.review_database import (
    create_review_decision,
    create_review_session,
    increment_reviewed_count,
)
from src.ontology.review_models import (
    ReviewDecision,
    ReviewDecisionType,
    ReviewElementType,
    ReviewSession,
    ReviewSessionStatus,
)
from src.ontology.review_ops import (
    assemble_ratified_schema,
    complete_review_session,
    compute_cq_impact_for_decision,
    compute_cq_impact_preview,
    get_element_review_status,
    partition_schema_by_module,
    start_review_session,
)
from src.shared.database import get_engine


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. Authorization: D485 / spec §6 Step 2.


SAMPLE_SEED_SCHEMA = {
    "entity_types": [
        {
            "name": "Company",
            "description": "A business entity",
            "domain": "corporate",
            "parent_type": None,
            "properties": [
                {"name": "name", "data_type": "string", "required": True},
                {"name": "jurisdiction", "data_type": "string", "required": False},
            ],
            "provenance": "seed+3pass",
            "confidence": 1.0,
            "source_passes": ["top_down", "bottom_up", "middle_out"],
            "answerable_cqs": ["cq_001", "cq_002"],
        },
        {
            "name": "Insurance_Policy",
            "description": "An insurance coverage policy",
            "domain": "insurance",
            "parent_type": None,
            "properties": [
                {"name": "policy_number", "data_type": "string", "required": True},
            ],
            "provenance": "2pass_novel",
            "confidence": 0.67,
            "source_passes": ["top_down", "middle_out"],
            "answerable_cqs": ["cq_003"],
        },
    ],
    "relationships": [
        {
            "name": "covers",
            "source_type": "Insurance_Policy",
            "target_type": "Company",
            "description": "Policy covers a company",
            "richness_tier": "attributed",
            "edge_properties": [
                {"name": "coverage_amount", "data_type": "float"},
            ],
            "domain": "insurance",
            "provenance": "seed+2pass",
            "confidence": 0.8,
            "source_passes": ["top_down", "bottom_up"],
            "answerable_cqs": ["cq_003"],
        },
    ],
    "coverage_matrix": [
        {
            "cq_id": "cq_001",
            "cq_text": "What companies does Acme control?",
            "domain": "corporate",
            "covered_by_types": ["Company"],
            "covered_by_relationships": [],
            "coverage_status": "partial",
        },
        {
            "cq_id": "cq_002",
            "cq_text": "In which jurisdictions are companies registered?",
            "domain": "corporate",
            "covered_by_types": ["Company"],
            "covered_by_relationships": [],
            "coverage_status": "partial",
        },
        {
            "cq_id": "cq_003",
            "cq_text": "What insurance covers each company?",
            "domain": "insurance",
            "covered_by_types": ["Insurance_Policy", "Company"],
            "covered_by_relationships": ["covers"],
            "coverage_status": "covered",
        },
    ],
    "quality_metrics": {"cq_coverage_rate": 0.67},
    "provenance_summary": {"seed+3pass": 1, "2pass_novel": 1, "seed+2pass": 1},
}


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text(
        "TRUNCATE TABLE change_of_status_events, review_decisions, "
        "review_sessions, schema_promotion_events, calibration_records, "
        "schema_proposals, ontology_versions "
        "RESTART IDENTITY CASCADE"
    ))
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


def _make_decision(session_id, **overrides) -> ReviewDecision:
    """Create a ReviewDecision with sensible defaults."""
    defaults = {
        "session_id": session_id,
        "element_type": ReviewElementType.ENTITY_TYPE,
        "element_name": "Company",
        "decision": ReviewDecisionType.APPROVED,
        "original_data": {"name": "Company", "description": "A business entity"},
        "reviewer": "tester",
    }
    defaults.update(overrides)
    return ReviewDecision(**defaults)


# --- Schema Assembly Tests ---


def test_assemble_all_approved():
    """All approved decisions -> includes all types."""
    decisions = [
        _make_decision(uuid4(), element_name="Company", decision=ReviewDecisionType.APPROVED),
        _make_decision(uuid4(), element_name="Insurance_Policy", decision=ReviewDecisionType.APPROVED),
        _make_decision(
            uuid4(),
            element_name="covers",
            element_type=ReviewElementType.RELATIONSHIP,
            decision=ReviewDecisionType.APPROVED,
            original_data={"name": "covers"},
        ),
    ]
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, decisions)
    assert "Company" in result["entity_types"]
    assert "Insurance_Policy" in result["entity_types"]
    assert "covers" in result["relationships"]


def test_assemble_reject_entity_type():
    """Rejecting an entity type excludes it."""
    decisions = [
        _make_decision(uuid4(), element_name="Company", decision=ReviewDecisionType.APPROVED),
        _make_decision(uuid4(), element_name="Insurance_Policy", decision=ReviewDecisionType.REJECTED),
    ]
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, decisions)
    assert "Company" in result["entity_types"]
    assert "Insurance_Policy" not in result["entity_types"]
    # "covers" relationship references Insurance_Policy as source -> should be excluded
    assert "covers" not in result["relationships"]


def test_assemble_rename_entity_type():
    """Renaming an entity type includes it with the new name."""
    decisions = [
        _make_decision(
            uuid4(),
            element_name="Company",
            decision=ReviewDecisionType.RENAMED,
            modified_data={"name": "Corporation"},
        ),
    ]
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, decisions)
    assert "Corporation" in result["entity_types"]
    assert "Company" not in result["entity_types"]


def test_assemble_edit_properties():
    """Editing properties includes the type with modified properties."""
    decisions = [
        _make_decision(
            uuid4(),
            element_name="Company",
            decision=ReviewDecisionType.EDITED,
            modified_data={
                "name": "Company",
                "description": "Updated description",
                "properties": [
                    {"name": "name", "data_type": "string", "required": True},
                    {"name": "tax_id", "data_type": "string", "required": True},
                ],
            },
        ),
    ]
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, decisions)
    assert "Company" in result["entity_types"]
    assert result["entity_types"]["Company"]["description"] == "Updated description"


def test_assemble_split_entity_type():
    """Splitting an entity type replaces it with subtypes."""
    decisions = [
        _make_decision(
            uuid4(),
            element_name="Company",
            decision=ReviewDecisionType.SPLIT,
            split_into=[
                {"name": "Public_Company", "description": "Publicly traded", "domain": "corporate"},
                {"name": "Private_Company", "description": "Privately held", "domain": "corporate"},
            ],
        ),
    ]
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, decisions)
    assert "Company" not in result["entity_types"]
    assert "Public_Company" in result["entity_types"]
    assert "Private_Company" in result["entity_types"]


def test_assemble_merge_entity_types():
    """Merging types: one stays with merged properties, other is consumed."""
    decisions = [
        _make_decision(
            uuid4(),
            element_name="Company",
            decision=ReviewDecisionType.MERGED,
            merged_with="Insurance_Policy",
            modified_data={
                "name": "Company",
                "description": "Merged entity",
                "properties": [
                    {"name": "name", "data_type": "string", "required": True},
                    {"name": "policy_number", "data_type": "string", "required": False},
                ],
            },
        ),
    ]
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, decisions)
    assert "Company" in result["entity_types"]
    assert "Insurance_Policy" not in result["entity_types"]
    assert result["entity_types"]["Company"]["description"] == "Merged entity"


def test_assemble_un_reviewed_included():
    """Elements with no decision are included as-is (auto-approve)."""
    # No decisions at all
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, [])
    assert "Company" in result["entity_types"]
    assert "Insurance_Policy" in result["entity_types"]
    assert "covers" in result["relationships"]


def test_assemble_redirect_relationship():
    """Redirecting a relationship updates source/target."""
    decisions = [
        _make_decision(
            uuid4(),
            element_name="covers",
            element_type=ReviewElementType.RELATIONSHIP,
            decision=ReviewDecisionType.REDIRECTED,
            original_data={"name": "covers"},
            modified_data={
                "name": "covers",
                "source_type": "Company",
                "target_type": "Insurance_Policy",
            },
        ),
    ]
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, decisions)
    assert "covers" in result["relationships"]
    assert result["relationships"]["covers"]["source_type"] == "Company"
    assert result["relationships"]["covers"]["target_type"] == "Insurance_Policy"


def test_assemble_reclassify_relationship():
    """Reclassifying a relationship updates richness_tier."""
    decisions = [
        _make_decision(
            uuid4(),
            element_name="covers",
            element_type=ReviewElementType.RELATIONSHIP,
            decision=ReviewDecisionType.RECLASSIFIED,
            original_data={"name": "covers"},
            modified_data={
                "name": "covers",
                "source_type": "Insurance_Policy",
                "target_type": "Company",
                "richness_tier": "reified",
                "edge_properties": [
                    {"name": "coverage_amount", "data_type": "float"},
                    {"name": "effective_date", "data_type": "date"},
                ],
            },
        ),
    ]
    result = assemble_ratified_schema(SAMPLE_SEED_SCHEMA, decisions)
    assert "covers" in result["relationships"]
    assert result["relationships"]["covers"]["richness_tier"] == "reified"


def test_partition_schema_by_module():
    """partition_schema_by_module groups by domain field."""
    schema_json = {
        "entity_types": {
            "Company": {"domain": "corporate", "description": "A company"},
            "Insurance_Policy": {"domain": "insurance", "description": "A policy"},
        },
        "relationships": {
            "covers": {"domain": "insurance", "source_type": "Insurance_Policy", "target_type": "Company"},
        },
    }
    modules = partition_schema_by_module(schema_json)
    assert "corporate" in modules
    assert "insurance" in modules
    assert "Company" in modules["corporate"]["entity_types"]
    assert "Insurance_Policy" in modules["insurance"]["entity_types"]
    assert "covers" in modules["insurance"]["relationships"]


# --- CQ Impact Tests ---


def test_cq_impact_reject_type_loses_coverage():
    """Rejecting a type shows CQs that lose coverage."""
    result = compute_cq_impact_preview(
        SAMPLE_SEED_SCHEMA,
        [],  # no decisions yet
        "Company",
        ReviewDecisionType.REJECTED,
    )
    assert result["element_name"] == "Company"
    assert result["cqs_that_lose_coverage"] > 0
    assert result["coverage_after"] < result["coverage_before"]


def test_cq_impact_approve_shows_no_change():
    """Approving a type that's already included shows no change."""
    result = compute_cq_impact_preview(
        SAMPLE_SEED_SCHEMA,
        [],  # all included by default
        "Company",
        ReviewDecisionType.APPROVED,
    )
    # Company is already included (no decisions = all included), so approving changes nothing
    assert result["cqs_that_lose_coverage"] == 0


def test_cq_impact_coverage_rates_correct():
    """Coverage before/after rates are correct floats."""
    result = compute_cq_impact_preview(
        SAMPLE_SEED_SCHEMA,
        [],
        "Insurance_Policy",
        ReviewDecisionType.REJECTED,
    )
    assert 0.0 <= result["coverage_before"] <= 1.0
    assert 0.0 <= result["coverage_after"] <= 1.0
    # Insurance_Policy covers cq_003, so rejecting it might reduce coverage
    # (but Company also covers cq_003, so it depends on relationship)


def test_cq_impact_for_decision_same_structure():
    """compute_cq_impact_for_decision returns same structure as preview."""
    decision = _make_decision(
        uuid4(),
        element_name="Company",
        decision=ReviewDecisionType.REJECTED,
    )
    result = compute_cq_impact_for_decision(SAMPLE_SEED_SCHEMA, [], decision)
    assert "element_name" in result
    assert "coverage_before" in result
    assert "coverage_after" in result
    assert "cqs_affected" in result


# --- Session Lifecycle Tests ---


def test_element_status_surfaces_friendly_fields(db_session):
    """D522 session: /elements payload carries plain-language presentation fields."""
    seed = {
        "entity_types": [
            {
                "name": "Legal_Entity",
                "description": "An organization with legal standing",
                "display_label": "Companies & Organizations",
                "plain_description": "The businesses and trusts in your documents.",
                "example_snippet": "Acme Capital Partners, LLC",
                "evidence_document_count": 7,
                "answerable_cqs": ["cq_001"],
                "properties": [],
            }
        ],
        "relationships": [],
    }
    session = start_review_session(db_session, "run-friendly", "reviewer", seed)
    result = get_element_review_status(db_session, session.id)

    et = result["entity_types"][0]
    assert et["name"] == "Legal_Entity"  # technical name preserved for decisions
    assert et["display_label"] == "Companies & Organizations"
    assert et["plain_description"].startswith("The businesses")
    assert et["example_snippet"] == "Acme Capital Partners, LLC"
    assert et["evidence_document_count"] == 7
    assert et["status"] == "pending"
    # answerable_questions resolves CQ IDs to text when CQs exist; otherwise empty.
    assert "answerable_questions" in et
    assert et["answerable_cq_count"] == 1


def test_element_status_degrades_for_legacy_snapshot(db_session):
    """Snapshots predating D522 (no friendly fields) still return a valid payload."""
    seed = {
        "entity_types": [{"name": "Company", "description": "A business", "answerable_cqs": []}],
        "relationships": [],
    }
    session = start_review_session(db_session, "run-legacy", "reviewer", seed)
    et = get_element_review_status(db_session, session.id)["entity_types"][0]
    assert et["display_label"] == ""
    assert et["plain_description"] == ""
    assert et["example_snippet"] is None
    assert et["evidence_document_count"] == 0


def test_complete_review_session_ratifies(db_session):
    """complete_review_session assembles schema and ratifies."""
    session = start_review_session(db_session, "run-001", "reviewer", SAMPLE_SEED_SCHEMA)

    # Approve all elements
    for et in SAMPLE_SEED_SCHEMA["entity_types"]:
        d = _make_decision(
            session.id,
            element_name=et["name"],
            decision=ReviewDecisionType.APPROVED,
            original_data=et,
        )
        create_review_decision(db_session, d)
        increment_reviewed_count(db_session, session.id, ReviewElementType.ENTITY_TYPE)

    for rel in SAMPLE_SEED_SCHEMA["relationships"]:
        d = _make_decision(
            session.id,
            element_name=rel["name"],
            element_type=ReviewElementType.RELATIONSHIP,
            decision=ReviewDecisionType.APPROVED,
            original_data=rel,
        )
        create_review_decision(db_session, d)
        increment_reviewed_count(db_session, session.id, ReviewElementType.RELATIONSHIP)

    result = complete_review_session(db_session, session.id, "reviewer")
    assert "version" in result
    assert result["version"]["version_number"] == 1
    assert result["session"]["status"] == "completed"


def test_complete_review_session_force_auto_approves(db_session):
    """complete_review_session with force=True auto-approves un-reviewed elements."""
    session = start_review_session(db_session, "run-002", "reviewer", SAMPLE_SEED_SCHEMA)

    # Only approve one type, leave rest un-reviewed
    d = _make_decision(
        session.id,
        element_name="Company",
        decision=ReviewDecisionType.APPROVED,
        original_data=SAMPLE_SEED_SCHEMA["entity_types"][0],
    )
    create_review_decision(db_session, d)
    increment_reviewed_count(db_session, session.id, ReviewElementType.ENTITY_TYPE)

    # force=True should auto-approve the rest
    result = complete_review_session(db_session, session.id, "reviewer", force=True)
    assert "version" in result
    assert result["decision_summary"]["total"] >= 3  # at least our 1 + 2 auto-approved


# --- Element Review Status Tests ---


def test_get_element_review_status_shows_pending_and_decided(db_session):
    """get_element_review_status shows pending and decided elements."""
    session = start_review_session(db_session, "run-003", "reviewer", SAMPLE_SEED_SCHEMA)

    # Decide on Company only
    d = _make_decision(
        session.id,
        element_name="Company",
        decision=ReviewDecisionType.APPROVED,
        original_data=SAMPLE_SEED_SCHEMA["entity_types"][0],
    )
    create_review_decision(db_session, d)

    status = get_element_review_status(db_session, session.id)
    et_names = {e["name"]: e for e in status["entity_types"]}
    assert et_names["Company"]["status"] == "decided"
    assert et_names["Company"]["decision"] == "approved"
    assert et_names["Insurance_Policy"]["status"] == "pending"
    assert et_names["Insurance_Policy"]["decision"] is None


# --- F-0012 / ISS-0045: null-domain partition tests ---


def test_partition_null_domain_lands_in_general():
    """Present-but-null domain must land in 'general', not a 'null' module."""
    schema_json = {
        "entity_types": {
            "Company": {"domain": "corporate"},
            "Mystery_Type": {"domain": None},  # key present, value null
            "Undomained_Type": {},  # key absent
        },
        "relationships": {
            "owns": {"domain": None, "source_type": "Company", "target_type": "Company"},
            "employs": {"source_type": "Company", "target_type": "Company"},
        },
    }

    modules = partition_schema_by_module(schema_json)

    # No None / "null" module key may ever appear.
    assert None not in modules
    assert "null" not in modules
    # Both null-domain and missing-domain elements land in "general".
    assert set(modules["general"]["entity_types"]) == {
        "Mystery_Type",
        "Undomained_Type",
    }
    assert set(modules["general"]["relationships"]) == {"owns", "employs"}
    # Real domains are unchanged.
    assert set(modules["corporate"]["entity_types"]) == {"Company"}


# --- F-0011 / ISS-0044: answerable-CQ coverage-matrix fallback ---


def test_answerable_cq_index_inverts_coverage_matrix():
    """_answerable_cq_index maps elements to the CQs that pull for them."""
    from src.ontology.review_ops import _answerable_cq_index

    snapshot = {
        "coverage_matrix": [
            {
                "cq_id": "cq_001",
                "covered_by_types": ["Company"],
                "covered_by_relationships": ["owns"],
            },
            {
                "cq_id": "cq_002",
                "covered_by_types": ["Company", "Trust"],
                "covered_by_relationships": [],
            },
        ]
    }

    index = _answerable_cq_index(snapshot)

    assert index[("entity_type", "Company")] == ["cq_001", "cq_002"]
    assert index[("entity_type", "Trust")] == ["cq_002"]
    assert index[("relationship", "owns")] == ["cq_001"]


def test_present_element_falls_back_to_coverage_matrix():
    """Elements without their own answerable_cqs get counts from the matrix."""
    from src.ontology.review_ops import _answerable_cq_index, _present_element

    snapshot = {
        "coverage_matrix": [
            {"cq_id": "cq_001", "covered_by_types": ["Trust"]},
            {"cq_id": "cq_002", "covered_by_types": ["Trust"]},
        ]
    }
    index = _answerable_cq_index(snapshot)
    raw = {"name": "Trust", "description": "A trust"}  # no answerable_cqs field

    shaped = _present_element(
        raw, ReviewElementType.ENTITY_TYPE, {}, {}, index
    )

    # F-0011: previously this showed 0 despite coverage_rate 1.0 upstream.
    assert shaped["answerable_cq_count"] == 2


def test_present_element_prefers_own_answerable_cqs():
    """An element carrying its own answerable_cqs keeps them (no override)."""
    from src.ontology.review_ops import _present_element

    index = {("entity_type", "Company"): ["cq_009"]}
    raw = {"name": "Company", "answerable_cqs": ["cq_001", "cq_002", "cq_003"]}

    shaped = _present_element(
        raw, ReviewElementType.ENTITY_TYPE, {}, {}, index
    )

    assert shaped["answerable_cq_count"] == 3


def test_element_listing_counts_from_coverage_matrix(db_session):
    """End-to-end: session whose snapshot only has a coverage matrix still counts."""
    snapshot = {
        "entity_types": [
            {"name": "Trust", "description": "A trust"},  # no answerable_cqs
        ],
        "relationships": [
            {"name": "administers", "description": "", "source_type": "Trustee", "target_type": "Trust"},
        ],
        "coverage_matrix": [
            {
                "cq_id": "cq_010",
                "covered_by_types": ["Trust"],
                "covered_by_relationships": ["administers"],
            },
        ],
    }
    session = start_review_session(db_session, "run-iss0044", "reviewer", snapshot)

    status = get_element_review_status(db_session, session.id)

    et = {e["name"]: e for e in status["entity_types"]}
    rel = {r["name"]: r for r in status["relationships"]}
    assert et["Trust"]["answerable_cq_count"] == 1
    assert rel["administers"]["answerable_cq_count"] == 1
