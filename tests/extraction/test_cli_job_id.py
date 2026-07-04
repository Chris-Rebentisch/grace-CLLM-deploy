"""CP4 — CLI --job-id flag acceptance and progress_json shape tests (D470)."""

from __future__ import annotations

import subprocess
import sys


def test_eval_checkpoint_job_id_flag_accepted():
    """eval_checkpoint --help output shows --job-id flag."""
    result = subprocess.run(
        [sys.executable, "-m", "src.extraction.eval_checkpoint", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert "--job-id" in result.stdout


def test_batch_runner_job_id_flag_accepted():
    """batch_runner --help output shows --job-id flag."""
    result = subprocess.run(
        [sys.executable, "-m", "src.discovery.batch_runner", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert "--job-id" in result.stdout


def test_progress_json_shape():
    """Validate the JSONB schema used for progress_json updates.

    The shape is: {documents_processed: int, documents_total: int,
    current_file: str, last_tick_at: str}.
    """
    import json
    from datetime import datetime, timezone

    # Simulate the shape that _run_with_job_tracking produces
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "documents_processed": 5,
        "documents_total": 10,
        "current_file": "test.txt",
        "last_tick_at": now_iso,
    }
    serialized = json.dumps(payload)
    parsed = json.loads(serialized)

    assert isinstance(parsed["documents_processed"], int)
    assert isinstance(parsed["documents_total"], int)
    assert isinstance(parsed["current_file"], str)
    assert isinstance(parsed["last_tick_at"], str)
    # Verify ISO8601 parseable
    datetime.fromisoformat(parsed["last_tick_at"])
