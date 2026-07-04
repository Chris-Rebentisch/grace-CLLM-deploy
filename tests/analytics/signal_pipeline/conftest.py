"""Shared fixtures for signal_pipeline tests.

Provides:
- ``test_engine`` / ``test_session_factory``: per-test PostgreSQL engine
  + sessionmaker over the live ``grace`` database (assumes
  ``alembic upgrade head`` has run; matches existing analytics-test
  precedent of running against the developer DB).
- ``cleanup_signal_tables``: teardown that deletes any rows we wrote.
- ``make_prom_reader``: build a ``PrometheusReader`` backed by an
  ``httpx.MockTransport`` returning canned JSON envelopes.
- ``signal_run_context``: assemble a ``SignalRunContext`` for tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.analytics.signal_pipeline.base import SignalRunContext
from src.analytics.signal_pipeline.config import SignalPipelineConfig
from src.analytics.prometheus_reader import PrometheusReader
from src.shared.config import get_settings


@pytest.fixture(scope="session")
def test_engine():
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces DELETE-based per-test cleanup with connection-level
# rollback. Authorization: D485 / spec §6 Step 2.


@pytest.fixture
def test_session_factory(test_engine):
    """Session factory bound to a SAVEPOINT-rollback connection (D485)."""
    connection = test_engine.connect()
    transaction = connection.begin()
    connection.execute(text("DELETE FROM analytics_signals; DELETE FROM signal_runs"))
    factory = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    yield factory
    transaction.rollback()
    connection.close()


@pytest.fixture
def cleanup_signal_tables():
    """No-op: SAVEPOINT-rollback in test_session_factory handles cleanup (D485)."""
    yield


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
    """Build a Prometheus instant-query vector envelope.

    Each ``samples`` entry: ``{"metric": {"label":"v"}, "value": 1.23,
    "ts": 1714000000}``.
    """
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
    """Build a Prometheus range-query matrix envelope.

    Each ``series`` entry: ``{"metric": {...}, "values": [(ts, val), ...]}``.
    """
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
def signal_run_context(
    test_session_factory, make_prom_reader
) -> Callable[..., SignalRunContext]:
    """Factory: build a SignalRunContext with stub Prometheus + real session."""

    def _build(
        *,
        prom_handler: Callable[[httpx.Request], httpx.Response] | None = None,
        config: SignalPipelineConfig | None = None,
        target_ontology_modules: list[str] | None = None,
        run_id: UUID | None = None,
    ) -> SignalRunContext:
        if prom_handler is None:
            def prom_handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200, json=make_prom_vector([])
                )
        reader = make_prom_reader(prom_handler)
        return SignalRunContext(
            run_id=run_id or uuid4(),
            started_at=datetime.now(UTC),
            prometheus_reader=reader,
            session_factory=test_session_factory,
            config=config or SignalPipelineConfig(),
            target_ontology_modules=target_ontology_modules,
        )

    return _build
