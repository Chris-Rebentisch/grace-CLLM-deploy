"""Integration tests for Discovery batch runner."""

import json

import pytest
from sqlalchemy import text

from src.discovery.batch_runner import run_batch
from src.discovery.database import list_documents
from src.discovery.models import ProcessingStatus
from src.shared.database import get_db, get_engine


@pytest.fixture(autouse=True)
def clean_table():
    """Clean the processed_documents table before and after each test."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM processed_documents"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM processed_documents"))
        conn.commit()


@pytest.fixture()
def test_files(tmp_path):
    """Create a temp directory with 3 test text files."""
    for i in range(3):
        f = tmp_path / f"doc{i}.txt"
        f.write_text(f"This is test document number {i} with some content.")
    return tmp_path


def test_batch_from_directory(test_files):
    """Create a temp dir with 3 test files, run batch, verify 3 documents in database."""
    result = run_batch(source_dir=test_files)
    db_gen = get_db()
    db = next(db_gen)
    try:
        docs = list_documents(db)
        assert len(docs) == 3
        for doc in docs:
            assert doc.status == ProcessingStatus.COMPLETE
            assert doc.word_count > 0
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


def test_batch_skips_duplicates(test_files):
    """Run batch twice on same files, verify no duplicate inserts."""
    run_batch(source_dir=test_files)
    run_batch(source_dir=test_files)

    db_gen = get_db()
    db = next(db_gen)
    try:
        docs = list_documents(db)
        assert len(docs) == 3  # not 6
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


def test_batch_dry_run(test_files):
    """Run with dry_run=True, verify no documents inserted."""
    result = run_batch(source_dir=test_files, dry_run=True)
    assert result["dry_run"] is True
    assert result["total_files"] == 3

    db_gen = get_db()
    db = next(db_gen)
    try:
        docs = list_documents(db)
        assert len(docs) == 0
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


def test_batch_from_manifest(test_files):
    """Create a manifest JSON pointing to test files, run batch with manifest."""
    # Create manifest
    files = [str(f.resolve()) for f in test_files.glob("*.txt")]
    manifest = {
        "created_at": "2026-03-20T15:00:00Z",
        "source_description": "Test manifest",
        "files": files,
    }
    manifest_path = test_files / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    run_batch(manifest_path=manifest_path)

    db_gen = get_db()
    db = next(db_gen)
    try:
        docs = list_documents(db)
        assert len(docs) == 3
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass
