"""Route-level tests for extraction job lifecycle routes (D470; D473/D474/D475).

Tests the POST spawn, GET by id, GET list, concurrent 409 protection,
path allowlist, cost-budget gate, and D473/D474/D475 regression tests.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.api.extraction_routes import _build_extraction_argv
from src.shared.database import get_session_factory


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def allowed_source_file(tmp_path):
    """Create a test file under a resolved root + stub the active-ontology resolver.

    - tmp_path is resolved so the allowlist root matches the route's
      ``Path.resolve(strict=True)`` output on macOS, where ``/var`` is a symlink
      to ``/private/var`` (an unresolved root makes ``is_relative_to`` fail → 422).
    - The D473 active-ontology lookup is stubbed: the isolated test DB has no
      ratified ontology version, which would otherwise 422. The subprocess is
      mocked, so the stub schema file is never actually read.
    """
    root = tmp_path.resolve()
    test_file = root / "test_doc.txt"
    test_file.write_text("This is a test document for extraction.")
    stub_schema = root / "ontology.json"
    stub_schema.write_text("{}")
    with patch(
        "src.api.extraction_routes._resolve_active_ontology_json",
        return_value=stub_schema,
    ):
        yield test_file, root


def test_post_extraction_job_accept(client, allowed_source_file, db):
    """POST /api/extraction/jobs returns 202 with job_id, status, pid."""
    test_file, tmp_path = allowed_source_file
    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.wait = MagicMock(return_value=0)

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]), \
         patch("src.api.extraction_routes.subprocess.Popen", return_value=mock_proc):
        resp = client.post("/api/extraction/jobs", json={
            "job_kind": "document",
            "source_path": str(test_file),
        })

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "pending"
    assert data["pid"] == 99999

    # Cleanup
    db.execute(text("DELETE FROM extraction_jobs WHERE job_id = :jid"), {"jid": data["job_id"]})
    db.commit()


def test_duplicate_post_returns_409_when_non_terminal_job_exists(client, allowed_source_file, db):
    """Second POST for same source_path while a pending row exists returns 409 (D470 / spec §9.2)."""
    test_file, tmp_path = allowed_source_file
    mock_proc = MagicMock()
    mock_proc.pid = 99997
    mock_proc.wait = MagicMock(return_value=0)

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]), \
         patch("src.api.extraction_routes.subprocess.Popen", return_value=mock_proc):
        first = client.post("/api/extraction/jobs", json={
            "job_kind": "document",
            "source_path": str(test_file),
        })
        assert first.status_code == 202
        dup = client.post("/api/extraction/jobs", json={
            "job_kind": "document",
            "source_path": str(test_file),
        })

    assert dup.status_code == 409
    detail = dup.json()["detail"]
    assert "Non-terminal job already exists" in detail or "already exists for" in detail

    job_id = first.json()["job_id"]
    db.execute(text("DELETE FROM extraction_jobs WHERE job_id = :jid"), {"jid": job_id})
    db.commit()


def test_post_extraction_job_traversal_rejected(client):
    """POST with traversal path returns 422."""
    resp = client.post("/api/extraction/jobs", json={
        "job_kind": "document",
        "source_path": "/etc/passwd",
    })
    assert resp.status_code == 422


def test_post_extraction_job_nonexistent_path(client):
    """POST with non-existent path returns 422."""
    resp = client.post("/api/extraction/jobs", json={
        "job_kind": "document",
        "source_path": "/nonexistent/path/file.txt",
    })
    assert resp.status_code == 422


def test_post_extraction_job_cost_budget_required(client, allowed_source_file):
    """POST batch job with cloud provider requires cost_budget_usd."""
    test_file, tmp_path = allowed_source_file

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]):
        resp = client.post("/api/extraction/jobs", json={
            "job_kind": "batch",
            "source_path": str(tmp_path),
            "provider": "anthropic",
        })

    assert resp.status_code == 422
    data = resp.json()
    assert "estimated_cost_usd" in str(data) or "cost_budget_usd" in str(data)


def test_post_extraction_job_cost_not_required_local(client, allowed_source_file, db):
    """POST batch job with local provider does NOT require cost_budget_usd."""
    test_file, tmp_path = allowed_source_file
    mock_proc = MagicMock()
    mock_proc.pid = 99998
    mock_proc.wait = MagicMock(return_value=0)

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]), \
         patch("src.api.extraction_routes.subprocess.Popen", return_value=mock_proc):
        resp = client.post("/api/extraction/jobs", json={
            "job_kind": "batch",
            "source_path": str(tmp_path),
            "provider": "ollama",
        })

    assert resp.status_code == 202

    # Cleanup
    data = resp.json()
    db.execute(text("DELETE FROM extraction_jobs WHERE job_id = :jid"), {"jid": data["job_id"]})
    db.commit()


def test_get_extraction_job_200(client, allowed_source_file, db):
    """GET /api/extraction/jobs/{job_id} returns 200 with job state."""
    # Insert a job directly
    job_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO extraction_jobs (job_id, job_kind, source_path, status, created_at) "
            "VALUES (:jid, 'document', '/tmp/test.txt', 'pending', now())"
        ),
        {"jid": job_id},
    )
    db.commit()

    resp = client.get(f"/api/extraction/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["status"] == "pending"
    assert "stalled" in data

    # Cleanup
    db.execute(text("DELETE FROM extraction_jobs WHERE job_id = :jid"), {"jid": job_id})
    db.commit()


def test_get_extraction_job_404(client):
    """GET /api/extraction/jobs/{job_id} returns 404 for missing job."""
    resp = client.get(f"/api/extraction/jobs/{uuid4()}")
    assert resp.status_code == 404


def test_get_extraction_jobs_list(client, db):
    """GET /api/extraction/jobs returns paginated list."""
    # Insert a job
    job_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO extraction_jobs (job_id, job_kind, source_path, status, created_at) "
            "VALUES (:jid, 'document', '/tmp/list_test.txt', 'completed', now())"
        ),
        {"jid": job_id},
    )
    db.commit()

    resp = client.get("/api/extraction/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data

    # Cleanup
    db.execute(text("DELETE FROM extraction_jobs WHERE job_id = :jid"), {"jid": job_id})
    db.commit()


# ── D473 regression: document-job --schema passes JSON, not YAML ──────────


def test_document_job_argv_uses_json_schema(tmp_path):
    """D473: _build_extraction_argv for document jobs passes a .json schema path."""
    schema_path = tmp_path / "abc123.json"
    schema_path.write_text("{}")
    source_file = tmp_path / "doc.txt"
    source_file.write_text("hello")

    argv = _build_extraction_argv(
        job_kind="document",
        job_id=uuid4(),
        schema_path=schema_path,
        source_path=source_file,
        provider="ollama",
        model="qwen2.5:7b",
    )
    # --schema must point to a .json file, never discovery.yaml
    schema_idx = argv.index("--schema")
    schema_val = argv[schema_idx + 1]
    assert schema_val.endswith(".json"), f"Expected .json schema path, got {schema_val}"
    assert "discovery.yaml" not in schema_val


# ── F-0008/F-0009 / ISS-0041: document-job argv targets the named file ────


def test_document_job_argv_uses_doc_file_and_persist(tmp_path):
    """F-0008 / ISS-0041: document argv carries --doc-file <requested file>,
    not --doc-dir <parent> --sample-count 1 (which discarded the filename and
    extracted the alphabetically-first .txt/.md in the directory).
    F-0009 / ISS-0041: --persist rides the argv so claims/events persist."""
    schema_path = tmp_path / "abc123.json"
    schema_path.write_text("{}")
    source_file = tmp_path / "zzz_last_alphabetically.txt"
    source_file.write_text("hello")
    # A decoy that the OLD argv would have extracted instead.
    (tmp_path / "aaa_first_alphabetically.txt").write_text("decoy")

    argv = _build_extraction_argv(
        job_kind="document",
        job_id=uuid4(),
        schema_path=schema_path,
        source_path=source_file,
    )
    assert "--doc-file" in argv
    assert argv[argv.index("--doc-file") + 1] == str(source_file)
    assert "--persist" in argv
    assert "--doc-dir" not in argv, "document argv must not fall back to --doc-dir for files"
    assert "--sample-count" not in argv


def test_post_document_job_unsupported_suffix_422(client, allowed_source_file):
    """F-0008 / ISS-0041 (binary-format follow-up): binary format WITHOUT a
    processed_documents row 422s fast at the API with batch-runner guidance
    (instead of spawning a subprocess doomed to exit 1). Pre-check mocked —
    pure unit, no processed_documents query."""
    _, tmp_path = allowed_source_file
    pdf_file = tmp_path / "claim.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]), \
         patch(
             "src.api.extraction_routes._processed_document_text_available",
             return_value=False,
         ):
        resp = client.post("/api/extraction/jobs", json={
            "job_kind": "document",
            "source_path": str(pdf_file),
        })

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "Unsupported document suffix" in detail
    assert ".txt" in detail and ".md" in detail
    # The remediation is actionable: run the Docling batch to populate
    # processed_documents.extracted_text for this path.
    assert "src.discovery.batch_runner" in detail


def test_post_document_job_binary_with_processed_row_202(
    client, allowed_source_file, db
):
    """F-0008 / ISS-0041 (binary-format follow-up): binary format WITH a
    processed_documents row is accepted (202) and the spawned argv carries
    --doc-file <resolved pdf> + --from-processed-doc + --persist. Pre-check
    and Popen mocked — no subprocess, no processed_documents query."""
    _, tmp_path = allowed_source_file
    pdf_file = tmp_path / "claim.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")

    mock_proc = MagicMock()
    mock_proc.pid = 99996
    mock_proc.wait = MagicMock(return_value=0)

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]), \
         patch(
             "src.api.extraction_routes._processed_document_text_available",
             return_value=True,
         ) as precheck, \
         patch(
             "src.api.extraction_routes.subprocess.Popen", return_value=mock_proc
         ) as popen:
        resp = client.post("/api/extraction/jobs", json={
            "job_kind": "document",
            "source_path": str(pdf_file),
        })

    assert resp.status_code == 202
    precheck.assert_called_once()
    argv = popen.call_args[0][0]
    assert "--doc-file" in argv
    assert argv[argv.index("--doc-file") + 1] == str(pdf_file.resolve())
    assert "--from-processed-doc" in argv
    assert "--persist" in argv

    # Cleanup
    data = resp.json()
    db.execute(text("DELETE FROM extraction_jobs WHERE job_id = :jid"), {"jid": data["job_id"]})
    db.commit()


def test_post_document_job_txt_skips_processed_doc_precheck(
    client, allowed_source_file, db
):
    """ISS-0041 binary-format follow-up: .txt/.md route behavior is
    byte-identical — no processed_documents pre-check, no
    --from-processed-doc in the spawned argv."""
    test_file, tmp_path = allowed_source_file
    mock_proc = MagicMock()
    mock_proc.pid = 99995
    mock_proc.wait = MagicMock(return_value=0)

    with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]), \
         patch(
             "src.api.extraction_routes._processed_document_text_available",
         ) as precheck, \
         patch("src.api.extraction_routes.subprocess.Popen", return_value=mock_proc) as popen:
        resp = client.post("/api/extraction/jobs", json={
            "job_kind": "document",
            "source_path": str(test_file),
        })

    assert resp.status_code == 202
    precheck.assert_not_called()
    argv = popen.call_args[0][0]
    assert "--from-processed-doc" not in argv

    # Cleanup
    data = resp.json()
    db.execute(text("DELETE FROM extraction_jobs WHERE job_id = :jid"), {"jid": data["job_id"]})
    db.commit()


# ── D474 regression: batch-job argv omits --provider/--model ──────────────


def test_batch_job_argv_omits_provider_and_model(tmp_path):
    """D474: _build_extraction_argv for batch jobs does NOT include --provider or --model."""
    schema_path = tmp_path / "abc123.json"
    schema_path.write_text("{}")

    argv = _build_extraction_argv(
        job_kind="batch",
        job_id=uuid4(),
        schema_path=schema_path,
        source_path=tmp_path,
        provider="ollama",  # should be ignored for batch
        model="qwen2.5:7b",  # should be ignored for batch
    )
    assert "--provider" not in argv, "batch argv must not contain --provider"
    assert "--model" not in argv, "batch argv must not contain --model"


# ── D475 regression: error capture ────────────────────────────────────────


def test_error_message_capacity_4096():
    """D475: error_message capture supports >= 4096 chars (traceback tail)."""
    long_tb = "X" * 5000
    tail = long_tb[-4096:]
    assert len(tail) == 4096


def test_logfile_path_construction():
    """D475: per-job logfile path uses job_id in the filename."""
    from src.api.extraction_routes import _LOG_DIR

    job_id = uuid4()
    expected = _LOG_DIR / f"extraction-job-{job_id}.log"
    assert str(job_id) in str(expected)
    assert expected.suffix == ".log"


def test_no_subprocess_devnull_in_stderr_path():
    """D475: the create_extraction_job spawn uses logfile for stderr, not DEVNULL."""
    import inspect

    from src.api.extraction_routes import create_extraction_job

    source = inspect.getsource(create_extraction_job)
    # The stderr line should use logfile, not subprocess.DEVNULL
    assert "logfile" in source, "Expected 'logfile' in create_extraction_job source"
    # Filter out comments — only check executable lines
    code_lines = [
        line.strip() for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert "stderr=subprocess.DEVNULL" not in code_only, (
        "stderr=subprocess.DEVNULL still present in code — D475 requires logfile capture"
    )
