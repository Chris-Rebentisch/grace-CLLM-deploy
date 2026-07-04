"""Guard tests for the C1 live-proof adapter-wiring fixes.

D536 — production pipeline path populates the adapter registry (was: empty
registry -> KeyError on get_adapter, masked because test_pipeline.py patches
get_adapter with a mock).

D537 — the pipeline reconciles the persisted source_id to the real
ingestion_sources row id (was: every adapter stamps a random
self._source_id = uuid4() -> FK violation on every insert, silently caught and
mislabeled "duplicate_message_id" while the run reported success with 0 rows).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.ingestion.adapter_base import AdapterResult
from src.ingestion.models import (
    CommunicationEvent,
    CommunicationEventRow,
    IngestionCheckpoint,
    IngestionSource,
)
from src.ingestion.pipeline import IngestionPipeline

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_d536_importing_pipeline_populates_adapter_registry():
    """Importing the production pipeline module (the single chokepoint that
    calls get_adapter) must trigger the @register_adapter side-effects in a
    CLEAN interpreter — no explicit adapter import, no mock. This is the
    anti-regression for the masked 'Unknown adapter type ... Registered: []'
    failure of the run/cycle/API spawn path."""
    code = (
        "import src.ingestion.pipeline;"
        "from src.ingestion.adapter_registry import list_registered;"
        "t={d['source_type'] for d in list_registered()};"
        "assert {'eml','mbox','msg','pst'} <= t, t;"
        "print('REGISTRY_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env={"PYTHONPATH": str(_REPO_ROOT), "PATH": __import__("os").environ.get("PATH", "")},
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "REGISTRY_OK" in result.stdout


def _mock_source(source_id, source_type="eml"):
    src = MagicMock(spec=IngestionSource)
    src.id = source_id
    src.source_type = source_type
    src.config_json = {"source_type": source_type, "directory_path": "/tmp/x"}
    src.segment = "test"
    src.enabled = True
    return src


def test_d537_pipeline_persists_real_source_id_not_adapter_uuid():
    """The adapter stamps a fresh random source_id on every event; the pipeline
    MUST overwrite it with the real ingestion_sources row id before persisting,
    or the row FK-violates. Hermetic: mock adapter + mock session capture."""
    real_source_id = uuid4()
    random_event_source_id = uuid4()  # what the buggy adapter stamps
    assert real_source_id != random_event_source_id

    source = _mock_source(real_source_id)
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = source
    added: list[object] = []
    db.add = added.append

    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    adapter.close = AsyncMock()

    async def _list_messages(*, limit=None):
        yield "m1"

    adapter.list_messages = _list_messages
    evt = CommunicationEvent(
        source_id=random_event_source_id,
        message_id="<wire@example.com>",
        sender_email="wire@example.com",
        source_type="eml",
    )
    adapter.parse_message = AsyncMock(return_value=AdapterResult(event=evt))
    adapter.checkpoint = MagicMock(
        return_value=IngestionCheckpoint(checkpoint_type="file_offset", value="1")
    )

    pipeline = IngestionPipeline(db)
    with patch("src.ingestion.pipeline.get_adapter", return_value=adapter):
        asyncio.run(pipeline.run(real_source_id))

    event_rows = [o for o in added if isinstance(o, CommunicationEventRow)]
    assert len(event_rows) == 1, f"expected 1 event row, got {len(event_rows)}"
    persisted = str(event_rows[0].source_id)
    assert persisted == str(real_source_id), (
        f"persisted source_id {persisted} must equal the real source id "
        f"{real_source_id}, not the adapter's random {random_event_source_id}"
    )
    assert persisted != str(random_event_source_id)
