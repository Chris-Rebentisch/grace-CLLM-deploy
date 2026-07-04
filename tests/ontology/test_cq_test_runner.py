"""Tests for CQ Test Runner: verbalizer, verification, orchestrator, gate."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.ontology.cq_test_models import (
    CQGapSeverity,
    CQGapType,
    CQTestResult,
    CQTestResultEntry,
    CQTestRunStatus,
)
from src.ontology.cq_test_runner import (
    create_test_run,
    get_test_run_by_id,
    run_cq_tests,
    run_non_regression_gate,
    verbalize_schema,
    verify_single_cq,
)
from src.shared.database import get_engine
from src.shared.llm_provider import LLMResponse


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. Authorization: D485 / spec §6 Step 2.


SAMPLE_SCHEMA_FLAT = {
    "entity_types": {
        "Company": {
            "description": "A business entity",
            "properties": [
                {"name": "name", "data_type": "string", "required": True},
                {"name": "jurisdiction", "data_type": "string", "required": False},
            ],
            "parent_type": None,
            "domain": "corporate",
        },
    },
    "relationships": {
        "employs": {
            "source_type": "Company",
            "target_type": "Person",
            "description": "Company employs a person",
            "edge_properties": [],
            "richness_tier": "simple",
        },
    },
}

SAMPLE_SCHEMA_DEFS = {
    "$defs": {
        "Company": {
            "description": "A business entity",
            "properties": {"name": {"type": "string"}},
        },
    },
}


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text(
        "TRUNCATE TABLE cq_test_runs, change_of_status_events, "
        "review_decisions, review_sessions, schema_promotion_events, "
        "calibration_records, schema_proposals, ontology_versions "
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


# --- Verbalization Tests ---


def test_verbalize_flat_format():
    """verbalize_schema produces correct output for flat GrACE format."""
    result = verbalize_schema(SAMPLE_SCHEMA_FLAT)
    assert "ONTOLOGY SCHEMA DESCRIPTION" in result
    assert "Company: A business entity" in result
    assert "name (string, required)" in result
    assert "jurisdiction (string)" in result
    assert "employs: Company -> Person" in result
    assert "Richness: simple" in result


def test_verbalize_defs_format():
    """verbalize_schema produces correct output for $defs format."""
    result = verbalize_schema(SAMPLE_SCHEMA_DEFS)
    assert "ONTOLOGY SCHEMA DESCRIPTION" in result
    assert "Company: A business entity" in result


def test_verbalize_deterministic():
    """Same input produces same output."""
    r1 = verbalize_schema(SAMPLE_SCHEMA_FLAT)
    r2 = verbalize_schema(SAMPLE_SCHEMA_FLAT)
    assert r1 == r2


def test_verbalize_empty_schema():
    """Empty schema produces minimal output."""
    result = verbalize_schema({})
    assert "ONTOLOGY SCHEMA DESCRIPTION" in result
    assert "(none)" in result


# --- Verification Tests ---


def _mock_provider(response_json: dict) -> LLMResponse:
    """Create a mock LLM provider that returns the given JSON."""
    provider = AsyncMock()
    provider.provider_name = "mock"
    provider.model = "mock-model"
    provider.generate.return_value = LLMResponse(
        text=json.dumps(response_json),
        model="mock-model",
        provider="mock",
    )
    return provider


@pytest.mark.asyncio
async def test_verify_single_cq_pass():
    """Mock LLM returns pass -> CQTestResultEntry with result=pass."""
    provider = _mock_provider({
        "result": "pass",
        "confidence": 0.95,
        "path": "Company -> (employs) -> Person",
        "gap_type": None,
        "gap_severity": None,
        "gap_details": None,
        "reasoning": "Schema contains Company, Person, and employs relationship",
    })
    entry = await verify_single_cq(
        "schema text", "What companies employ people?", "cq_001", "corporate", provider
    )
    assert entry.result == CQTestResult.PASS
    assert entry.confidence == 0.95
    assert entry.traced_path is not None
    provider.generate.assert_called_once()


@pytest.mark.asyncio
async def test_verify_single_cq_fail_with_gap():
    """Mock LLM returns fail with gap -> correct gap_type and gap_severity."""
    provider = _mock_provider({
        "result": "fail",
        "confidence": 0.85,
        "path": None,
        "gap_type": "missing_type",
        "gap_severity": "major",
        "gap_details": "No Person entity type exists",
        "reasoning": "Schema lacks Person type",
    })
    entry = await verify_single_cq(
        "schema text", "Who works at the company?", "cq_002", "corporate", provider
    )
    assert entry.result == CQTestResult.FAIL
    assert entry.gap_type == CQGapType.MISSING_TYPE
    assert entry.gap_severity == CQGapSeverity.MAJOR
    assert entry.gap_details is not None


@pytest.mark.asyncio
async def test_verify_single_cq_unparseable():
    """Mock LLM returns unparseable response -> result=ERROR."""
    provider = AsyncMock()
    provider.provider_name = "mock"
    provider.generate.return_value = LLMResponse(
        text="This is not JSON at all",
        model="mock-model",
        provider="mock",
    )
    entry = await verify_single_cq(
        "schema text", "What is X?", "cq_003", "other", provider
    )
    assert entry.result == CQTestResult.ERROR
    assert entry.error_message is not None


@pytest.mark.asyncio
async def test_verify_single_cq_exception():
    """Mock LLM call fails with exception -> result=ERROR."""
    provider = AsyncMock()
    provider.provider_name = "mock"
    provider.generate.side_effect = Exception("Connection refused")
    entry = await verify_single_cq(
        "schema text", "What is X?", "cq_004", "other", provider
    )
    assert entry.result == CQTestResult.ERROR
    assert "Connection refused" in entry.error_message


# --- Orchestrator Tests ---


def _create_test_version(db_session):
    """Create a ratified ontology version for testing."""
    from src.ontology.schema_store import ratify_version
    from src.ontology.models import VersionSource

    return ratify_version(
        db=db_session,
        schema_json=SAMPLE_SCHEMA_FLAT,
        schema_modules={"corporate": {"entity_types": {"Company": {}}}},
        source=VersionSource.DISCOVERY,
        reviewer="test",
        changelog="Test version",
    )


@pytest.mark.asyncio
async def test_run_cq_tests_all_pass(db_session):
    """All CQs pass -> pass_rate=1.0."""
    version = _create_test_version(db_session)

    mock_provider = _mock_provider({
        "result": "pass",
        "confidence": 0.9,
        "path": "Company -> (employs) -> Person",
        "gap_type": None,
        "gap_severity": None,
        "gap_details": None,
        "reasoning": "All elements present",
    })

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_provider), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [
                {"cq_id": "cq_001", "cq_text": "What companies exist?", "domain": "corporate"},
                {"cq_id": "cq_002", "cq_text": "Who works there?", "domain": "corporate"},
            ],
            [],  # no out-of-scope
        )
        run = await run_cq_tests(db_session, schema_version_id=version.id)

    assert run.status == CQTestRunStatus.COMPLETED
    assert run.passing == 2
    assert run.pass_rate == 1.0
    assert mock_provider.generate.call_count == 2


@pytest.mark.asyncio
async def test_run_cq_tests_some_fail(db_session):
    """Some CQs fail -> correct pass_rate calculation."""
    version = _create_test_version(db_session)

    pass_response = LLMResponse(
        text=json.dumps({"result": "pass", "confidence": 0.9, "path": "A", "reasoning": "ok"}),
        model="mock", provider="mock",
    )
    fail_response = LLMResponse(
        text=json.dumps({
            "result": "fail", "confidence": 0.8, "gap_type": "missing_type",
            "gap_severity": "minor", "gap_details": "Missing X", "reasoning": "no X",
        }),
        model="mock", provider="mock",
    )

    mock_prov = AsyncMock()
    mock_prov.provider_name = "mock"
    mock_prov.model = "mock-model"
    mock_prov.generate.side_effect = [pass_response, fail_response]

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [
                {"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"},
                {"cq_id": "cq_002", "cq_text": "Q2", "domain": "d"},
            ],
            [],
        )
        run = await run_cq_tests(db_session, schema_version_id=version.id)

    assert run.passing == 1
    assert run.failing == 1
    assert run.pass_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_run_cq_tests_out_of_scope_excluded(db_session):
    """Out-of-scope CQs excluded from pass_rate denominator."""
    version = _create_test_version(db_session)

    mock_prov = _mock_provider({
        "result": "pass", "confidence": 0.9, "path": "A", "reasoning": "ok",
    })

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"}],
            [{"cq_id": "cq_oos", "cq_text": "Out of scope Q", "domain": "other"}],
        )
        run = await run_cq_tests(db_session, schema_version_id=version.id)

    assert run.total_cqs == 2
    assert run.out_of_scope == 1
    assert run.passing == 1
    assert run.pass_rate == 1.0  # 1 / (2 - 1) = 1.0
    # Only 1 LLM call (out-of-scope doesn't call LLM)
    assert mock_prov.generate.call_count == 1


@pytest.mark.asyncio
async def test_run_cq_tests_sequential(db_session):
    """Concurrency=1 runs sequentially (verify call count)."""
    version = _create_test_version(db_session)

    mock_prov = _mock_provider({
        "result": "pass", "confidence": 0.9, "path": "A", "reasoning": "ok",
    })

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [
                {"cq_id": f"cq_{i}", "cq_text": f"Q{i}", "domain": "d"}
                for i in range(3)
            ],
            [],
        )
        run = await run_cq_tests(db_session, schema_version_id=version.id, concurrency=1)

    assert run.passing == 3
    assert mock_prov.generate.call_count == 3


@pytest.mark.asyncio
async def test_run_cq_tests_concurrent(db_session):
    """Concurrency=3 runs with semaphore (verify all complete)."""
    version = _create_test_version(db_session)

    mock_prov = _mock_provider({
        "result": "pass", "confidence": 0.9, "path": "A", "reasoning": "ok",
    })

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [
                {"cq_id": f"cq_{i}", "cq_text": f"Q{i}", "domain": "d"}
                for i in range(5)
            ],
            [],
        )
        run = await run_cq_tests(db_session, schema_version_id=version.id, concurrency=3)

    assert run.passing == 5
    assert mock_prov.generate.call_count == 5


# --- Gate Tests ---


@pytest.mark.asyncio
async def test_gate_passes(db_session):
    """pass_rate >= threshold -> gate_passed=True."""
    version = _create_test_version(db_session)

    mock_prov = _mock_provider({
        "result": "pass", "confidence": 0.9, "path": "A", "reasoning": "ok",
    })

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"}],
            [],
        )
        result = await run_non_regression_gate(
            db_session, proposed_schema_json=SAMPLE_SCHEMA_FLAT, threshold=0.90
        )

    assert result.gate_passed is True
    assert result.pass_rate == 1.0


@pytest.mark.asyncio
async def test_gate_fails(db_session):
    """pass_rate < threshold -> gate_passed=False."""
    version = _create_test_version(db_session)

    fail_response = LLMResponse(
        text=json.dumps({
            "result": "fail", "confidence": 0.8, "gap_type": "missing_type",
            "gap_severity": "major", "gap_details": "Missing X", "reasoning": "no",
        }),
        model="mock", provider="mock",
    )
    mock_prov = AsyncMock()
    mock_prov.provider_name = "mock"
    mock_prov.model = "mock-model"
    mock_prov.generate.return_value = fail_response

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"}],
            [],
        )
        result = await run_non_regression_gate(
            db_session, proposed_schema_json=SAMPLE_SCHEMA_FLAT, threshold=0.90
        )

    assert result.gate_passed is False
    assert result.pass_rate == 0.0
    assert len(result.failing_cqs) == 1
