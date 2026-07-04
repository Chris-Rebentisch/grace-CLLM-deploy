"""F-0031 / ISS-0047 — registration-time SourceConfig validation.

POST /api/ingestion/sources previously persisted config_json unvalidated;
a config missing the tagged-union discriminator (or required adapter fields)
only failed at cycle time as a raw pydantic union_tag_not_found. These tests
cover the registration-time validation (helper + route), the PATCH surface,
and the F-0030d deployment-path guidance rider.

Pure unit tests — DB and Arcade dependencies are mocked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.ingestion_routes import _validate_source_config
from src.ingestion.models import IngestionSource


# --- Fixtures (mirrors tests/ingestion/test_ingestion_routes.py) -----------


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
    from src.graph.arcade_client import get_arcade_client
    from src.shared.database import get_db

    def _override_db():
        yield mock_db

    def _override_arcade():
        return AsyncMock()

    test_app.dependency_overrides[get_db] = _override_db
    test_app.dependency_overrides[get_arcade_client] = _override_arcade

    with TestClient(test_app) as c:
        yield c

    test_app.dependency_overrides.clear()


def _make_source_row(source_type="mbox", config_json=None):
    src = MagicMock(spec=IngestionSource)
    src.id = uuid4()
    src.name = "row"
    src.source_type = source_type
    src.config_json = config_json or {"source_type": source_type, "file_path": "/t.mbox"}
    src.segment = "insurance"
    src.enabled = True
    src.status = "pending"
    src.created_at = datetime.now(timezone.utc)
    src.deleted_at = None
    return src


# --- Helper-level tests ------------------------------------------------------


class TestValidateSourceConfigHelper:
    def test_missing_discriminator_is_defaulted_and_validates(self):
        cfg = _validate_source_config("mbox", {"file_path": "/data/mail.mbox"})
        assert cfg["source_type"] == "mbox"
        assert cfg["file_path"] == "/data/mail.mbox"

    def test_explicit_matching_discriminator_passes(self):
        cfg = _validate_source_config(
            "eml", {"source_type": "eml", "directory_path": "/data/eml/"}
        )
        assert cfg["source_type"] == "eml"

    def test_mismatched_discriminator_422(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_source_config(
                "mbox", {"source_type": "eml", "directory_path": "/data/"}
            )
        assert exc_info.value.status_code == 422
        assert "does not match" in exc_info.value.detail

    def test_missing_required_field_422_with_helpful_detail(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_source_config("mbox", {})
        assert exc_info.value.status_code == 422
        detail = exc_info.value.detail
        assert "file_path" in detail
        assert "mbox" in detail

    def test_unknown_source_type_422_lists_valid_types(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_source_config("carrier-pigeon", {"file_path": "/x"})
        assert exc_info.value.status_code == 422
        assert "mbox" in str(exc_info.value.detail)

    def test_none_config_defaults_discriminator_then_reports_missing_fields(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_source_config("pst", None)
        assert exc_info.value.status_code == 422
        assert "file_path" in exc_info.value.detail


# --- Route-level tests -------------------------------------------------------


class TestCreateSourceValidation:
    def test_create_with_missing_discriminator_defaults_and_persists_it(
        self, client, mock_db
    ):
        """The normalized config (discriminator injected) reaches the ORM row."""
        with patch("src.api.ingestion_routes.IngestionSource") as MockSource:
            MockSource.return_value = _make_source_row()
            resp = client.post(
                "/api/ingestion/sources",
                json={
                    "name": "whitfield-mbox",
                    "source_type": "mbox",
                    "config_json": {"file_path": "/data/mail.mbox"},
                    "segment": "insurance",
                },
            )
            assert resp.status_code == 201
            persisted_config = MockSource.call_args.kwargs["config_json"]
            assert persisted_config["source_type"] == "mbox"

    def test_create_with_invalid_config_422s_with_detail(self, client, mock_db):
        resp = client.post(
            "/api/ingestion/sources",
            json={
                "name": "broken",
                "source_type": "mbox",
                "config_json": {},
                "segment": "insurance",
            },
        )
        assert resp.status_code == 422
        assert "file_path" in resp.json()["detail"]
        mock_db.add.assert_not_called()

    def test_create_with_unknown_type_422s(self, client, mock_db):
        resp = client.post(
            "/api/ingestion/sources",
            json={
                "name": "bad-type",
                "source_type": "pigeon",
                "config_json": {"file_path": "/x"},
                "segment": "insurance",
            },
        )
        assert resp.status_code == 422
        mock_db.add.assert_not_called()


class TestPatchSourceValidation:
    def test_patch_with_invalid_config_422s(self, client, mock_db):
        row = _make_source_row()
        mock_db.query.return_value.filter_by.return_value.first.return_value = row
        resp = client.patch(
            f"/api/ingestion/sources/{row.id}",
            json={"config_json": {"nonsense": True}},
        )
        assert resp.status_code == 422
        assert "file_path" in resp.json()["detail"]

    def test_patch_with_valid_config_normalizes_discriminator(self, client, mock_db):
        row = _make_source_row()
        mock_db.query.return_value.filter_by.return_value.first.return_value = row
        resp = client.patch(
            f"/api/ingestion/sources/{row.id}",
            json={"config_json": {"file_path": "/data/new.mbox"}},
        )
        assert resp.status_code == 200
        assert row.config_json["source_type"] == "mbox"
        assert row.config_json["file_path"] == "/data/new.mbox"


class TestDeploymentPathGuidance:
    """F-0030d rider: bad deployment_path values get guidance, not literal_error."""

    def test_patch_deployment_path_invalid_value_422(self, client):
        resp = client.patch(
            "/api/ingestion/config/deployment-path",
            json={"deployment_path": "X"},
        )
        assert resp.status_code == 422
        assert "'A', 'B', 'C'" in resp.json()["detail"] or "must be" in resp.json()["detail"]
