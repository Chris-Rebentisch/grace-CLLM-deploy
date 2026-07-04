"""F-0035/F-0036 (ISS-0026) regression: CQ test run failure propagation,
cancellation, and tolerant domain read-back.

validation run 2026-07-03: POST /api/ontology/cq-test/run returned 200 +
run_id; the background task raised immediately and the run row stayed
`status='running', total_cqs=0` for six hours — no failure propagation, no
cancel endpoint. Compounding (F-0036): the CQ domain whitelist was re-enforced
when rows were READ BACK, so stored data exploded mid-pipeline instead of at
authoring time.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session as SASession

from src.ontology.cq_test_models import CQTestRun, CQTestRunStatus
from src.ontology.cq_test_runner import (
    CQTestRunRow,
    cancel_test_run,
    create_test_run,
    load_testable_cqs,
    mark_test_run_failed,
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
        "TRUNCATE TABLE cq_test_runs, competency_questions, ontology_versions "
        "RESTART IDENTITY CASCADE"
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
    from src.ontology.models import VersionSource
    from src.ontology.schema_store import ratify_version

    return ratify_version(
        db=db_session,
        schema_json=SAMPLE_SCHEMA_FLAT,
        schema_modules={"corporate": {"entity_types": {"Company": {}}}},
        source=VersionSource.DISCOVERY,
        reviewer="test",
        changelog="Test version",
    )


def _create_running_run(db_session, version) -> CQTestRun:
    run = CQTestRun(
        schema_version_id=version.id,
        schema_version_number=version.version_number,
        status=CQTestRunStatus.RUNNING,
    )
    return create_test_run(db_session, run)


# --- mark_test_run_failed (F-0035 fix 1) ---


def test_mark_test_run_failed_persists_error_and_completed_at(db_session):
    version = _create_test_version(db_session)
    created = _create_running_run(db_session, version)

    updated = mark_test_run_failed(db_session, created.id, "boom: domain re-validation")

    assert updated is not None
    assert updated.status == CQTestRunStatus.FAILED
    assert updated.completed_at is not None
    assert updated.metadata_extra["error"] == "boom: domain re-validation"


def test_mark_test_run_failed_missing_run_returns_none(db_session):
    assert mark_test_run_failed(db_session, uuid4(), "boom") is None


def test_mark_test_run_failed_does_not_overwrite_terminal(db_session):
    """A late-failing task must not clobber an operator cancel."""
    version = _create_test_version(db_session)
    created = _create_running_run(db_session, version)
    cancel_test_run(db_session, created.id)

    result = mark_test_run_failed(db_session, created.id, "boom")

    assert result.status == CQTestRunStatus.CANCELLED
    assert "error" not in (result.metadata_extra or {})


def test_run_cq_tests_failure_persists_error_detail(db_session):
    """An exception inside run_cq_tests ends the row `failed` with error detail,
    never stuck `running` (F-0035)."""
    version = _create_test_version(db_session)
    created = _create_running_run(db_session, version)

    mock_prov = AsyncMock()
    mock_prov.provider_name = "mock"
    mock_prov.model = "mock-model"
    mock_prov.generate.return_value = LLMResponse(
        text=json.dumps({"result": "pass", "confidence": 0.9, "reasoning": "ok"}),
        model="mock-model",
        provider="mock",
    )

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load, \
         patch(
             "src.ontology.cq_test_runner._build_gap_summary",
             side_effect=RuntimeError("gap summary exploded"),
         ):
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"}],
            [],
        )
        with pytest.raises(RuntimeError, match="gap summary exploded"):
            import asyncio

            asyncio.run(
                run_cq_tests(
                    db_session,
                    schema_version_id=version.id,
                    concurrency=1,
                    existing_run_id=created.id,
                )
            )

    row = db_session.query(CQTestRunRow).filter(CQTestRunRow.id == created.id).one()
    assert row.status == CQTestRunStatus.FAILED.value
    assert row.completed_at is not None
    assert row.metadata_extra["error"] == "gap summary exploded"


# --- cancel_test_run (F-0035 fix 2) ---


def test_cancel_test_run_running_to_cancelled(db_session):
    version = _create_test_version(db_session)
    created = _create_running_run(db_session, version)

    outcome, run = cancel_test_run(db_session, created.id)

    assert outcome == "cancelled"
    assert run.status == CQTestRunStatus.CANCELLED
    assert run.completed_at is not None


def test_cancel_test_run_terminal_is_conflict(db_session):
    version = _create_test_version(db_session)
    run = CQTestRun(
        schema_version_id=version.id,
        status=CQTestRunStatus.COMPLETED,
    )
    created = create_test_run(db_session, run)

    outcome, result = cancel_test_run(db_session, created.id)

    assert outcome == "conflict"
    assert result.status == CQTestRunStatus.COMPLETED


def test_cancel_test_run_missing_is_not_found(db_session):
    outcome, result = cancel_test_run(db_session, uuid4())
    assert outcome == "not_found"
    assert result is None


def test_cooperative_cancel_stops_llm_calls_and_preserves_status(db_session):
    """A cancel landing mid-run stops further LLM calls and the run ends
    `cancelled` — the background task never overwrites it to `completed`."""
    version = _create_test_version(db_session)
    created = _create_running_run(db_session, version)

    calls = {"n": 0}

    async def fake_generate(**kwargs):
        calls["n"] += 1
        # Simulate the operator hitting POST .../cancel while CQ 1 is in flight.
        db_session.query(CQTestRunRow).filter(CQTestRunRow.id == created.id).update(
            {"status": CQTestRunStatus.CANCELLED.value}
        )
        db_session.commit()
        return LLMResponse(
            text=json.dumps({"result": "pass", "confidence": 0.9, "reasoning": "ok"}),
            model="mock-model",
            provider="mock",
        )

    mock_prov = AsyncMock()
    mock_prov.provider_name = "mock"
    mock_prov.model = "mock-model"
    mock_prov.generate.side_effect = fake_generate

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [
                {"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"},
                {"cq_id": "cq_002", "cq_text": "Q2", "domain": "d"},
            ],
            [],
        )
        import asyncio

        result = asyncio.run(
            run_cq_tests(
                db_session,
                schema_version_id=version.id,
                concurrency=1,
                existing_run_id=created.id,
            )
        )

    assert calls["n"] == 1  # second CQ never dispatched
    assert result.status == CQTestRunStatus.CANCELLED

    row = db_session.query(CQTestRunRow).filter(CQTestRunRow.id == created.id).one()
    assert row.status == CQTestRunStatus.CANCELLED.value


# --- load_testable_cqs domain tolerance (F-0036 fix 3) ---


def test_load_testable_cqs_tolerates_unlisted_domain(db_session):
    """A stored CQ whose domain is missing from the current domain_categories
    whitelist must NOT crash the run — warn and proceed (F-0036)."""
    from src.discovery.cq_database import CompetencyQuestionRow

    db_session.add(
        CompetencyQuestionRow(
            canonical_text="What is the thing?",
            source="human",
            status="ACCEPTED",
            domain="definitely_not_in_any_whitelist_zzz",
        )
    )
    db_session.commit()

    accepted, oos = load_testable_cqs(db_session)

    assert len(accepted) == 1
    assert accepted[0]["cq_text"] == "What is the thing?"
    assert accepted[0]["domain"] == "definitely_not_in_any_whitelist_zzz"
    assert oos == []


def test_load_testable_cqs_splits_accepted_and_out_of_scope(db_session):
    """Behavioral parity with the pre-fix list_cqs path for valid rows."""
    from src.discovery.cq_database import CompetencyQuestionRow

    db_session.add_all([
        CompetencyQuestionRow(
            canonical_text="Accepted Q", source="human", status="ACCEPTED",
            domain="other",
        ),
        CompetencyQuestionRow(
            canonical_text="OOS Q", source="human", status="OUT_OF_SCOPE",
            domain="other",
        ),
        CompetencyQuestionRow(
            canonical_text="Draft Q", source="human", status="DRAFT",
            domain="other",
        ),
    ])
    db_session.commit()

    accepted, oos = load_testable_cqs(db_session)

    assert [c["cq_text"] for c in accepted] == ["Accepted Q"]
    assert [c["cq_text"] for c in oos] == ["OOS Q"]


# --- Wall-clock cap (F-0035 / ISS-0026 deferral closure) ---


class _FakeClock:
    """Deterministic replacement for the module's ``time`` reference."""

    def __init__(self, values: list[float]):
        self._values = list(values)
        self._last = values[-1]

    def time(self) -> float:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


