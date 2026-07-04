"""Regression tests for image_pipeline interface-drift fixes (validation run).

F-12: job-status updater imported a nonexistent module (src.api.database).
F-11: process_image ignored the llm.vision config block (bare get_provider()).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from src.extraction import image_pipeline


def test_f12_update_job_status_is_callable():
    """F-12: the job-status updater symbol must exist and be callable.

    The old `from src.api.database import get_session_factory` raised
    ModuleNotFoundError at call time (swallowed by the except), so job status
    never updated. This asserts the symbol resolves.
    """
    assert callable(image_pipeline._update_job_status)


def test_f12_update_job_status_imports_shared_database():
    """F-12: the updater must import from src.shared.database, not src.api.database."""
    src = inspect.getsource(image_pipeline._update_job_status)
    assert "from src.shared.database import get_session_factory" in src
    # The broken import path must not appear as an actual import statement
    # (comments referencing it for capture-the-why are fine).
    assert "from src.api.database import" not in src


def test_f12_get_session_factory_importable():
    """F-12: the real home of get_session_factory must import cleanly."""
    from src.shared.database import get_session_factory  # noqa: F401


@pytest.mark.asyncio
async def test_f11_process_image_uses_vision_config(tmp_path):
    """F-11: the vision path must request the llm.vision config override.

    We feed a tiny 'photo' (no OCR text) so process_image takes the vision
    branch, mock get_provider + read_vision_config_from_yaml, and assert the
    provider was constructed with config_override=<vision config>.
    """
    # 1x1 PNG bytes (no embedded text -> classified as 'photo').
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d49444154789c6360000002000100ffff0300000600"
        "0557bfabd40000000049454e44ae426082"
    )
    img_path = tmp_path / "damage.png"
    img_path.write_bytes(png)

    fake_vision_config = {"provider": "ollama", "model": "qwen2.5-vl:32b", "enabled": True}

    fake_provider = MagicMock()
    fake_provider.provider_name = "ollama"

    async def _fake_generate_vision(**kwargs):
        resp = MagicMock()
        resp.parsed = None
        return resp

    fake_provider.generate_vision = _fake_generate_vision

    with patch(
        "src.shared.llm_provider.read_vision_config_from_yaml",
        return_value=fake_vision_config,
    ) as mock_cfg, patch(
        "src.shared.llm_provider.get_provider",
        return_value=fake_provider,
    ) as mock_get_provider:
        result = await image_pipeline.process_image(img_path, job_id="j1", persist=False)

    assert result["image_class"] == "photo"
    mock_cfg.assert_called_once()
    # get_provider must have been called with the vision config as override.
    assert mock_get_provider.called
    _, kwargs = mock_get_provider.call_args
    assert kwargs.get("config_override") == fake_vision_config
