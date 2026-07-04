"""Tests for the ontology schema store operations."""

import pytest
from sqlalchemy import text

# D485 carve-out (Chunk 75a): this module genuinely requires empty-baseline
# semantics (genesis hash chain, null previous_hash on first row).
# TRUNCATE retained with requires_db_wipe marker for D472 interlock.
pytestmark = pytest.mark.requires_db_wipe

from src.ontology.models import OntologyVersion, VersionSource
from src.ontology.schema_store import (
    _canonicalize_schema,
    canonical_json,
    compute_hash,
    count_schema_elements,
    get_schema_for_module,
    get_version_history,
    ratify_version,
    verify_hash_chain,
)
from src.shared.database import get_db, get_engine


@pytest.fixture(autouse=True)
def clean_tables():
    """Clean ontology tables before and after each test.

    TRUNCATE ... CASCADE bypasses row-level append-only/immutable triggers
    and auto-includes FK referrers added by later chunks
    (governance_decision_events c50b, calibration_decisions c49a, etc.).
    """
    engine = get_engine()

    def _cleanup() -> None:
        with engine.connect() as conn:
            conn.execute(text(
                "TRUNCATE TABLE schema_promotion_events, calibration_records, "
                "schema_proposals, ontology_versions "
                "RESTART IDENTITY CASCADE"
            ))
            conn.commit()

    _cleanup()
    yield
    _cleanup()


@pytest.fixture()
def db_session():
    """Yield a database session for testing."""
    gen = get_db()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


# --- compute_hash Tests ---


def test_compute_hash_deterministic():
    """Same input always produces the same hash."""
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    h1 = compute_hash(schema)
    h2 = compute_hash(schema)
    assert h1 == h2


def test_compute_hash_different_schemas():
    """Different schemas produce different hashes."""
    s1 = {"a": 1}
    s2 = {"a": 2}
    assert compute_hash(s1) != compute_hash(s2)


def test_compute_hash_with_previous_hash():
    """Including previous_hash changes the output."""
    schema = {"a": 1}
    h_no_prev = compute_hash(schema)
    h_with_prev = compute_hash(schema, previous_hash="abc123")
    assert h_no_prev != h_with_prev


def test_compute_hash_version_1_case():
    """Version 1 (no previous_hash) hashes schema_json alone."""
    schema = {"type": "object"}
    h = compute_hash(schema)
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex digest


# --- ratify_version Tests ---


def test_ratify_version_creates_v1(db_session):
    """ratify_version creates version 1 with no predecessor."""
    schema = {"entity_types": {"Company": {"properties": {"name": {}}}}, "relationships": {}}
    v = ratify_version(
        db_session,
        schema_json=schema,
        schema_modules={"core": schema},
        source=VersionSource.DISCOVERY,
        changelog="Initial Discovery run",
    )
    assert v.version_number == 1
    assert v.is_active is True
    assert v.patch_json is None
    assert v.diff_summary is None
    assert v.previous_version_id is None
    assert v.hash_chain is not None


def test_ratify_version_creates_v2_with_diff(db_session):
    """ratify_version creates version 2 with diff from version 1."""
    schema1 = {"entity_types": {"Company": {"properties": {}}}, "relationships": {}}
    ratify_version(db_session, schema_json=schema1, schema_modules={}, source=VersionSource.DISCOVERY)

    schema2 = {
        "entity_types": {"Company": {"properties": {}}, "Trust": {"properties": {}}},
        "relationships": {"owns": {}},
    }
    v2 = ratify_version(db_session, schema_json=schema2, schema_modules={}, source=VersionSource.MANUAL)

    assert v2.version_number == 2
    assert v2.patch_json is not None
    assert len(v2.patch_json) > 0
    assert v2.diff_summary is not None
    assert "summary" in v2.diff_summary
    assert v2.previous_version_id is not None


def test_ratify_version_active_swap(db_session):
    """New version becomes active, old deactivated."""
    schema1 = {"entity_types": {}, "relationships": {}}
    v1 = ratify_version(db_session, schema_json=schema1, schema_modules={}, source=VersionSource.DISCOVERY)

    schema2 = {"entity_types": {"A": {}}, "relationships": {}}
    v2 = ratify_version(db_session, schema_json=schema2, schema_modules={}, source=VersionSource.MANUAL)

    assert v2.is_active is True
    # Check v1 is no longer active
    from src.ontology.database import get_version_by_id
    v1_check = get_version_by_id(db_session, v1.id)
    assert v1_check.is_active is False


