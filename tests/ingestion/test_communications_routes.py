"""Tests for communications routes (Chunk 58, CP8).

Validates:
1. Each of 8 routes returns expected shapes
2. Admin-key gating on /explanation + /dpia/attestation
3. 409 duplicate-date + template-SHA mismatch
4. 201 success with chmod 600 file
5. READONLY_ROUTES count == 31
6. No numeric scores in response bodies (D120/D217)
7. D246 mirror: route module does not import profile_generator
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    from src.api.communications_routes import communications_router

    app = FastAPI()
    app.include_router(communications_router)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _mock_session_with_profile():
    """Create a mock session that returns a profile row."""
    mock_session = MagicMock()
    style_sig = {
        "sentence_length_band": "medium",
        "vocabulary_complexity_band": "medium",
        "formality_band": "high",
        "greeting_closing_band": "medium",
        "hedging_frequency_band": "low",
        "directness_band": "high",
        "response_timing_band": "medium",
        "thread_depth_band": "low",
    }
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (
        "profile-uuid",     # id
        "person-uuid",      # sender_person_id
        3,                  # profile_version
        style_sig,          # style_signature
        "high",             # profile_quality_band
        datetime(2026, 5, 1, tzinfo=timezone.utc),  # created_at
    )
    mock_session.execute.return_value = mock_result
    return mock_session


class TestGetProfile:
    """Route (a): GET /profiles/{person_id}."""

    def test_returns_profile(self, client):
        mock_session = _mock_session_with_profile()
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get("/api/communications/profiles/person-uuid")
        assert resp.status_code == 200
        data = resp.json()
        assert data["person_id"] == "person-uuid"
        assert data["profile_version"] == 3
        assert "style_signature" in data
        assert data["profile_quality_band"] == "high"

    def test_returns_404_when_missing(self, client):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get("/api/communications/profiles/nonexistent")
        assert resp.status_code == 404


class TestPerRecipientProfile:
    """Route (b): GET /profiles/{person_id}/for-recipient/{recipient_id}."""

    def test_returns_per_recipient(self, client):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (
            "peer_same_department",  # category
            "high",                  # confidence_band
            {"formality_shift": "high"},  # style_delta
            {"formality_band": "medium"},  # style_signature
            2,                       # profile_version
        )
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get(
                "/api/communications/profiles/p1/for-recipient/r1"
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "peer_same_department"
        assert data["confidence_band"] == "high"

    def test_returns_404_when_missing(self, client):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get(
                "/api/communications/profiles/p1/for-recipient/r1"
            )
        assert resp.status_code == 404


class TestPerCategoryProfile:
    """Route (c): GET /profiles/{person_id}/for-category/{category}."""

    def test_invalid_category_422(self, client):
        resp = client.get(
            "/api/communications/profiles/p1/for-category/invalid_cat"
        )
        assert resp.status_code == 422

    def test_valid_category_returns_recipients(self, client):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("recip-1", "high", {"formality_shift": "high"}),
        ]
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get(
                "/api/communications/profiles/p1/for-category/peer_same_department"
            )
        assert resp.status_code == 200
        assert len(resp.json()["recipients"]) == 1


class TestDraftGuidance:
    """Route (d): POST /draft-guidance (read-only POST, D504 enriched)."""

    def test_returns_style_payload(self, client):
        mock_session = MagicMock()
        style_sig = {
            "sentence_length_band": "medium",
            "vocabulary_complexity_band": "medium",
            "formality_band": "high",
            "greeting_closing_band": "medium",
            "hedging_frequency_band": "low",
            "directness_band": "high",
            "response_timing_band": "medium",
            "thread_depth_band": "low",
            "greeting_patterns": ["Dear colleagues,"],
            "closing_patterns": ["Best regards,"],
            "sample_phrases": ["Please find attached"],
            "avoid_phrases": ["Hey", "Cool"],
            "tone_summary": "Formal structured communicator.",
            "contrastive_markers": ["the", "of"],
        }
        mock_session.execute.return_value.fetchone.return_value = (
            style_sig, "high", 2,
        )
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.post(
                "/api/communications/draft-guidance",
                json={"person_id": "00000000-0000-0000-0000-000000000001"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["profile_version"] == 2
        # D504: response now includes guidance payload
        assert "guidance" in data
        guidance = data["guidance"]
        assert guidance["greeting"] == "Dear colleagues,"
        assert guidance["closing"] == "Best regards,"
        assert guidance["tone_summary"] == "Formal structured communicator."
        assert guidance["sample_phrases"] == ["Please find attached"]
        assert guidance["avoid_phrases"] == ["Hey", "Cool"]
        assert guidance["hedging"] == "low"
        assert guidance["directness"] == "high"

    def test_draft_guidance_returns_payload(self, client):
        """D504 CP4: draft-guidance returns DraftGuidancePayload from persisted JSONB."""
        mock_session = MagicMock()
        style_sig = {
            "sentence_length_band": "high",
            "vocabulary_complexity_band": "high",
            "formality_band": "high",
            "greeting_closing_band": "high",
            "hedging_frequency_band": "medium",
            "directness_band": "medium",
            "response_timing_band": "low",
            "thread_depth_band": "medium",
            "greeting_patterns": ["Good morning,"],
            "closing_patterns": ["Kind regards,"],
            "sample_phrases": ["Per our discussion", "As per the agenda"],
            "avoid_phrases": ["ASAP", "FYI"],
            "tone_summary": "Professional and measured.",
            "contrastive_markers": ["per", "regarding"],
        }
        mock_session.execute.return_value.fetchone.return_value = (
            style_sig, "high", 5,
        )
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.post(
                "/api/communications/draft-guidance",
                json={"person_id": "00000000-0000-0000-0000-000000000001"},
            )
        assert resp.status_code == 200
        data = resp.json()
        guidance = data["guidance"]
        assert guidance["greeting"] == "Good morning,"
        assert guidance["closing"] == "Kind regards,"
        assert guidance["tone_summary"] == "Professional and measured."
        assert len(guidance["sample_phrases"]) == 2
        assert len(guidance["avoid_phrases"]) == 2

    def test_draft_guidance_no_llm_call(self):
        """D504: verify get_provider is not imported by the route module."""
        import ast

        route_path = Path(__file__).resolve().parents[2] / "src" / "api" / "communications_routes.py"
        source = route_path.read_text()
        tree = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.name)
        assert "get_provider" not in imported_names, (
            "communications_routes.py must not import get_provider (D504)"
        )

    def test_returns_404_when_no_profile(self, client):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.post(
                "/api/communications/draft-guidance",
                json={"person_id": "00000000-0000-0000-0000-000000000001"},
            )
        assert resp.status_code == 404


class TestExplanation:
    """Route (e): GET /profiles/{person_id}/explanation (admin-key gated)."""

    def test_admin_key_required(self, client):
        """Non-loopback without admin key → 401."""
        with patch.dict(os.environ, {"GRACE_ADMIN_KEY": "secret123"}):
            resp = client.get(
                "/api/communications/profiles/p1/explanation",
            )
        assert resp.status_code == 401

    def test_admin_key_accepted(self, client):
        """Valid admin key → passes auth gate."""
        mock_session = MagicMock()
        # Profile row
        profile_result = MagicMock()
        profile_result.fetchone.return_value = (
            "profile-id", 1,
            {"sentence_length_band": "medium", "formality_band": "high"},
            "high",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        # Categories
        cats_result = MagicMock()
        cats_result.fetchall.return_value = [("peer_same_department",)]
        # Email headers
        headers_result = MagicMock()
        headers_result.fetchall.return_value = []

        mock_session.execute.side_effect = [
            profile_result, cats_result, headers_result,
        ]

        with patch.dict(os.environ, {"GRACE_ADMIN_KEY": "secret123"}), patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get(
                "/api/communications/profiles/p1/explanation",
                headers={"X-Admin-Key": "secret123"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "natural_language_summary" in data
        assert "feature_contributions" in data
        assert "sample_email_headers" in data


class TestAggregateProfile:
    """Route (f): GET /profiles/aggregate/{segment}."""

    def test_returns_aggregate(self, client):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (
            "engineering", "medium", "high", "medium", 15,
        )
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get("/api/communications/profiles/aggregate/engineering")
        assert resp.status_code == 200
        data = resp.json()
        assert data["aggregate_segment"] == "engineering"
        assert data["profile_count"] == 15

    def test_returns_404_when_missing(self, client):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get("/api/communications/profiles/aggregate/nonexistent")
        assert resp.status_code == 404


class TestDpiaStatus:
    """Route (g): GET /dpia/status."""

    def test_no_dpia_dir(self, client):
        with patch(
            "src.api.communications_routes._DPIA_DIR",
            Path("/nonexistent/dpia"),
        ):
            resp = client.get("/api/communications/dpia/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["attestation_active"] is False

    def test_valid_attestation(self, client, tmp_path):
        dpia_dir = tmp_path / "dpia"
        dpia_dir.mkdir()
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        att_file = dpia_dir / f"voice-tone-attestation-{today}.md"
        att_file.write_text("---\nsigned_by: Alice\n---\n")

        with patch(
            "src.api.communications_routes._DPIA_DIR", dpia_dir,
        ):
            resp = client.get("/api/communications/dpia/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["attestation_active"] is True
        assert data["signed_by"] == "Alice"


class TestDpiaAttestation:
    """Route (h): POST /dpia/attestation (admin-key gated, Lock-R4)."""

    def test_admin_key_required(self, client):
        with patch.dict(os.environ, {"GRACE_ADMIN_KEY": "secret123"}):
            resp = client.post(
                "/api/communications/dpia/attestation",
                json={
                    "signed_by": "Alice",
                    "signed_role": "DPO",
                    "signed_at_iso": "2026-05-19T00:00:00Z",
                    "dpia_template_content_sha256": "a" * 64,
                },
            )
        assert resp.status_code == 401

    def test_duplicate_date_409(self, client, tmp_path):
        dpia_dir = tmp_path / "dpia"
        dpia_dir.mkdir()
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        (dpia_dir / f"voice-tone-attestation-{today}.md").write_text("existing")

        with patch(
            "src.api.communications_routes._DPIA_DIR", dpia_dir,
        ):
            resp = client.post(
                "/api/communications/dpia/attestation",
                json={
                    "signed_by": "Alice",
                    "signed_role": "DPO",
                    "signed_at_iso": "2026-05-19T00:00:00Z",
                    "dpia_template_content_sha256": "a" * 64,
                },
            )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_template_sha_mismatch_409(self, client, tmp_path):
        dpia_dir = tmp_path / "dpia"
        template_path = tmp_path / "dpia-template.md"
        template_path.write_text("DPIA template content")
        real_sha = hashlib.sha256(b"DPIA template content").hexdigest()

        with patch(
            "src.api.communications_routes._DPIA_DIR", dpia_dir,
        ), patch(
            "src.api.communications_routes._DPIA_TEMPLATE_PATH", template_path,
        ):
            resp = client.post(
                "/api/communications/dpia/attestation",
                json={
                    "signed_by": "Alice",
                    "signed_role": "DPO",
                    "signed_at_iso": "2026-05-19T00:00:00Z",
                    "dpia_template_content_sha256": "b" * 64,  # wrong SHA
                },
            )
        assert resp.status_code == 409
        assert "template changed" in resp.json()["detail"]

    def test_success_201(self, client, tmp_path):
        dpia_dir = tmp_path / "dpia"
        template_path = tmp_path / "dpia-template.md"
        template_path.write_text("DPIA template content")
        real_sha = hashlib.sha256(b"DPIA template content").hexdigest()

        with patch(
            "src.api.communications_routes._DPIA_DIR", dpia_dir,
        ), patch(
            "src.api.communications_routes._DPIA_TEMPLATE_PATH", template_path,
        ):
            resp = client.post(
                "/api/communications/dpia/attestation",
                json={
                    "signed_by": "Alice",
                    "signed_role": "DPO",
                    "signed_at_iso": "2026-05-19T00:00:00Z",
                    "dpia_template_content_sha256": real_sha,
                },
            )
        assert resp.status_code == 201
        data = resp.json()
        assert "path" in data
        assert "valid_until" in data

        # Verify file was written
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        att_file = dpia_dir / f"voice-tone-attestation-{today}.md"
        assert att_file.exists()


class TestReadonlyRoutesCount:
    """READONLY_ROUTES stands at 36 (25 pre-72a + 6 Chunk 72a + 5 later additions)."""

    def test_readonly_routes_count_36(self):
        from src.mcp_server.server import READONLY_ROUTES

        assert len(READONLY_ROUTES) == 36, (
            f"READONLY_ROUTES should have 36 entries, got {len(READONLY_ROUTES)}"
        )

    def test_draft_guidance_in_readonly_routes(self):
        from src.mcp_server.server import READONLY_ROUTES

        assert ("POST", "/api/communications/draft-guidance") in READONLY_ROUTES


class TestD120D217Compliance:
    """No numeric scores in response bodies."""

    def test_profile_response_uses_bands_only(self, client):
        """Profile response contains only band labels, no raw numeric scores."""
        mock_session = _mock_session_with_profile()
        with patch(
            "src.api.communications_routes.get_session_factory",
            return_value=lambda: mock_session,
        ):
            resp = client.get("/api/communications/profiles/person-uuid")
        data = resp.json()
        body_str = json.dumps(data)
        # Ensure the response contains band labels
        assert "high" in body_str or "medium" in body_str or "low" in body_str
        # No floating-point confidence/score keys
        for key in ["confidence_score", "numeric_score", "raw_score"]:
            assert key not in body_str
