"""CP5 contract tests for image_pipeline.py (D246/D501/D503).

Tests verify:
1. CLI argparser accepts the documented argv contract.
2. process_image returns correct structure for photo-class images.
3. process_image returns correct structure for document-class images.
4. ArcadeDB persistence + idempotency (mocked client).
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_tiny_jpeg(tmp_path: Path, name: str = "test.jpg") -> Path:
    """Create a real 1x1 JPEG on disk for pipeline tests."""
    from PIL import Image

    p = tmp_path / name
    img = Image.new("RGB", (1, 1), color=(255, 0, 0))
    img.save(p, format="JPEG")
    return p


def test_image_pipeline_argparser_contract():
    """D476: CLI argparser accepts --job-id and --source-path."""
    from src.extraction.image_pipeline import _build_argparser

    parser = _build_argparser()
    args = parser.parse_args(["--job-id", "abc-123", "--source-path", "/tmp/img.jpg"])
    assert args.job_id == "abc-123"
    assert args.source_path == "/tmp/img.jpg"


def test_process_image_photo_class(tmp_path):
    """D501: process_image returns ImageAsset with photo classification for small images."""
    jpeg_path = _make_tiny_jpeg(tmp_path)

    mock_provider = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = None
    mock_provider.generate_vision = AsyncMock(return_value=mock_response)
    mock_provider.provider_name.return_value = "ollama"

    with patch("src.shared.llm_provider.get_provider", return_value=mock_provider):
        from src.extraction.image_pipeline import process_image

        result = asyncio.run(process_image(jpeg_path))

    assert result["image_class"] == "photo"
    assert result["content_sha256"]
    assert result["asset"].media_type == "image/jpeg"
    assert result["asset"].grace_id


def test_process_image_vision_scene_fields(tmp_path):
    """F-011 / ISS-0018: vision prompt requests scene semantics and the
    scene fields flow through to vision_description_json."""
    import json

    from src.extraction.extraction_models import PhotoObservation

    jpeg_path = _make_tiny_jpeg(tmp_path)

    parsed = PhotoObservation(
        damage_type="none",
        affected_component="n/a",
        severity_band="minor",
        visible_text="LOT 1 LOT 2 LOT 3 LOT 4",
        confidence_band="high",
        scene_summary="Site plan showing 4 numbered lots along an access road.",
        key_elements=["4 numbered lots", "access road", "north arrow"],
    )
    mock_provider = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = parsed
    mock_provider.generate_vision = AsyncMock(return_value=mock_response)
    mock_provider.provider_name = "mock"

    with patch(
        "src.shared.llm_provider.get_provider", return_value=mock_provider
    ), patch("src.analytics.metrics.record_vision_call"):
        from src.extraction.image_pipeline import process_image

        result = asyncio.run(process_image(jpeg_path))

    # Prompt instructs scene-semantics capture (scene- and damage-class)
    prompt = mock_provider.generate_vision.call_args.kwargs["prompt"]
    assert "scene_summary" in prompt
    assert "key_elements" in prompt
    assert "damage_type to 'none'" in prompt

    # Scene fields persisted into the vision_description_json payload
    assert result["vision_json"] is not None
    payload = json.loads(result["vision_json"])
    assert payload["scene_summary"].startswith("Site plan")
    assert "north arrow" in payload["key_elements"]
    # Existing damage fields retained (backward compat)
    assert payload["damage_type"] == "none"


def test_process_image_document_class(tmp_path):
    """D501: images with document-like filenames classify as document."""
    jpeg_path = _make_tiny_jpeg(tmp_path, name="scan_receipt.jpg")

    mock_provider = MagicMock()

    with patch("src.shared.llm_provider.get_provider", return_value=mock_provider):
        from src.extraction.image_pipeline import process_image

        result = asyncio.run(process_image(jpeg_path))

    assert result["image_class"] == "document"
    assert result["vision_json"] is None


def test_persist_image_asset_inserts_vertex(tmp_path):
    """D501: persist_image_asset calls ArcadeDB INSERT for new content_sha256."""
    from src.extraction.extraction_models import ImageAsset
    from src.extraction.image_pipeline import persist_image_asset

    asset = ImageAsset(
        grace_id="00000000-0000-4000-8000-000000000001",
        source_path=str(tmp_path / "x.jpg"),
        content_sha256="abc123",
        media_type="image/jpeg",
        image_class="photo",
        ocr_text=None,
        vision_description_json=None,
        sensitivity_tags="",
    )

    mock_client = MagicMock()
    mock_client.execute_cypher = AsyncMock(
        side_effect=[
            {"result": []},
            {"result": [{}]},
        ]
    )
    mock_client.execute_sql = AsyncMock()

    with patch(
        "src.shared.embeddings.embed_texts",
        new_callable=AsyncMock,
    ):
        summary = asyncio.run(
            persist_image_asset(mock_client, asset, document_chunk_mode="never")
        )

    assert summary["skipped"] is False
    assert summary["grace_id"] == asset.grace_id
    assert mock_client.execute_cypher.await_count >= 1


def test_persist_image_asset_idempotent_skip(tmp_path):
    """AC12: duplicate content_sha256 skips INSERT."""
    from src.extraction.extraction_models import ImageAsset
    from src.extraction.image_pipeline import persist_image_asset

    asset = ImageAsset(
        grace_id="00000000-0000-4000-8000-000000000002",
        source_path=str(tmp_path / "y.jpg"),
        content_sha256="dedup-sha",
        media_type="image/jpeg",
        image_class="photo",
    )

    mock_client = MagicMock()
    mock_client.execute_cypher = AsyncMock(
        return_value={"result": [{"grace_id": "existing-id"}]}
    )

    summary = asyncio.run(
        persist_image_asset(mock_client, asset, document_chunk_mode="never")
    )

    assert summary["skipped"] is True
    assert summary["grace_id"] == "existing-id"
    create_calls = [
        c for c in mock_client.execute_cypher.await_args_list
        if c and "CREATE (n:Image_Asset" in str(c)
    ]
    assert create_calls == []


def test_document_chunk_mode_auto_creates_chunk_and_edge(tmp_path):
    """AC13: document + OCR text creates Document_Chunk derives_from Image_Asset."""
    from src.extraction.extraction_models import ImageAsset
    from src.extraction.image_pipeline import persist_image_asset

    long_ocr = "x" * 60
    asset = ImageAsset(
        grace_id="00000000-0000-4000-8000-000000000003",
        source_path=str(tmp_path / "doc.jpg"),
        content_sha256="doc-sha",
        media_type="image/jpeg",
        image_class="document",
        ocr_text=long_ocr,
    )

    mock_client = MagicMock()
    mock_client.execute_cypher = AsyncMock(return_value={"result": []})
    mock_client.execute_sql = AsyncMock()

    mock_chunk = MagicMock()
    mock_chunk.text = long_ocr
    mock_chunk.token_count_estimate = 10

    with patch(
        "src.shared.embeddings.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ), patch(
        "src.extraction.document_chunker.DocumentChunker"
    ) as mock_chunker_cls:
        mock_chunker_cls.return_value.chunk_text.return_value = [mock_chunk]
        summary = asyncio.run(
            persist_image_asset(mock_client, asset, document_chunk_mode="auto")
        )

    assert summary["chunks_created"] == 1
    cypher_calls = " ".join(str(c) for c in mock_client.execute_cypher.await_args_list)
    assert "Image_Asset" in cypher_calls
    assert "Document_Chunk" in cypher_calls
    assert "derives_from" in cypher_calls