def _mock_provider(calls: dict | None = None):
    async def fake_generate(**kwargs):
        if calls is not None:
            calls["n"] += 1
        return LLMResponse(
            text=json.dumps({"result": "pass", "confidence": 0.9, "reasoning": "ok"}),
            model="mock-model",
            provider="mock",
        )

    prov = AsyncMock()
    prov.provider_name = "mock"
    prov.model = "mock-model"
    prov.generate.side_effect = fake_generate
    return prov


def test_wall_clock_cap_exceeded_marks_failed_and_stops_llm_calls(db_session):
    """Elapsed > max_run_seconds at the cooperative check → run `failed` with
    wall_clock_cap_exceeded error, no further LLM calls issued
    (F-0035 / ISS-0026 deferral closure)."""
    import asyncio

    version = _create_test_version(db_session)
    created = _create_running_run(db_session, version)

    calls = {"n": 0}
    # start_time=0.0; the first loop check sees 100.0 → elapsed 100 > cap 10.
    clock = _FakeClock([0.0, 100.0])

    with patch("src.ontology.cq_test_runner.get_provider", return_value=_mock_provider(calls)), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load, \
         patch("src.ontology.cq_test_runner._load_max_run_seconds", return_value=10.0), \
         patch("src.ontology.cq_test_runner.time", clock):
        mock_load.return_value = (
            [
                {"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"},
                {"cq_id": "cq_002", "cq_text": "Q2", "domain": "d"},
            ],
            [],
        )
        result = asyncio.run(
            run_cq_tests(
                db_session,
                schema_version_id=version.id,
                concurrency=1,
                existing_run_id=created.id,
            )
        )

    assert calls["n"] == 0  # no LLM call ever issued past the cap
    assert result.status == CQTestRunStatus.FAILED

    row = db_session.query(CQTestRunRow).filter(CQTestRunRow.id == created.id).one()
    assert row.status == CQTestRunStatus.FAILED.value
    assert row.completed_at is not None
    assert row.metadata_extra["error"] == "wall_clock_cap_exceeded after 100s"


