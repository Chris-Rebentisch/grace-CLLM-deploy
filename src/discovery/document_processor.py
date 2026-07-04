"""Document processor: converts a single file via Docling into a ProcessedDocument."""

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from docling.datamodel.base_models import InputFormat  # noqa: E402
from docling.datamodel.pipeline_options import (  # noqa: E402
    EasyOcrOptions,
    OcrMacOptions,
    PdfPipelineOptions,
    RapidOcrOptions,
    TesseractOcrOptions,
    VlmPipelineOptions,
)
from docling.datamodel import vlm_model_specs  # noqa: E402
from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption  # noqa: E402
from docling.pipeline.vlm_pipeline import VlmPipeline  # noqa: E402

import structlog  # noqa: E402

from src.discovery.models import (  # noqa: E402
    FileType,
    ProcessedDocument,
    ProcessingStatus,
    get_valid_domains,
    load_discovery_config,
)

logger = structlog.get_logger()

# Valid pipeline_mode values for document_processing config (D443).
_VALID_PIPELINE_MODES = {"standard", "vlm"}

# Valid vlm.model_spec values — subset of vlm_model_specs attributes surfaced to config (D443).
_VALID_MODEL_SPECS = {"GRANITEDOCLING_MLX", "GRANITEDOCLING_TRANSFORMERS", "GRANITEDOCLING_OLLAMA"}

# Valid ocr.backend values for image-OCR config (D499).
_VALID_OCR_BACKENDS = {"auto", "ocrmac", "rapidocr", "tesseract", "easyocr"}


def _build_converter(config: dict) -> DocumentConverter:
    """Construct a DocumentConverter based on document_processing config.

    When pipeline_mode is 'vlm', rebinds InputFormat.PDF to VlmPipeline with the
    configured model_spec. DOCX/XLSX/PPTX remain on standard backends regardless of mode.

    When pipeline_mode is 'standard' (or absent), returns a bare DocumentConverter()
    identical to the pre-Chunk-62 default.

    Invariant: D443 — config-selectable VLM pipeline adoption.
    """
    doc_processing = config.get("document_processing", {})
    pipeline_mode = doc_processing.get("pipeline_mode", "standard")

    if pipeline_mode not in _VALID_PIPELINE_MODES:
        raise ValueError(
            f"Invalid pipeline_mode '{pipeline_mode}'. Must be one of: {sorted(_VALID_PIPELINE_MODES)}"
        )

    if pipeline_mode == "standard":
        converter = DocumentConverter()
    else:
        # VLM mode — PDF-scoped only (research Subject 3).
        vlm_config = doc_processing.get("vlm", {})
        model_spec_name = vlm_config.get("model_spec", "GRANITEDOCLING_TRANSFORMERS")

        if model_spec_name not in _VALID_MODEL_SPECS:
            raise ValueError(
                f"Invalid vlm.model_spec '{model_spec_name}'. Must be one of: {sorted(_VALID_MODEL_SPECS)}"
            )

        if not hasattr(vlm_model_specs, model_spec_name):
            raise ValueError(
                f"vlm_model_specs has no attribute '{model_spec_name}'. "
                f"Check Docling version compatibility."
            )

        vlm_options = getattr(vlm_model_specs, model_spec_name)
        vlm_pipeline_options = VlmPipelineOptions(vlm_options=vlm_options)
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_cls=VlmPipeline,
                    pipeline_options=vlm_pipeline_options,
                ),
            }
        )

    # D499 image-OCR branch. Extends D443 config-surface with InputFormat.IMAGE
    # registration. Platform-aware auto-select: OcrMac on macOS (Apple Vision
    # framework, D138 airgap-compliant), RapidOCR elsewhere (ONNX Runtime CPU,
    # D138 airgap via pre-staged models). Dual-platform target per project
    # requirement.
    # Composition: PdfPipelineOptions wraps OCR option class per Docling 2.80.0
    # API (spec §18.2 — ImageFormatOption requires PdfPipelineOptions, not bare
    # OcrOptions).
    # Invariant: D443 — VLM-PDF path preserved. Carve-out: D499 image-OCR
    # registration is additive and orthogonal to PDF pipeline mode.
    ocr_config = doc_processing.get("ocr", {})
    backend = ocr_config.get("backend", "auto")
    force_full_page = ocr_config.get("force_full_page_ocr", True)

    if backend not in _VALID_OCR_BACKENDS:
        raise ValueError(
            f"Invalid ocr.backend '{backend}'. Must be one of: {sorted(_VALID_OCR_BACKENDS)}"
        )

    # Resolve 'auto': macOS → OcrMac, elsewhere → RapidOCR.
    if backend == "auto":
        backend = "ocrmac" if sys.platform == "darwin" else "rapidocr"

    if backend == "ocrmac" and sys.platform != "darwin":
        raise ValueError("OcrMac requires macOS (sys.platform == 'darwin')")

    _OCR_OPTIONS_MAP = {
        "ocrmac": OcrMacOptions,
        "rapidocr": RapidOcrOptions,
        "tesseract": TesseractOcrOptions,
        "easyocr": EasyOcrOptions,
    }
    ocr_options = _OCR_OPTIONS_MAP[backend](force_full_page_ocr=force_full_page)
    ocr_pipeline_opts = PdfPipelineOptions(do_ocr=True, ocr_options=ocr_options)

    # Runtime mutation: format_to_options is the mutable dict on a constructed
    # DocumentConverter (format_options is constructor-keyword only).
    converter.format_to_options[InputFormat.IMAGE] = ImageFormatOption(
        pipeline_options=ocr_pipeline_opts,
    )

    return converter