def test_ratify_version_hash_chain_correct(db_session):
    """Hash chain is computed correctly across versions.

    Phase-4 4.6d note: ``ratify_version`` canonicalizes the schema via
    ``_canonicalize_schema`` BEFORE hashing (production line 286 then 301),
    so the expected hashes must mirror that order. Direct ``compute_hash``
    on raw input would drift whenever the input carries un-canonical
    shapes (e.g. ``{"A": {}}`` -> ``{"A": {"properties": {}}}``).
    """
    schema1 = {"entity_types": {}, "relationships": {}}
    v1 = ratify_version(db_session, schema_json=schema1, schema_modules={}, source=VersionSource.DISCOVERY)

    expected_v1_hash = compute_hash(_canonicalize_schema(schema1))
    assert v1.hash_chain == expected_v1_hash

    schema2 = {"entity_types": {"A": {}}, "relationships": {}}
    v2 = ratify_version(db_session, schema_json=schema2, schema_modules={}, source=VersionSource.MANUAL)

    expected_v2_hash = compute_hash(_canonicalize_schema(schema2), v1.hash_chain)
    assert v2.hash_chain == expected_v2_hash


def test_ratify_version_counts_populated(db_session):
    """entity_type_count and relationship_type_count are populated."""
    schema = {
        "entity_types": {"Company": {}, "Trust": {}, "Person": {}},
        "relationships": {"owns": {}, "manages": {}},
    }
    v = ratify_version(db_session, schema_json=schema, schema_modules={}, source=VersionSource.DISCOVERY)
    assert v.entity_type_count == 3
    assert v.relationship_type_count == 2


# --- verify_hash_chain Tests ---


def test_verify_hash_chain_valid(db_session):
    """Valid chain returns valid=True."""
    schema1 = {"entity_types": {}, "relationships": {}}
    ratify_version(db_session, schema_json=schema1, schema_modules={}, source=VersionSource.DISCOVERY)

    schema2 = {"entity_types": {"A": {}}, "relationships": {}}
    ratify_version(db_session, schema_json=schema2, schema_modules={}, source=VersionSource.MANUAL)

    result = verify_hash_chain(db_session)
    assert result["valid"] is True
    assert result["versions_checked"] == 2


def test_verify_hash_chain_tampered(db_session):
    """Tampered version detected via raw SQL bypass of trigger."""
    schema1 = {"entity_types": {}, "relationships": {}}
    v1 = ratify_version(db_session, schema_json=schema1, schema_modules={}, source=VersionSource.DISCOVERY)

    schema2 = {"entity_types": {"A": {}}, "relationships": {}}
    v2 = ratify_version(db_session, schema_json=schema2, schema_modules={}, source=VersionSource.MANUAL)

    # Tamper with v1's schema_json via raw SQL (disable trigger temporarily)
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE ontology_versions DISABLE TRIGGER trig_ontology_versions_immutable"))
        conn.execute(
            text("UPDATE ontology_versions SET schema_json = :new_json WHERE version_number = 1"),
            {"new_json": '{"tampered": true}'},
        )
        conn.execute(text("ALTER TABLE ontology_versions ENABLE TRIGGER trig_ontology_versions_immutable"))
        conn.commit()

    # Expire session cache so it re-reads from DB
    db_session.expire_all()

    result = verify_hash_chain(db_session)
    assert result["valid"] is False
    assert result["first_broken_version"] == 1


# --- get_schema_for_module Tests ---


def test_get_schema_for_module_returns_subset(db_session):
    """Returns correct module subset."""
    modules = {"core": {"types": ["Company"]}, "extended": {"types": ["Trust"]}}
    ratify_version(
        db_session,
        schema_json={"entity_types": {}},
        schema_modules=modules,
        source=VersionSource.DISCOVERY,
    )

    result = get_schema_for_module(db_session, "core")
    assert result == {"types": ["Company"]}


def test_get_schema_for_module_nonexistent(db_session):
    """Returns None for nonexistent module."""
    ratify_version(
        db_session,
        schema_json={"entity_types": {}},
        schema_modules={"core": {}},
        source=VersionSource.DISCOVERY,
    )

    result = get_schema_for_module(db_session, "nonexistent")
    assert result is None


# --- get_version_history Tests ---


def test_get_version_history_format(db_session):
    """Returns correct summary format."""
    ratify_version(
        db_session,
        schema_json={"entity_types": {"A": {}}, "relationships": {}},
        schema_modules={},
        source=VersionSource.DISCOVERY,
        reviewer="alice",
        changelog="First version",
    )

    history = get_version_history(db_session)
    assert len(history) == 1
    entry = history[0]
    assert entry["version_number"] == 1
    assert entry["source"] == "discovery"
    assert entry["reviewer"] == "alice"
    assert entry["changelog"] == "First version"
    assert entry["is_active"] is True
    assert "entity_type_count" in entry
    assert "relationship_type_count" in entry


# --- count_schema_elements Tests ---


def test_count_schema_elements_flat():
    """Counts types in flat GrACE structure."""
    schema = {
        "entity_types": {"Company": {}, "Trust": {}, "Person": {}},
        "relationships": {"owns": {}, "manages": {}},
    }
    entities, rels = count_schema_elements(schema)
    assert entities == 3
    assert rels == 2


def test_count_schema_elements_defs():
    """Counts types in $defs structure."""
    schema = {
        "$defs": {"Company": {}, "Trust": {}},
        "type": "object",
    }
    entities, rels = count_schema_elements(schema)
    assert entities == 2
    assert rels == 0
