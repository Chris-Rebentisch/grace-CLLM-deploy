"""F-0043 / ISS-0050 — daemon confirm/revert field-name aliases.

Revert required ``reverted_by`` while confirm's route family used
``reviewer`` — a 422 on first try for operators. Both routes now accept
BOTH field names additively (preferred name unchanged per route).

Pure unit tests: pydantic model validation + route-level checks with a
mocked DB session (no Postgres, no ArcadeDB).
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.daemon_routes import ConfirmRequest, RevertRequest, router


# --- Model-level alias tests -------------------------------------------------


class TestRevertRequestAliases:
    def test_documented_field_name(self):
        req = RevertRequest.model_validate({"reverted_by": "op@example.com"})
        assert req.reverted_by == "op@example.com"

    def test_reviewer_alias_accepted(self):
        req = RevertRequest.model_validate({"reviewer": "op@example.com"})
        assert req.reverted_by == "op@example.com"

    def test_missing_both_rejected(self):
        with pytest.raises(Exception):
            RevertRequest.model_validate({"reason": "nope"})


class TestConfirmRequestAliases:
    def test_documented_field_name(self):
        req = ConfirmRequest.model_validate({"reviewer": "op@example.com"})
        assert req.reviewer == "op@example.com"

    def test_reverted_by_alias_accepted(self):
        req = ConfirmRequest.model_validate({"reverted_by": "op@example.com"})
        assert req.reviewer == "op@example.com"

    def test_empty_body_allowed(self):
        req = ConfirmRequest.model_validate({})
        assert req.reviewer is None


# --- Route-level tests (mocked DB) -------------------------------------------


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(router)
    return a


@pytest.fixture
def mock_db(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr("src.api.daemon_routes._get_db", lambda: db)
    return db


@pytest.fixture
def client(app):
    return TestClient(app)


def _cooling_proposal():
    proposal = MagicMock()
    proposal.status = "cooling"
    proposal.change_tier = 1
    proposal.kgcl_command = "create class 'X'"
    return proposal


class TestConfirmRouteAliases:
    @pytest.mark.parametrize(
        "body",
        [None, {"reviewer": "ops@example.com"}, {"reverted_by": "ops@example.com"}],
    )
    def test_confirm_accepts_both_aliases_and_no_body(self, client, mock_db, body):
        proposal = _cooling_proposal()
        mock_db.query.return_value.filter_by.return_value.first.return_value = proposal

        pid = uuid4()
        if body is None:
            resp = client.post(f"/api/ontology/daemon/{pid}/confirm")
        else:
            resp = client.post(f"/api/ontology/daemon/{pid}/confirm", json=body)

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "applied"


class TestRevertRouteAliases:
    @pytest.mark.parametrize("field_name", ["reverted_by", "reviewer"])
    def test_revert_body_validation_passes_with_either_field(
        self, client, mock_db, monkeypatch, field_name
    ):
        """Either field name clears FastAPI body validation.

        kgcl_invert is stubbed to return None so the route exits with the
        domain-level 422 ("not revertible") — proving the request body itself
        was accepted (the pre-fix failure was a pydantic missing-field 422
        before the route body ever ran).
        """
        proposal = _cooling_proposal()
        mock_db.query.return_value.filter_by.return_value.first.return_value = proposal
        monkeypatch.setattr("src.api.daemon_routes.kgcl_invert", lambda cmd: None)

        pid = uuid4()
        resp = client.post(
            f"/api/ontology/daemon/{pid}/revert",
            json={field_name: "ops@example.com", "reason": "test"},
        )
        assert resp.status_code == 422
        assert "not revertible" in resp.json()["detail"]

    def test_revert_missing_operator_field_still_422s_as_body_error(
        self, client, mock_db
    ):
        pid = uuid4()
        resp = client.post(
            f"/api/ontology/daemon/{pid}/revert", json={"reason": "no operator"}
        )
        assert resp.status_code == 422
        # FastAPI validation error shape (list of error dicts), not the
        # domain-level "not revertible" string.
        assert isinstance(resp.json()["detail"], list)
