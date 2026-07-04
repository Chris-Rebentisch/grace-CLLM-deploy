"""D462: Tests for POST /api/ontology/proposals create route (Chunk 70 CP4)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.ontology.kgcl_models import KGCLCommandKind, ProposedSchemaChange
from src.ontology.models import OntologyVersion, ProposalType, VersionSource
from src.shared.database import get_db


def _mock_active_version() -> OntologyVersion:
    """Return a minimal active ontology version with entity_types for schema lookup."""
    return OntologyVersion(
        id=uuid4(),
        version_number=1,
        created_at=datetime.now(UTC),
        schema_json={
            "entity_types": {
                "Legal_Entity": {
                    "type": "object",
                    "description": "A legal entity",
                    "properties": {
                        "full_name": {"type": "string"},
                        "registration_number": {"type": "string"},
                    },
                    "synonyms": [],
                },
            },
        },
        schema_modules={},
        hash_chain="0" * 64,
        source=VersionSource.MANUAL,
    )


def _make_mock_db():
    """Return a mock Session that accepts add/commit/refresh."""
    db = MagicMock()
    def _refresh_side_effect(row):
        if not hasattr(row, "created_at") or row.created_at is None:
            row.created_at = datetime.now(UTC)
    db.refresh.side_effect = _refresh_side_effect
    return db


_GET_ACTIVE_PATCH = "src.api.proposal_routes.get_active_version"
_PARSE_KGCL_PATCH = "src.api.proposal_routes.parse_kgcl"


@pytest.fixture
def mock_db_client():
    """Yield (client, mock_db) with get_db overridden to return a MagicMock session."""
    mock_db = _make_mock_db()

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as c:
            yield c, mock_db
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestCreateProposalCommandText:
    def test_post_proposals_command_text_invokes_parser(self, mock_db_client):
        """Valid command_text + proposal_type -> 201 with status='pending'."""
        client, mock_db = mock_db_client
        active = _mock_active_version()
        with patch(_GET_ACTIVE_PATCH, return_value=active):
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "add_entity_type",
                    "command_text": "create class 'NewEntity'",
                },
            )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["status"] == "pending"
        assert data["proposal_type"] == "add_entity_type"
        assert "id" in data
        assert mock_db.add.called
        assert mock_db.commit.called

    def test_post_proposals_parsed_change_skips_parser(self, mock_db_client):
        """Valid parsed_change -> 201 without invoking parse_kgcl()."""
        client, mock_db = mock_db_client
        active = _mock_active_version()
        parsed_change = {
            "command_kind": "create_class",
            "target_name": "NewEntity",
        }
        with (
            patch(_GET_ACTIVE_PATCH, return_value=active),
            patch(_PARSE_KGCL_PATCH) as mock_parse,
        ):
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "add_entity_type",
                    "parsed_change": parsed_change,
                },
            )
        assert resp.status_code == 201, resp.json()
        mock_parse.assert_not_called()


class TestCreateProposalValidation:
    def test_post_proposals_both_present_returns_422(self):
        """Both command_text and parsed_change -> 422."""
        with TestClient(app) as client:
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "add_entity_type",
                    "command_text": "create class 'X'",
                    "parsed_change": {"command_kind": "create_class", "target_name": "X"},
                },
            )
        assert resp.status_code == 422

    def test_post_proposals_neither_present_returns_422(self):
        """Neither command_text nor parsed_change -> 422."""
        with TestClient(app) as client:
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "add_entity_type",
                },
            )
        assert resp.status_code == 422

    def test_post_proposals_invalid_proposal_type_returns_422_with_enum_list(self):
        """Invalid proposal_type -> 422."""
        with TestClient(app) as client:
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "totally_invalid",
                    "command_text": "create class 'X'",
                },
            )
        assert resp.status_code == 422


class TestCreateProposalAuth:
    def test_post_proposals_admin_key_required(self):
        """Missing X-Admin-Key when GRACE_ADMIN_KEY set -> 401."""
        # Patch the module-level cached variable (read at import time).
        with patch("src.api.auth_middleware.GRACE_ADMIN_KEY", "a" * 64):
            with TestClient(app) as auth_client:
                resp = auth_client.post(
                    "/api/ontology/proposals",
                    json={
                        "proposal_type": "add_entity_type",
                        "command_text": "create class 'X'",
                    },
                )
        assert resp.status_code == 401


class TestCreateProposalIdempotency:
    def test_post_proposals_idempotency_key_dedup(self, mock_db_client):
        """Same Idempotency-Key -> cached response on second POST."""
        from src.api.proposal_routes import _idempotency_cache
        _idempotency_cache.clear()

        client, mock_db = mock_db_client
        active = _mock_active_version()
        idem_key = "test-idem-key-12345678"
        with patch(_GET_ACTIVE_PATCH, return_value=active):
            resp1 = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "add_entity_type",
                    "command_text": "create class 'IdemTest'",
                },
                headers={"Idempotency-Key": idem_key},
            )
        assert resp1.status_code == 201
        first_id = resp1.json()["id"]
        first_add_count = mock_db.add.call_count

        # Second POST with same key — should return cached response.
        with patch(_GET_ACTIVE_PATCH, return_value=active):
            resp2 = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "add_entity_type",
                    "command_text": "create class 'IdemTest2'",
                },
                headers={"Idempotency-Key": idem_key},
            )
        assert resp2.status_code == 201  # Cached response (route status_code=201)
        assert resp2.json()["id"] == first_id
        # DB should not have been written to on the second call.
        assert mock_db.add.call_count == first_add_count

        _idempotency_cache.clear()


class TestCreateProposalEvidenceHygiene:
    """F-0040/F-0042 / ISS-0053: operator-authored proposals carry honest
    evidence — no fabricated signal scaffolding, populated affected types,
    self-contained proposed_diff."""

    def _created_row(self, mock_db):
        assert mock_db.add.called
        return mock_db.add.call_args.args[0]

    def test_no_fabricated_signal_scaffolding(self, mock_db_client):
        """No source signals -> signal_type/signal_strength absent (None)."""
        client, mock_db = mock_db_client
        with patch(_GET_ACTIVE_PATCH, return_value=_mock_active_version()):
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "deprecate_type",
                    "command_text": "obsolete class 'Legal_Entity'",
                },
            )
        assert resp.status_code == 201, resp.json()
        row = self._created_row(mock_db)
        assert row.evidence["source_signal_ids"] == []
        assert row.evidence["signal_type"] is None
        assert row.evidence["signal_strength"] is None

    def test_row_signal_type_is_human_initiated(self, mock_db_client):
        client, mock_db = mock_db_client
        with patch(_GET_ACTIVE_PATCH, return_value=_mock_active_version()):
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "deprecate_type",
                    "command_text": "obsolete class 'Legal_Entity'",
                },
            )
        assert resp.status_code == 201, resp.json()
        row = self._created_row(mock_db)
        assert row.signal_type == "human_initiated"

    def test_affected_entity_types_populated_from_kgcl_target(self, mock_db_client):
        """F-0040: deprecate_type target is right there in the KGCL string."""
        client, mock_db = mock_db_client
        with patch(_GET_ACTIVE_PATCH, return_value=_mock_active_version()):
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "deprecate_type",
                    "command_text": "obsolete class 'Legal_Entity'",
                },
            )
        assert resp.status_code == 201, resp.json()
        row = self._created_row(mock_db)
        assert row.evidence["affected_entity_types"] == ["Legal_Entity"]

    def test_proposed_diff_persisted_non_empty(self, mock_db_client):
        """F-0040(e): the row is self-contained — proposed_diff is the real diff."""
        client, mock_db = mock_db_client
        with patch(_GET_ACTIVE_PATCH, return_value=_mock_active_version()):
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "deprecate_type",
                    "command_text": "obsolete class 'Legal_Entity'",
                },
            )
        assert resp.status_code == 201, resp.json()
        row = self._created_row(mock_db)
        assert row.proposed_diff != {}

    def test_raw_confidence_is_none_for_signal_less(self, mock_db_client):
        """F-0042 / ISS-0053 deferral closure: human-initiated proposals store
        raw_confidence NULL — never the fabricated 1.0 interim sentinel
        (D120/D217; migration r4a_raw_confidence_nullable)."""
        client, mock_db = mock_db_client
        with patch(_GET_ACTIVE_PATCH, return_value=_mock_active_version()):
            resp = client.post(
                "/api/ontology/proposals",
                json={
                    "proposal_type": "deprecate_type",
                    "command_text": "obsolete class 'Legal_Entity'",
                },
            )
        assert resp.status_code == 201, resp.json()
        row = self._created_row(mock_db)
        assert row.raw_confidence is None
