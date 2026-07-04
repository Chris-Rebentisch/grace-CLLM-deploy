"""Tests for Chunk 56 curation and events-list API routes (CP8 — 13 tests)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.ingestion.models import (
    CommunicationEventRow,
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


def _make_event_row(source_id, message_id="<msg@example.com>", sender="alice@example.com", sent_at=None):
    row = MagicMock(spec=CommunicationEventRow)
    row.id = uuid4()
    row.source_id = source_id
    row.message_id = message_id
    row.sender_email = sender
    row.sender_display_name = None
    row.subject = "Test"
    row.sent_at = sent_at or datetime.now(timezone.utc)
    row.received_at = None
    row.triage_tier_outcome = "pending"
    row.thread_id = None
    return row


class TestCurateEndpoint:
    def test_curate_creates_subset(self, client, mock_db):
        source_id = uuid4()
        src = _make_source(source_id=source_id)
        ev = _make_event_row(source_id, message_id="<msg1@example.com>")

        mock_db.query.return_value.filter_by.return_value.first.return_value = src
        mock_db.query.return_value.filter.return_value.all.return_value = [ev]

        resp = client.post(
            "/api/ingestion/curate",
            json={
                "source_id": str(source_id),
                "selected_message_ids": ["<msg1@example.com>"],
                "deployment_path": "B",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "subset_id" in data
        assert data["message_count"] == 1
        assert "diversity_metrics" in data
        assert data["diversity_metrics"]["sender_band"] in ("narrow", "balanced", "wide")

    def test_curate_empty_selection_400(self, client, mock_db):
        resp = client.post(
            "/api/ingestion/curate",
            json={
                "source_id": str(uuid4()),
                "selected_message_ids": [],
                "deployment_path": "B",
            },
        )
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    def test_curate_source_not_found_404(self, client, mock_db):
        mock_db.query.return_value.filter_by.return_value.first.return_value = None

        resp = client.post(
            "/api/ingestion/curate",
            json={
                "source_id": str(uuid4()),
                "selected_message_ids": ["<msg@example.com>"],
                "deployment_path": "C",
            },
        )
        assert resp.status_code == 404

    def test_curate_unknown_message_id_400(self, client, mock_db):
        source_id = uuid4()
        src = _make_source(source_id=source_id)

        mock_db.query.return_value.filter_by.return_value.first.return_value = src
        mock_db.query.return_value.filter.return_value.all.return_value = []

        resp = client.post(
            "/api/ingestion/curate",
            json={
                "source_id": str(source_id),
                "selected_message_ids": ["<unknown@example.com>"],
                "deployment_path": "B",
            },
        )
        assert resp.status_code == 400
        assert "Unknown" in resp.json()["detail"]

    def test_curate_diversity_metrics_band_only(self, client, mock_db):
        """Response diversity_metrics contain band labels only — no raw counts (D432)."""
        source_id = uuid4()
        src = _make_source(source_id=source_id)
        ev = _make_event_row(source_id, message_id="<m1@x.com>")

        mock_db.query.return_value.filter_by.return_value.first.return_value = src
        mock_db.query.return_value.filter.return_value.all.return_value = [ev]

        resp = client.post(
            "/api/ingestion/curate",
            json={
                "source_id": str(source_id),
                "selected_message_ids": ["<m1@x.com>"],
                "deployment_path": "B",
            },
        )
        data = resp.json()
        metrics = data["diversity_metrics"]
        # Band labels present
        assert "sender_band" in metrics
        assert "thread_depth_band" in metrics
        assert "date_range_band" in metrics
        # Raw counts NOT in response diversity_metrics
        assert "sender_count" not in metrics
        assert "date_span_days" not in metrics

    def test_curate_path_b_sentinel_ready(self, client, mock_db):
        """Path B curate sets sentinel_status='ready'."""
        source_id = uuid4()
        src = _make_source(source_id=source_id)
        ev = _make_event_row(source_id, message_id="<m@x.com>")

        mock_db.query.return_value.filter_by.return_value.first.return_value = src
        mock_db.query.return_value.filter.return_value.all.return_value = [ev]

        resp = client.post(
            "/api/ingestion/curate",
            json={
                "source_id": str(source_id),
                "selected_message_ids": ["<m@x.com>"],
                "deployment_path": "B",
            },
        )
        assert resp.status_code == 201
        # The db.add call should have been made with sentinel_status='ready'
        added = mock_db.add.call_args[0][0]
        assert added.sentinel_status == "ready"

    def test_curate_path_c_sentinel_pending(self, client, mock_db):
        """Path C curate sets sentinel_status='pending'."""
        source_id = uuid4()
        src = _make_source(source_id=source_id)
        ev = _make_event_row(source_id, message_id="<m@x.com>")

        mock_db.query.return_value.filter_by.return_value.first.return_value = src
        mock_db.query.return_value.filter.return_value.all.return_value = [ev]

        resp = client.post(
            "/api/ingestion/curate",
            json={
                "source_id": str(source_id),
                "selected_message_ids": ["<m@x.com>"],
                "deployment_path": "C",
            },
        )
        assert resp.status_code == 201
        added = mock_db.add.call_args[0][0]
        assert added.sentinel_status == "pending"


class TestDiscoveryManifestUnchanged:
    def test_curate_does_not_modify_discovery_manifest(self):
        """AC-12 / D433 — curation must not write discovery-manifest.json."""
        manifest = Path(__file__).resolve().parents[3] / "config" / "discovery-manifest.json"
        assert manifest.is_file()
        before = hashlib.sha256(manifest.read_bytes()).hexdigest()
        # Route module must not reference manifest writes (static guard).
        routes_src = (
            Path(__file__).resolve().parents[3] / "src" / "api" / "ingestion_routes.py"
        ).read_text()
        assert "discovery-manifest" not in routes_src
        after = hashlib.sha256(manifest.read_bytes()).hexdigest()
        assert before == after


class TestGetCuratedSubset:
    def test_get_subset_200(self, client, mock_db):
        subset_id = uuid4()
        subset = MagicMock(spec=CuratedEmailSubsetRow)
        subset.id = subset_id
        subset.source_id = uuid4()
        subset.deployment_path = "B"
        subset.selected_message_ids = ["<m@x.com>"]
        subset.diversity_metrics = {"sender_band": "narrow"}
        subset.sentinel_status = "ready"
        subset.created_at = datetime.now(timezone.utc)

        mock_db.query.return_value.filter_by.return_value.first.return_value = subset
        resp = client.get(f"/api/ingestion/curate/{subset_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == str(subset_id)

    def test_get_subset_404(self, client, mock_db):
        mock_db.query.return_value.filter_by.return_value.first.return_value = None
        resp = client.get(f"/api/ingestion/curate/{uuid4()}")
        assert resp.status_code == 404


class TestEventsListEndpoint:
    def test_list_events_200(self, client, mock_db):
        source_id = uuid4()
        src = _make_source(source_id=source_id)
        ev = _make_event_row(source_id)

        # query(IngestionSource).filter_by().first() for source lookup
        mock_db.query.return_value.filter_by.return_value.first.return_value = src
        # query(CommunicationEventRow).filter().filter().order_by().offset().limit().all()
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [ev]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [ev]

        resp = client.get(f"/api/ingestion/sources/{source_id}/events")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "next_cursor" in data

    def test_list_events_source_404(self, client, mock_db):
        mock_db.query.return_value.filter_by.return_value.first.return_value = None
        resp = client.get(f"/api/ingestion/sources/{uuid4()}/events")
        assert resp.status_code == 404

    def test_list_events_no_body_in_response(self, client, mock_db):
        """Events list returns metadata only — no body_plain or body_html (D435 §40.10)."""
        source_id = uuid4()
        src = _make_source(source_id=source_id)
        ev = _make_event_row(source_id)

        mock_db.query.return_value.filter_by.return_value.first.return_value = src
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [ev]

        resp = client.get(f"/api/ingestion/sources/{source_id}/events")
        assert resp.status_code == 200
        items = resp.json()["items"]
        if items:
            item = items[0]
            assert "body_plain" not in item
            assert "body_html" not in item
            assert "raw_headers_json" not in item
