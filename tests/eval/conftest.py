"""Shared fixtures for eval tests (Chunk 34)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def make_dataset_dir(tmp_path: Path):
    """Returns a callable that materializes a list of case dicts as a single
    JSON file in a tmp directory and returns the directory.
    """

    def _make(cases: list[dict], filename: str = "module.json") -> Path:
        target = tmp_path / filename
        target.write_text(json.dumps(cases))
        return tmp_path

    return _make


def _case(
    *,
    case_id: str,
    module: str,
    complexity: str = "simple",
    retrieval_path: str = "graph",
    query_text: str | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "query_text": query_text or f"Query for {case_id}",
        "expected_output": f"Expected output for {case_id}.",
        "expected_retrieval_path": retrieval_path,
        "query_complexity": complexity,
        "ontology_module": module,
        "source_documents": [],
        "notes": None,
    }


@pytest.fixture
def case_factory():
    """Factory for building synthetic GoldenCase-shaped dicts."""
    return _case
