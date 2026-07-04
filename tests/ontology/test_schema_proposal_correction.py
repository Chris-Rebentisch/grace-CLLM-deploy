"""Tests for POST /proposals/{id}/correct (CP6, D448).

Verifies correction carve-out: proposal_type UPDATE within 60-minute window,
422 on invalid ProposalType, 409 on second correction, 409 past window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.ontology.database import SchemaProposalRow
from src.shared.database import get_session_factory

UTC = timezone.utc


def _create_proposal(db, **overrides) -> SchemaProposalRow:
    """Insert a test proposal row and return it."""
    from src.ontology.database import OntologyVersionRow

    # Find or create an ontology version for FK
    version = db.query(OntologyVersionRow).first()
    if version is None:
        import hashlib
        version = OntologyVersionRow(
            id=uuid4(),
            version_number=99999,
            schema_json={},
            schema_modules={},
            hash_chain=hashlib.sha256(b"test").hexdigest(),
            source="manual",
        )
        db.add(version)
        db.flush()

    defaults = dict(
        id=uuid4(),
        proposal_type="add_entity_type",
        change_tier=1,
        kgcl_command="create class 'TestType'",
        proposed_diff={},
        evidence={},
        raw_confidence=1.0,
        priority="medium",
        status="pending",
        current_schema_version_id=version.id,
    )
    defaults.update(overrides)
    row = SchemaProposalRow(**defaults)
    db.add(row)
    db.commit()
    return row


class TestSchemaProposalCorrection:
    """Tests for the correction carve-out route."""

    def test_correction_succeeds_within_60_minute_window(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        factory = get_session_factory()
        db = factory()
        try:
            proposal = _create_proposal(db)
            pid = str(proposal.id)
        finally:
            db.close()

        client = TestClient(app)
        resp = client.post(
            f"/api/ontology/proposals/{pid}/correct",
            json={"proposal_type": "add_property", "reason": "wrong type at bootstrap"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["proposal_type"] == "add_property"
        assert data["is_correction"] is True

    def test_correction_422_on_invalid_proposal_type(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        factory = get_session_factory()
        db = factory()
        try:
            proposal = _create_proposal(db)
            pid = str(proposal.id)
        finally:
            db.close()

        client = TestClient(app)
        resp = client.post(
            f"/api/ontology/proposals/{pid}/correct",
            json={"proposal_type": "invalid_type", "reason": "test invalid"},
        )
        assert resp.status_code == 422

    def test_correction_409_on_second_correction(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        factory = get_session_factory()
        db = factory()
        try:
            proposal = _create_proposal(db)
            pid = str(proposal.id)
        finally:
            db.close()

        client = TestClient(app)
        # First correction
        resp1 = client.post(
            f"/api/ontology/proposals/{pid}/correct",
            json={"proposal_type": "add_property", "reason": "first correction"},
        )
        assert resp1.status_code == 200

        # Second correction — should be rejected (one-shot)
        resp2 = client.post(
            f"/api/ontology/proposals/{pid}/correct",
            json={"proposal_type": "split_type", "reason": "second correction"},
        )
        assert resp2.status_code == 409

    def test_correction_409_past_60_minute_window(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        factory = get_session_factory()
        db = factory()
        try:
            # Create a proposal with created_at > 60 minutes ago.
            # Must pass created_at at INSERT time — the trigger blocks UPDATE
            # on created_at (immutable column).
            old_time = datetime.now(UTC) - timedelta(minutes=61)
            proposal = _create_proposal(db, created_at=old_time)
            pid = proposal.id
        finally:
            db.close()

        client = TestClient(app)
        resp = client.post(
            f"/api/ontology/proposals/{str(pid)}/correct",
            json={"proposal_type": "add_property", "reason": "past window correction"},
        )
        assert resp.status_code == 409
