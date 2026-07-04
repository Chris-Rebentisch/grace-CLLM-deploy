"""CLI-only image processing pipeline (D246 mirror, Chunk 77b).

Reads images from disk, calls ``generate_vision`` for photo-class images
to produce ``PhotoObservation``, creates ``Image_Asset`` + ``derives_from``
edges in ArcadeDB. Optionally chunks OCR text into ``Document_Chunk``
vertices (controlled by ``image_ingestion.document_chunk_mode``).

Entry: ``python -m src.extraction.image_pipeline --job-id <UUID> --source-path <path>``

D246: CLI-only. ``src/api/extraction_routes.py`` MUST NOT import this module.
D501: Image_Asset graph type, PhotoObservation structured output.
D503: airgap-default via router-level vision provider selection.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import structlog
import yaml

logger = structlog.get_logger()

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SENSITIVITY_RULES_PATH = _REPO_ROOT / "config" / "sensitivity_rules.yaml"
_DISCOVERY_YAML = _REPO_ROOT / "config" / "discovery.yaml"

# Image file extensions we handle (must be a subset of FileType.IMAGE from 77a)
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"})


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _detect_media_type(path: Path) -> str:
    """Detect MIME type from file extension."""
    ext = path.suffix.lower()
    mapping = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".tiff": "image/tiff", ".tif": "image/tiff",
        ".bmp": "image/bmp", ".webp": "image/webp",
    }
    return mapping.get(ext, "application/octet-stream")


def _classify_image(ocr_text: str | None, path: Path) -> str:
    """Classify image: 'document' vs 'photo' vs 'unknown'.

    Heuristic: if OCR text is non-trivial (>50 chars), likely a document scan.
    Otherwise photo (real-world scene/damage).
    """
    if ocr_text and len(ocr_text.strip()) > 50:
        return "document"
    name = path.stem.lower()
    if any(kw in name for kw in ("scan", "doc", "receipt", "invoice", "form", "letter")):
        return "document"
    return "photo"


def _compute_sensitivity_tags(
    path: Path,
    ocr_text: str | None,
    exif_data: dict | None = None,
) -> str:
    """Compute bar-form sensitivity tags for an image."""
    tags: set[str] = set()

    if exif_data:
        gps_keys = {"GPSLatitude", "GPSLongitude", "GPS GPSLatitude"}
        if any(k in exif_data for k in gps_keys):
            tags.add("pii_dense")

    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        img = Image.open(path)
        exif_raw = img.getexif()
        if exif_raw:
            for tag_id, value in exif_raw.items():
                tag_name = TAGS.get(tag_id, "")
                if "GPS" in tag_name:
                    tags.add("pii_dense")
                    break
    except Exception:
        pass

    if ocr_text:
        try:
            with open(_SENSITIVITY_RULES_PATH) as fh:
                rules = yaml.safe_load(fh) or {}
            phrases = rules.get("privilege_phrases", [])
            for phrase in phrases:
                if re.search(re.escape(phrase), ocr_text, re.IGNORECASE):
                    tags.add("privileged")
                    break
        except FileNotFoundError:
            pass

    if not tags:
        return ""
    return "|" + "|".join(sorted(tags)) + "|"


def _read_document_chunk_mode() -> str:
    """Read image_ingestion.document_chunk_mode from discovery.yaml."""
    try:
        with open(_DISCOVERY_YAML) as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("image_ingestion", {}).get("document_chunk_mode", "auto")
    except FileNotFoundError:
        return "auto"


def _should_create_document_chunks(
    mode: str,
    image_class: str,
    ocr_text: str | None,
) -> bool:
    """Whether to create Document_Chunk vertices from OCR text (D501)."""
    if mode == "never":
        return False
    if not ocr_text or not ocr_text.strip():
        return False
    if mode == "always":
        return True
    return image_class == "document" and len(ocr_text.strip()) > 50


def _update_job_status(job_id: str, status: str, error_message: str | None = None) -> None:
    """Update extraction_jobs row (D470 progress reporting)."""
    try:
        from sqlalchemy import text

        # F-12 (validation run): the session factory lives in
        # src.shared.database, not src.api.database (which does not exist).
        # The bad import raised ModuleNotFoundError, silently swallowed by the
        # except below so every job-status update no-oped.
        from src.shared.database import get_session_factory

        Session = get_session_factory()
        with Session() as session:
            if error_message:
                session.execute(
                    text("UPDATE extraction_jobs SET status=:s, error_message=:em, completed_at=now() WHERE job_id=:jid"),
                    {"s": status, "em": error_message, "jid": job_id},
                )
            else:
                params: dict = {"s": status, "jid": job_id}
                sql = "UPDATE extraction_jobs SET status=:s"
                if status == "running":
                    sql += ", started_at=now()"
                elif status in ("completed", "failed"):
                    sql += ", completed_at=now()"
                sql += " WHERE job_id=:jid"
                session.execute(text(sql), params)
            session.commit()
    except Exception as exc:
        logger.warning("job_status_update_failed", job_id=job_id, error=str(exc))


async def persist_image_asset(
    client,
    asset,
    *,
    document_chunk_mode: str,
    ollama_base_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
) -> dict:
    """Persist Image_Asset (+ optional Document_Chunks) to ArcadeDB.

    Idempotent on ``content_sha256``. Returns summary dict with
    ``skipped``, ``grace_id``, and ``chunks_created``.
    """
    from src.extraction.document_chunker import DocumentChunker
    from src.extraction.graph_writer import (
        _compute_chunk_sensitivity_tags,
        _insert_derives_from_chunk_to_image,
        _insert_document_chunk_vertex,
        _insert_image_asset_vertex,
        _lookup_document_chunk,
        _lookup_image_asset_by_sha256,
    )
    from src.shared.embeddings import embed_texts

    if not callable(getattr(client, "execute_cypher", None)):
        raise TypeError("client must provide execute_cypher")

    existing = await _lookup_image_asset_by_sha256(client, asset.content_sha256)
    if existing:
        return {"skipped": True, "grace_id": existing, "chunks_created": 0}

    await _insert_image_asset_vertex(client, asset)

    chunks_created = 0
    if _should_create_document_chunks(
        document_chunk_mode, asset.image_class, asset.ocr_text
    ):
        chunker = DocumentChunker()
        doc_id = asset.grace_id
        for idx, chunk in enumerate(
            chunker.chunk_text(asset.ocr_text or "", document_id=doc_id)
        ):
            if await _lookup_document_chunk(client, doc_id, idx):
                continue
            embedding = (await embed_texts(
                [chunk.text],
                base_url=ollama_base_url,
                model=embedding_model,
            ))[0]
            sensitivity_tags = _compute_chunk_sensitivity_tags(chunk.text)
            chunk_gid = str(uuid4())
            await _insert_document_chunk_vertex(
                client=client,
                grace_id=chunk_gid,
                source_document_id=doc_id,
                chunk_index=idx,
                text=chunk.text,
                chunk_token_count=chunk.token_count_estimate,
                embedding=embedding,
                sensitivity_tags=sensitivity_tags,
            )
            await _insert_derives_from_chunk_to_image(
                client, chunk_gid, asset.grace_id
            )
            chunks_created += 1

    from src.analytics.metrics import record_image_asset_ingested

    record_image_asset_ingested()
    return {
        "skipped": False,
        "grace_id": asset.grace_id,
        "chunks_created": chunks_created,
    }


async def process_image(
    source_path: Path,
    job_id: str | None = None,
    *,
    persist: bool = False,
    client=None,
) -> dict:
    """Process a single image file through the vision pipeline."""
    from src.extraction.extraction_models import ImageAsset, PhotoObservation
    from src.shared.llm_provider import get_provider, read_vision_config_from_yaml

    image_bytes = source_path.read_bytes()
    content_sha256 = _compute_sha256(image_bytes)
    media_type = _detect_media_type(source_path)

    ocr_text: str | None = None
    try:
        from PIL import Image

        img = Image.open(source_path)
        info = img.info or {}
        if "text" in info:
            ocr_text = str(info["text"])
    except Exception:
        pass

    image_class = _classify_image(ocr_text, source_path)
    sensitivity_tags = _compute_sensitivity_tags(source_path, ocr_text)

    vision_json: str | None = None
    if image_class == "photo":
        try:
            # F-11 (validation run): bare get_provider() ignored the
            # llm.vision config block, so vision calls ran against the main
            # (often non-vision) provider/model. Route through the vision
            # config so llm.vision provider/model selection is honored (D500).
            provider = get_provider(config_override=read_vision_config_from_yaml())
            # F-011 / ISS-0018: prompt was damage-only, so scene-class images
            # (site plans, maps, layouts) had no instruction to describe scene
            # semantics. Require scene_summary/key_elements for every image;
            # damage-class images keep the existing damage fields.
            prompt = (
                "Analyze this image. Provide a 1-3 sentence scene_summary of "
                "what the image shows overall, and list the salient objects, "
                "labels, or features as key_elements. If the image shows "
                "physical damage, also describe the damage type, affected "
                "component, and severity; if there is no damage, set "
                "damage_type to 'none' and still fully populate scene_summary "
                "and key_elements. If there is visible text, include it. "
                "Respond as structured JSON."
            )
            t0 = time.monotonic()
            response = await provider.generate_vision(
                prompt=prompt,
                images=[image_bytes],
                response_model=PhotoObservation,
            )
            duration = time.monotonic() - t0
            from src.analytics.metrics import record_vision_call

            record_vision_call(
                # F-54: provider_name is a @property on every LLMProvider (ABC in
                # llm_provider.py); calling it invoked the returned str -> TypeError,
                # which the except below swallowed, silently discarding the parsed
                # vision JSON (vision_json stayed None).
                provider=provider.provider_name,
                duration_seconds=duration,
            )
            if response.parsed:
                vision_json = response.parsed.model_dump_json()
        except Exception as exc:
            logger.warning("vision_call_failed", source_path=str(source_path), error=str(exc))

    asset = ImageAsset(
        grace_id=str(uuid4()),
        source_path=str(source_path),
        content_sha256=content_sha256,
        media_type=media_type,
        image_class=image_class,
        ocr_text=ocr_text,
        vision_description_json=vision_json,
        sensitivity_tags=sensitivity_tags,
        extracted_at=datetime.now(timezone.utc),
    )

    result: dict = {
        "asset": asset,
        "content_sha256": content_sha256,
        "image_class": image_class,
        "sensitivity_tags": sensitivity_tags,
        "vision_json": vision_json,
        "persisted": False,
        "idempotent_skip": False,
    }

    if persist and client is not None:
        persist_summary = await persist_image_asset(
            client,
            asset,
            document_chunk_mode=_read_document_chunk_mode(),
        )
        result["persisted"] = not persist_summary["skipped"]
        result["idempotent_skip"] = persist_summary["skipped"]
        result["chunks_created"] = persist_summary.get("chunks_created", 0)
        result["grace_id"] = persist_summary["grace_id"]

    return result


def _build_argparser() -> argparse.ArgumentParser:
    """Build CLI argument parser (D476 contract-test compatible)."""
    parser = argparse.ArgumentParser(
        description="Image processing pipeline (D246 mirror, Chunk 77b)",
    )
    parser.add_argument("--job-id", required=True, help="Extraction job UUID")
    parser.add_argument("--source-path", required=True, help="Path to image file")
    return parser


def main() -> None:
    """CLI entry point."""
    # F-15: mirror this subprocess's OTel counters into the prometheus
    # multiproc dir so uvicorn's /metrics can expose them (no-op when
    # PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = _build_argparser()
    args = parser.parse_args()

    job_id = args.job_id
    source_path = Path(args.source_path)

    if not source_path.exists():
        logger.error("source_not_found", source_path=str(source_path))
        _update_job_status(job_id, "failed", f"Source path not found: {source_path}")
        sys.exit(1)

    if source_path.suffix.lower() not in _IMAGE_EXTENSIONS:
        logger.error("unsupported_image_type", suffix=source_path.suffix)
        _update_job_status(job_id, "failed", f"Unsupported image type: {source_path.suffix}")
        sys.exit(1)

    _update_job_status(job_id, "running")

    try:
        from src.graph.arcade_client import get_arcade_client

        client = get_arcade_client()
        result = asyncio.run(
            process_image(source_path, job_id, persist=True, client=client)
        )
        logger.info(
            "image_processed",
            grace_id=result.get("grace_id", result["asset"].grace_id),
            image_class=result["image_class"],
            content_sha256=result["content_sha256"],
            has_vision=result["vision_json"] is not None,
            persisted=result.get("persisted"),
            idempotent_skip=result.get("idempotent_skip"),
            chunks_created=result.get("chunks_created", 0),
        )
        _update_job_status(job_id, "completed")
    except Exception as exc:
        logger.error("image_pipeline_failed", error=str(exc))
        _update_job_status(job_id, "failed", str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
