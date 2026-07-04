"""Shared fixtures for performance regression tests (Chunk 61, CP6).

Provides timer utilities and skip-gracefully decorator for Ollama-required tests.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

import pytest


@contextmanager
def perf_timer() -> Generator[dict, None, None]:
    """Context manager returning elapsed wall-clock seconds in result['elapsed']."""
    result: dict = {}
    start = time.perf_counter()
    try:
        yield result
    finally:
        result["elapsed"] = time.perf_counter() - start


def skip_if_no_ollama() -> pytest.MarkDecorator:
    """Return a pytest skip marker if Ollama is not reachable on localhost:11434."""
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/version", timeout=3.0)
        available = resp.status_code == 200
    except Exception:
        available = False

    return pytest.mark.skipif(
        not available,
        reason="Ollama not reachable on localhost:11434 — skip-gracefully (warn-only)",
    )
