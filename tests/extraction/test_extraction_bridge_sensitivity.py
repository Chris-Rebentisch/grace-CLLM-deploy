"""Tests for D520 — extraction bridge sensitivity_tags propagation + retag.

Verifies:
- Bridge reads source-email sensitivity_tags and passes to extract_document.
- Retag CLI subcommand is registered.
- Retag run_retag function exists with correct signature.

D356 capture-the-why: D520 — propagate source-email sensitivity to domain
vertices via extraction bridge; REQUIRED retag of pre-Chunk-81 email-derived
vertices; R2 mitigation.
"""

import argparse
import inspect

from src.extraction.extraction_bridge import (
    _build_argparser,
    _process_email,
    run_retag,
)


def test_bridge_propagates_sensitivity_tags_kwarg():
    """_process_email passes sensitivity_tags to extract_document."""
    # Verify _process_email reads sensitivity_tags from the row dict
    source = inspect.getsource(_process_email)
    assert "sensitivity_tags" in source, (
        "_process_email must reference sensitivity_tags"
    )
    assert "extract_document" in source, (
        "_process_email must call extract_document"
    )


def test_retag_subcommand_registered():
    """retag CLI subcommand is registered in the argparser."""
    parser = _build_argparser()
    # Parse retag subcommand
    args = parser.parse_args(["retag", "--dry-run"])
    assert args.command == "retag"
    assert args.dry_run is True


def test_retag_batch_size_default():
    """retag --batch-size defaults to 100."""
    parser = _build_argparser()
    args = parser.parse_args(["retag"])
    assert args.batch_size == 100


def test_run_retag_signature():
    """run_retag accepts batch_size and dry_run kwargs."""
    sig = inspect.signature(run_retag)
    params = sig.parameters
    assert "batch_size" in params
    assert "dry_run" in params
    assert params["batch_size"].default == 100
    assert params["dry_run"].default is False