EXTENSION_TO_FILETYPE: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".docx": FileType.DOCX,
    ".xlsx": FileType.XLSX,
    ".pptx": FileType.PPTX,
    ".html": FileType.HTML,
    ".htm": FileType.HTML,
    ".txt": FileType.TXT,
    ".md": FileType.TXT,
    ".csv": FileType.CSV,
    ".jpg": FileType.IMAGE,
    ".jpeg": FileType.IMAGE,
    ".png": FileType.IMAGE,
    ".tiff": FileType.IMAGE,
    ".tif": FileType.IMAGE,
    ".bmp": FileType.IMAGE,
    ".webp": FileType.IMAGE,
}


def _get_supported_extensions() -> list[str]:
    """Return supported file extensions from discovery.yaml."""
    config = load_discovery_config()
    return config["supported_extensions"]


def _get_file_type(ext: str) -> FileType:
    """Map a file extension to a FileType enum value."""
    return EXTENSION_TO_FILETYPE.get(ext.lower(), FileType.OTHER)


def process_document(
    file_path: Path,
    converter: DocumentConverter | None = None,
) -> ProcessedDocument:
    """Process a single document file through Docling and return a ProcessedDocument.

    Steps:
    1. Validate file exists and extension is supported
    2. Read file metadata (size, created, modified timestamps)
    3. Determine FileType from extension
    4. Convert via Docling DocumentConverter
    5. Export to Markdown (extracted_text) and lossless JSON (docling_document_json)
    6. Count words in the extracted text
    7. Return a ProcessedDocument with status=COMPLETE

    If conversion fails, return ProcessedDocument with status=FAILED and error_message.
    If extension is not supported, return ProcessedDocument with status=SKIPPED.
    """
    file_path = Path(file_path).resolve()
    file_name = file_path.name
    ext = file_path.suffix.lower()

    # Check file exists
    if not file_path.exists():
        return ProcessedDocument(
            file_path=str(file_path),
            file_name=file_name,
            file_type=_get_file_type(ext),
            file_size_bytes=0,
            status=ProcessingStatus.FAILED,
            error_message=f"File not found: {file_path}",
        )

    # Check extension is supported
    supported = _get_supported_extensions()
    if ext not in supported:
        stat = file_path.stat()
        return ProcessedDocument(
            file_path=str(file_path),
            file_name=file_name,
            file_type=_get_file_type(ext),
            file_size_bytes=stat.st_size,
            status=ProcessingStatus.SKIPPED,
            error_message=f"Unsupported extension: {ext}",
        )

    # Read file metadata
    stat = file_path.stat()
    file_size = stat.st_size
    created_at = datetime.fromtimestamp(stat.st_birthtime, tz=UTC) if hasattr(stat, "st_birthtime") else None
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    file_type = _get_file_type(ext)

    # Plain text files (.txt) are read directly — Docling doesn't support them.
    # .md and .csv go through Docling which handles them natively.
    if ext == ".txt":
        try:
            start = time.monotonic()
            extracted_text = file_path.read_text(encoding="utf-8", errors="replace")
            word_count = len(extracted_text.split())
            elapsed = time.monotonic() - start

            logger.info(
                "document_processed",
                file_name=file_name,
                status="COMPLETE",
                word_count=word_count,
                elapsed_seconds=round(elapsed, 1),
            )

            return ProcessedDocument(
                file_path=str(file_path),
                file_name=file_name,
                file_type=file_type,
                file_size_bytes=file_size,
                created_at=created_at,
                modified_at=modified_at,
                status=ProcessingStatus.COMPLETE,
                extracted_text=extracted_text,
                docling_document_json=None,
                word_count=word_count,
            )
        except Exception as e:
            logger.error("document_processing_failed", file_name=file_name, error=str(e))
            return ProcessedDocument(
                file_path=str(file_path),
                file_name=file_name,
                file_type=file_type,
                file_size_bytes=file_size,
                created_at=created_at,
                modified_at=modified_at,
                status=ProcessingStatus.FAILED,
                error_message=str(e),
            )

    # Convert via Docling
    try:
        if converter is None:
            converter = _build_converter(load_discovery_config())

        start = time.monotonic()
        result = converter.convert(str(file_path))
        doc = result.document

        extracted_text = doc.export_to_markdown()
        docling_json = json.loads(doc.model_dump_json())
        word_count = len(extracted_text.split())
        elapsed = time.monotonic() - start

        logger.info(
            "document_processed",
            file_name=file_name,
            status="COMPLETE",
            word_count=word_count,
            elapsed_seconds=round(elapsed, 1),
        )

        return ProcessedDocument(
            file_path=str(file_path),
            file_name=file_name,
            file_type=file_type,
            file_size_bytes=file_size,
            created_at=created_at,
            modified_at=modified_at,
            status=ProcessingStatus.COMPLETE,
            extracted_text=extracted_text,
            docling_document_json=docling_json,
            word_count=word_count,
        )

    except Exception as e:
        logger.error(
            "document_processing_failed",
            file_name=file_name,
            error=str(e),
        )
        return ProcessedDocument(
            file_path=str(file_path),
            file_name=file_name,
            file_type=file_type,
            file_size_bytes=file_size,
            created_at=created_at,
            modified_at=modified_at,
            status=ProcessingStatus.FAILED,
            error_message=str(e),
        )
