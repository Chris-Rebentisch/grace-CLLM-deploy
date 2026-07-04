"""Shared fixtures for decomposition tests (Chunk 40).

Provides synthetic archives, mocked ``LLMProvider``, mocked
``embed_texts()``, config loader fixtures, and Postgres session
fixtures. CI does not depend on Ollama or external network.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.decomposition.config import DecompositionConfig, load_config


# ---------- Config fixtures ----------


@pytest.fixture
def default_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DecompositionConfig:
    """Load the on-disk YAML config with no env overrides applied."""
    # Ensure no DECOMPOSITION_* env vars leak in from the operator shell.
    for key in list(os.environ):
        if key.startswith("DECOMPOSITION_"):
            monkeypatch.delenv(key, raising=False)
    return load_config()


# ---------- Synthetic archive fixtures ----------


@pytest.fixture
def synthetic_archive(tmp_path: Path) -> Path:
    """Create a tiny synthetic archive with mixed suffixes."""
    root = tmp_path / "archive"
    (root / "ops").mkdir(parents=True)
    (root / "finance").mkdir()
    (root / "ops" / "memo.txt").write_text(
        "Ops memo body. Acme Inc reports quarterly. " * 5
    )
    (root / "ops" / "plan.md").write_text(
        "# Ops Plan\n\nDetails about Beacon LLC contracts.\n" * 4
    )
    (root / "finance" / "budget.txt").write_text(
        "Budget summary for Acme Inc and partners. " * 8
    )
    return root


# ---------- Mocked LLM provider ----------


class MockLLMProvider:
    """Minimal ``LLMProvider`` test double.

    Supports ``generate(prompt, **kwargs)`` returning canned strings or
    JSON. The ``responses`` queue is consumed FIFO; ``calls`` records
    each invocation for assertion.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses: list[str] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        if self.responses:
            return self.responses.pop(0)
        return ""


@pytest.fixture
def mock_llm_provider() -> MockLLMProvider:
    """Default empty mock provider; tests can extend ``responses``."""
    return MockLLMProvider()


# ---------- Mocked embed_texts ----------


@pytest.fixture
def mock_embed_texts(monkeypatch: pytest.MonkeyPatch):
    """Patch ``src.shared.embeddings.embed_texts`` to return deterministic vectors.

    Tests can pass ``vectors`` to control the return; otherwise zeros.
    """

    async def _stub(texts: list[str], *args: Any, **kwargs: Any) -> list[list[float]]:
        # 16-dim deterministic placeholder vectors.
        return [[float((i + j) % 7) for j in range(16)] for i in range(len(texts))]

    monkeypatch.setattr("src.shared.embeddings.embed_texts", _stub)
    return _stub


# ---------- Postgres session fixture ----------


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2:///grace"
    )


@pytest.fixture
def db_session() -> Session:
    """Yield a Postgres session and roll back any changes at end."""
    engine = create_engine(_database_url(), future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