def test_wall_clock_cap_disabled_runs_to_completion(db_session):
    """Cap <= 0 disables the wall-clock check — a long-elapsed run still
    completes normally (unchanged behavior)."""
    import asyncio

    version = _create_test_version(db_session)
    created = _create_running_run(db_session, version)

    calls = {"n": 0}
    # Clock far advanced past any plausible cap; cap disabled via 0.
    clock = _FakeClock([0.0, 999999.0])

    with patch("src.ontology.cq_test_runner.get_provider", return_value=_mock_provider(calls)), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load, \
         patch("src.ontology.cq_test_runner._load_max_run_seconds", return_value=0.0), \
         patch("src.ontology.cq_test_runner.time", clock):
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"}],
            [],
        )
        result = asyncio.run(
            run_cq_tests(
                db_session,
                schema_version_id=version.id,
                concurrency=1,
                existing_run_id=created.id,
            )
        )

    assert calls["n"] == 1
    assert result.status == CQTestRunStatus.COMPLETED


def test_wall_clock_cap_large_runs_to_completion(db_session):
    """A generous cap that is never exceeded leaves behavior unchanged."""
    import asyncio

    version = _create_test_version(db_session)
    created = _create_running_run(db_session, version)

    calls = {"n": 0}
    clock = _FakeClock([0.0, 1.0, 2.0])

    with patch("src.ontology.cq_test_runner.get_provider", return_value=_mock_provider(calls)), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load, \
         patch("src.ontology.cq_test_runner._load_max_run_seconds", return_value=3600.0), \
         patch("src.ontology.cq_test_runner.time", clock):
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"}],
            [],
        )
        result = asyncio.run(
            run_cq_tests(
                db_session,
                schema_version_id=version.id,
                concurrency=1,
                existing_run_id=created.id,
            )
        )

    assert calls["n"] == 1
    assert result.status == CQTestRunStatus.COMPLETED


def test_load_max_run_seconds_reads_shipped_config():
    """The shipped config/eval_config.yaml carries cq_test.max_run_seconds."""
    from src.ontology.cq_test_runner import _load_max_run_seconds

    assert _load_max_run_seconds() == 3600.0
