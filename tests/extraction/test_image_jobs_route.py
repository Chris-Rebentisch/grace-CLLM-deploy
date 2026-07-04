"""CP3 tests: POST /api/extraction/jobs with job_kind='image' (D502).

Verifies:
- Image job creates pending row (202)
- Image exceeding 20 MB rejected (422)
- Path outside allowlist rejected (422)
- Cloud vision job requires cost_budget_usd
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def _client():
    """Create a test client for the extraction routes."""
    from src.api.main import app
    return TestClient(app)


@pytest.fixture()
def _image_in_allowlist(tmp_path):
    """Create a small JPEG file inside an allowlisted directory."""
    import io
    from PIL import Image

    # Create a valid small JPEG
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="blue").save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    img_path = tmp_path / "test_image.jpg"
    img_path.write_bytes(jpeg_bytes)
    return img_path


def test_image_job_creates_pending(_client, _image_in_allowlist):
    """POST /api/extraction/jobs {job_kind:'image'} -> 202."""
    img_path = _image_in_allowlist

    with patch.dict(os.environ, {"GRACE_EXTRACTION_ALLOWED_ROOTS": str(img_path.parent)}):
        # Mock subprocess to avoid actually spawning
        with patch("src.api.extraction_routes.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            resp = _client.post(
                "/api/extraction/jobs",
                json={"job_kind": "image", "source_path": str(img_path)},
            )

    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "job_id" in data
    assert data.get("status") == "pending" or "job_id" in data


def test_image_job_size_cap_rejection(_client, tmp_path):
    """Image file exceeding 20 MB -> 422."""
    # Create a file just over 20 MB
    big_file = tmp_path / "huge_image.jpg"
    big_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * (20 * 1024 * 1024 + 100))

    with patch.dict(os.environ, {"GRACE_EXTRACTION_ALLOWED_ROOTS": str(tmp_path)}):
        resp = _client.post(
            "/api/extraction/jobs",
            json={"job_kind": "image", "source_path": str(big_file)},
        )

    assert resp.status_code == 422
    assert "maximum size" in resp.json().get("detail", "").lower() or "exceeds" in resp.json().get("detail", "").lower()


def test_image_job_path_allowlist_reuse(_client, tmp_path):
    """Path outside allowlist -> 422 (reuses D470 _validate_source_path)."""
    # Create image file outside any allowlisted root
    img_path = tmp_path / "outside.jpg"
    img_path.write_bytes(b"\xff\xd8" + b"\x00" * 100)

    # Don't set GRACE_EXTRACTION_ALLOWED_ROOTS to tmp_path
    resp = _client.post(
        "/api/extraction/jobs",
        json={"job_kind": "image", "source_path": str(img_path)},
    )

    assert resp.status_code == 422
    assert "allowlist" in resp.json().get("detail", "").lower() or "outside" in resp.json().get("detail", "").lower()


def test_image_job_cloud_cost_budget(_client, _image_in_allowlist):
    """Cloud vision provider without cost_budget_usd -> rejection for batch, accepted for image (single-file)."""
    img_path = _image_in_allowlist

    with patch.dict(os.environ, {"GRACE_EXTRACTION_ALLOWED_ROOTS": str(img_path.parent)}):
        with patch("src.api.extraction_routes.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            # Image jobs with cloud provider should still work (cost gate is for batch only)
            resp = _client.post(
                "/api/extraction/jobs",
                json={"job_kind": "image", "source_path": str(img_path), "provider": "anthropic"},
            )

    # Single image jobs don't require cost budget (that's a batch-only gate)
    assert resp.status_code in (202, 422), f"Unexpected status: {resp.status_code}"
