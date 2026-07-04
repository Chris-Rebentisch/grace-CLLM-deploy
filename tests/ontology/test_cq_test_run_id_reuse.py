"""F-58 regression: cq-test run reuses the created run row (returned run_id).

validation run: POST /api/ontology/cq-test/run returned a run_id whose row
stayed `running 0/0` forever because run_cq_tests() created a SECOND row that
actually received results. The fix threads the created run's id into
run_cq_tests(existing_run_id=...) so the returned id IS the executing row.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session as SASession

from src.ontology.cq_test_models import CQTestRun, CQTestRunStatus
from src.ontology.cq_test_runner import (
    CQTestRunRow,
    create_test_run,
    run_cq_tests,
)
from src.shared.database import get_engine
from src.shared.llm_provider import LLMResponse

SAMPLE_SCHEMA_FLAT = {
    "entity_types": {
        "Company": {"description": "A co", "properties": {}, "domain": "corporate"}
    },
    "relationships": {},
}


@pytest.fixture()
def db_session():
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text(
        "TRUNCATE TABLE cq_test_runs, ontology_versions RESTART IDENTITY CASCADE"
    ))
    session = SASession(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def _create_test_version(db_session):
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


def _mock_provider():
    provider = AsyncMock()
    provider.provider_name = "mock"
    provider.model = "mock-model"
    provider.generate.return_value = LLMResponse(
        text=json.dumps({"result": "pass", "confidence": 0.9, "path": "A", "reasoning": "ok"}),
        model="mock-model",
        provider="mock",
    )
    return provider


@pytest.mark.asyncio
async def test_f58_existing_run_id_is_the_executing_row(db_session):
    """When existing_run_id is passed, results land on THAT row and no second
    row is created — so the API's returned run_id is the executing row."""
    version = _create_test_version(db_session)

    # Simulate the route: create the run row up front.
    pre = create_test_run(
        db_session,
        CQTestRun(
            schema_version_id=version.id,
            schema_version_number=version.version_number,
            status=CQTestRunStatus.RUNNING,
            concurrency=1,
        ),
    )
    pre_id = pre.id

    rows_before = db_session.query(CQTestRunRow).count()

    with patch("src.ontology.cq_test_runner.get_provider", return_value=_mock_provider()), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "What companies exist?", "domain": "corporate"}],
            [],
        )
        result = await run_cq_tests(
            db_session,
            schema_version_id=version.id,
            existing_run_id=pre_id,
        )

    # The returned run IS the pre-created row (same id).
    assert result.id == pre_id
    # No extra row was created.
    rows_after = db_session.query(CQTestRunRow).count()
    assert rows_after == rows_before

    # And the pre-created row now carries the executed results (not 0/0/running).
    row = db_session.query(CQTestRunRow).filter(CQTestRunRow.id == pre_id).first()
    assert row.status == CQTestRunStatus.COMPLETED.value
    assert row.total_cqs == 1
    assert row.passing == 1


@pytest.mark.asyncio
async def test_f58_without_existing_run_id_creates_own_row(db_session):
    """Backward-compat: absent existing_run_id, run_cq_tests still self-creates."""
    version = _create_test_version(db_session)
    rows_before = db_session.query(CQTestRunRow).count()

    with patch("src.ontology.cq_test_runner.get_provider", return_value=_mock_provider()), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "Q1", "domain": "corporate"}],
            [],
        )
        result = await run_cq_tests(db_session, schema_version_id=version.id)

    rows_after = db_session.query(CQTestRunRow).count()
    assert rows_after == rows_before + 1
    assert result.status == CQTestRunStatus.COMPLETED
