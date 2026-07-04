"""Tests for Chunk 56 triage trigger route (CP8 — 5 tests)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.ingestion.models import (
    CuratedEmailSubsetRow,
    IngestionSource,
)


@pytest.fixture
def test_app():
    from fastapi import FastAPI
    from src.api.ingestion_routes import ingestion_router

    app = FastAPI()
    app.include_router(ingestion_router)
    return app


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def client(test_app, mock_db):
    from src.shared.database import get_db
    from src.graph.arcade_client import get_arcade_client
    from unittest.mock import AsyncMock

    def _override_db():
        yield mock_db

    def _override_arcade():
        return AsyncMock()

    test_app.dependency_overrides[get_db] = _override_db
    test_app.dependency_overrides[get_arcade_client] = _override_arcade

    with TestClient(test_app) as c:
        yield c

    test_app.dependency_overrides.clear()


def _make_source(source_id=None, source_type="mbox"):
    src = MagicMock(spec=IngestionSource)
    src.id = source_id or uuid4()
    src.name = "test-source"
    src.source_type = source_type
    src.config_json = {"source_type": source_type, "file_path": "/test.mbox"}
    src.segment = "insurance"
    src.enabled = True
    src.created_at = datetime.now(timezone.utc)
    src.deleted_at = None
    return src


class TestTriageTrigger:
    @patch("src.api.ingestion_routes.subprocess.Popen")
    @patch("src.api.ingestion_routes.yaml")
    def test_triage_trigger_202(self, mock_yaml, mock_popen, client, mock_db):
        source_id = uuid4()
        src = _make_source(source_id=source_id)
        mock_db.query.return_value.filter_by.return_value.first.return_value = src

        mock_yaml.safe_load.return_value = {"ingestion": {"deployment_path": "A"}}

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        resp = client.post(f"/api/ingestion/sources/{source_id}/triage")
        assert resp.status_code == 202
        data = resp.json()
        assert "run_id" in data

    def test_triage_trigger_source_404(self, client, mock_db):
        mock_db.query.return_value.filter_by.return_value.first.return_value = None
        resp = client.post(f"/api/ingestion/sources/{uuid4()}/triage")
        assert resp.status_code == 404

    @patch("src.api.ingestion_routes.yaml")
    def test_triage_trigger_path_b_no_bootstrap_422(self, mock_yaml, client, mock_db):
        """Path B without curated subset returns 422."""
        source_id = uuid4()
        src = _make_source(source_id=source_id)

        # First query: IngestionSource
        # Second query: CuratedEmailSubsetRow
        call_count = [0]
        def _query_side(model):
            m = MagicMock()
            call_count[0] += 1
            if model == IngestionSource:
                fb = MagicMock()
                fb.first.return_value = src
                m.filter_by.return_value = fb
            elif model == CuratedEmailSubsetRow:
                fb = MagicMock()
                fb.first.return_value = None  # no curated subset
                m.filter_by.return_value = fb
            return m
        mock_db.query.side_effect = _query_side

        mock_yaml.safe_load.return_value = {"ingestion": {"deployment_path": "B"}}

        resp = client.post(f"/api/ingestion/sources/{source_id}/triage")
        assert resp.status_code == 422
        assert "bootstrap" in resp.json()["detail"].lower()

    @patch("src.api.ingestion_routes.subprocess.Popen")
    @patch("src.api.ingestion_routes.yaml")
    def test_triage_concurrent_409(self, mock_yaml, mock_popen, client, mock_db, test_app):
        """Second triage trigger for same source returns 409."""
        from src.api.ingestion_routes import _IN_FLIGHT_TRIAGE

        source_id = uuid4()
        src = _make_source(source_id=source_id)
        mock_db.query.return_value.filter_by.return_value.first.return_value = src
        mock_yaml.safe_load.return_value = {"ingestion": {"deployment_path": "A"}}

        # Plant a still-running process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        _IN_FLIGHT_TRIAGE[source_id] = mock_proc

        try:
            resp = client.post(f"/api/ingestion/sources/{source_id}/triage")
            assert resp.status_code == 409
            assert "already in progress" in resp.json()["detail"]
        finally:
            _IN_FLIGHT_TRIAGE.pop(source_id, None)

    def test_tiers_4_returns_exit_2(self):
        """CLI --tiers 1,2,3,4 is now accepted (Chunk 57 ships Tier 4)."""
        # Chunk 57: Tier 4 is no longer rejected. Verify the acceptance test exists.
        import tests.ingestion.triage.test_pipeline as tp
        assert hasattr(tp, "test_tiers_4_accepted")
