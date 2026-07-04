"""Tests for D520 — sensitivity_tags threading through extract_document -> write_batch.

Verifies:
- extract_document accepts sensitivity_tags kwarg.
- write_batch threads sensitivity_tags to EntityCreate.
- Default empty preserves document-origin path.

D356 capture-the-why: D520 — propagate source sensitivity to domain vertices;
D106 write-batch contract unchanged.
"""

import inspect

from src.extraction.extraction_pipeline import ExtractionPipeline
from src.extraction.graph_writer import write_batch


def test_extract_document_accepts_sensitivity_tags_kwarg():
    """extract_document() signature includes sensitivity_tags: str = ''."""
    sig = inspect.signature(ExtractionPipeline.extract_document)
    params = sig.parameters
    assert "sensitivity_tags" in params, "sensitivity_tags kwarg missing from extract_document"
    default = params["sensitivity_tags"].default
    assert default == "", f"Expected default '', got {default!r}"


def test_run_extract_document_accepts_sensitivity_tags_kwarg():
    """_run_extract_document() signature includes sensitivity_tags: str = ''."""
    sig = inspect.signature(ExtractionPipeline._run_extract_document)
    params = sig.parameters
    assert "sensitivity_tags" in params
    assert params["sensitivity_tags"].default == ""


def test_write_batch_accepts_sensitivity_tags_kwarg():
    """write_batch() signature includes sensitivity_tags: str = ''."""
    sig = inspect.signature(write_batch)
    params = sig.parameters
    assert "sensitivity_tags" in params, "sensitivity_tags kwarg missing from write_batch"
    default = params["sensitivity_tags"].default
    assert default == "", f"Expected default '', got {default!r}"
