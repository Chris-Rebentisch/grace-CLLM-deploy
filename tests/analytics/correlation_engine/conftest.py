"""Shared fixtures for correlation_engine tests (Chunk 33).

Mirrors ``tests/analytics/signal_pipeline/conftest.py``:
- ``test_engine`` / ``test_session_factory``: live ``grace`` PostgreSQL
  engine + sessionmaker (assumes ``alembic upgrade head``).
- ``cleanup_correlation_tables``: deletes diagnostic_records, then
  correlation_runs, then alert_events between tests.
- ``make_prom_reader``: factory wrapping a request handler in a
  ``PrometheusReader`` over ``httpx.MockTransport``.
- ``correlation_run_context``: factory producing a
  ``CorrelationRunContext`` with stub Prometheus + real session.
- ``insert_signal_row``: helper that inserts a synthetic
  ``analytics_signals`` row so D252 signal-reading detectors can exercise
  the table without going through the signal pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.analytics.correlation_engine.base import CorrelationRunContext
from src.analytics.correlation_engine.config import CorrelationEngineConfig
from src.analytics.prometheus_reader import PrometheusReader
from src.shared.config import get_settings


@pytest.fixture(scope="session")
def test_engine():
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def test_session_factory(test_engine):
    return sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture
def cleanup_correlation_tables(test_engine):
    """Delete rows in FK-safe order between tests."""
    yield
    with test_engine.begin() as conn:
        conn.execute(text("DELETE FROM diagnostic_records"))
        conn.execute(text("DELETE FROM correlation_runs"))
        conn.execute(text("DELETE FROM alert_events"))


@pytest.fixture
def cleanup_signal_rows(test_engine):
    """Delete any analytics_signals rows produced by tests."""
    yield
    with test_engine.begin() as conn:
        conn.execute(text("DELETE FROM analytics_signals"))
        conn.execute(text("DELETE FROM signal_runs"))


@pytest.fixture
def make_prom_reader() -> Callable[[Callable[[httpx.Request], httpx.Response]], PrometheusReader]:
    """Return a factory that wraps a request handler in a ``PrometheusReader``."""

    def _factory(handler: Callable[[httpx.Request], httpx.Response]) -> PrometheusReader:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:9090"
        )
        return PrometheusReader(client=client)

    return _factory


def make_prom_vector(samples: list[dict[str, Any]]) -> dict:
    """Build a Prometheus instant-query vector envelope."""
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": s.get("metric", {}),
                    "value": [
                        s.get("ts", 1714_000_000.0),
                        f"{float(s['value'])}",
                    ],
                }
                for s in samples
            ],
        },
    }


def make_prom_matrix(series: list[dict[str, Any]]) -> dict:
    """Build a Prometheus range-query matrix envelope."""
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": s.get("metric", {}),
                    "values": [
                        [float(ts), f"{float(val)}"] for ts, val in s["values"]
                    ],
                }
                for s in series
            ],
        },
    }


@pytest.fixture
def insert_synthetic_signal_run(test_engine):
    """Insert a synthetic ``signal_runs`` row + N ``analytics_signals``.

    Returns ``(run_id, [(signal_type, ontology_module, strength), ...])``
    via the inserted rows. Idempotent across the same test (uuid4 each call).
    """
    inserted_run_ids: list[UUID] = []

    # F-0038/ISS-0027: fetch_latest_signal_strengths now windows across ALL
    # runs within signal_lookback_hours, so co-tenant residue in grace_test
    # (e.g. a prior run's signals from earlier today) is no longer shadowed
    # by the test's own newest run. Make the fixture hermetic: clear the
    # signal tables at setup, not just this test's own rows at teardown.
    with test_engine.begin() as conn:
        conn.execute(text("DELETE FROM analytics_signals"))
        conn.execute(text("DELETE FROM signal_runs"))

    def _insert(
        rows: list[tuple[str, str, float]],
        *,
        status: str = "success",
    ) -> UUID:
        run_id = uuid4()
        inserted_run_ids.append(run_id)
        now = datetime.now(UTC)
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO signal_runs (
                        id, started_at, completed_at, status,
                        triggered_by, config_hash
                    )
                    VALUES (
                        :id, :started, :completed, :status, 'cli', 'test-hash'
                    )
                    """
                ),
                {
                    "id": str(run_id),
                    "started": now,
                    "completed": now,
                    "status": status,
                },
            )
            for signal_type, ontology_module, strength in rows:
                conn.execute(
                    text(
                        """
                        INSERT INTO analytics_signals (
                            id, run_id, signal_type, ontology_module,
                            strength, evidence_snapshot, detected_at
                        ) VALUES (
                            gen_random_uuid(), :run_id, :sig, :mod,
                            :strength, '{}'::jsonb, :detected
                        )
                        """
                    ),
                    {
                        "run_id": str(run_id),
                        "sig": signal_type,
                        "mod": ontology_module,
                        "strength": strength,
                        "detected": now,
                    },
                )
        return run_id

    yield _insert

    with test_engine.begin() as conn:
        for run_id in inserted_run_ids:
            conn.execute(
                text("DELETE FROM analytics_signals WHERE run_id = :id"),
                {"id": str(run_id)},
            )
            conn.execute(
                text("DELETE FROM signal_runs WHERE id = :id"),
                {"id": str(run_id)},
            )


@pytest.fixture
def correlation_run_context(
    test_session_factory, make_prom_reader
) -> Callable[..., CorrelationRunContext]:
    """Factory: build a ``CorrelationRunContext`` with stub Prometheus + real session."""

    def _build(
        *,
        prom_handler: Callable[[httpx.Request], httpx.Response] | None = None,
        config: CorrelationEngineConfig | None = None,
        target_ontology_modules: list[str] | None = None,
        run_id: UUID | None = None,
    ) -> CorrelationRunContext:
        if prom_handler is None:
            def prom_handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json=make_prom_vector([]))
        reader = make_prom_reader(prom_handler)
        return CorrelationRunContext(
            run_id=run_id or uuid4(),
            started_at=datetime.now(UTC),
            prometheus_reader=reader,
            session_factory=test_session_factory,
            config=config or CorrelationEngineConfig(),
            target_ontology_modules=target_ontology_modules,
        )

    return _build
