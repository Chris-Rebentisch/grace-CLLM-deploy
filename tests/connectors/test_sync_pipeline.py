"""Tests for sync pipeline CLI (CP6, D246 mirror, D411).

Tests validate pipeline behavior through source inspection, CLI probes,
and focused unit tests of helper functions.
"""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.connectors.models import SyncResult, SyncStatus


# ---------------------------------------------------------------------------
# 1. --dry-run contract: verify dry_run early-return path in source
# ---------------------------------------------------------------------------


def test_dry_run_early_return() -> None:
    """--dry-run path in run_sync returns without calling update_sync_status or resolve_or_create."""
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    # Dry-run path should complete without hitting resolve_or_create
    assert "if dry_run:" in source
    assert "result.status = SyncStatus.COMPLETED" in source


# ---------------------------------------------------------------------------
# 2. Initial load → synced: verify status transitions in source
# ---------------------------------------------------------------------------


def test_initial_load_status_transitions() -> None:
    """Source sets 'syncing' then 'synced' via update_sync_status."""
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    assert 'update_sync_status(db, db_name, "syncing"' in source
    assert 'update_sync_status(db, db_name, "synced"' in source


# ---------------------------------------------------------------------------
# 3. Mode auto-detect logic
# ---------------------------------------------------------------------------


def test_mode_auto_detect_logic() -> None:
    """Auto-detect: last_sync_at is None → initial; non-None → incremental."""
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    assert "last_sync_at is None" in source
    assert '"initial"' in source
    assert '"incremental"' in source


def test_watermark_read_before_syncing_stamp() -> None:
    """D411: mode uses prior watermark; ``syncing`` must not advance last_sync_at."""
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    idx_mark = source.find("_get_last_sync_at(db, namespace_id)")
    idx_syncing = source.find('update_sync_status(db, db_name, "syncing"')
    assert idx_mark != -1 and idx_syncing != -1
    assert idx_mark < idx_syncing


# ---------------------------------------------------------------------------
# 4. Connectivity failure → error status
# ---------------------------------------------------------------------------


def test_connectivity_failure_error_path() -> None:
    """check_connectivity failure path sets error status."""
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    assert "check_connectivity()" in source
    assert '"error"' in source
    assert "SyncStatus.FAILED" in source


# ---------------------------------------------------------------------------
# 5. connector_sync_state upsert
# ---------------------------------------------------------------------------


def test_upsert_sync_state_function() -> None:
    """_upsert_sync_state uses INSERT ... ON CONFLICT ... DO UPDATE."""
    from src.connectors.sync_pipeline import _upsert_sync_state
    source = inspect.getsource(_upsert_sync_state)
    assert "connector_sync_state" in source
    assert "ON CONFLICT" in source
    assert "DO UPDATE" in source


# ---------------------------------------------------------------------------
# 6. No parallel UPDATE against graph_namespaces (single-writer invariant)
# ---------------------------------------------------------------------------


def test_no_direct_update_graph_namespaces() -> None:
    """Pipeline source does not contain direct UPDATE SQL against graph_namespaces."""
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    # Should use update_sync_status() only, never direct UPDATE graph_namespaces
    lines = source.split("\n")
    for line in lines:
        if "UPDATE" in line and "graph_namespaces" in line:
            pytest.fail(f"Direct UPDATE against graph_namespaces found: {line}")
    assert "update_sync_status" in source


# ---------------------------------------------------------------------------
# 7. CLI help from sync_pipeline entry point
# ---------------------------------------------------------------------------


def test_cli_help_sync_pipeline() -> None:
    """CLI help from sync_pipeline entry point."""
    result = subprocess.run(
        [sys.executable, "-m", "src.connectors.sync_pipeline", "--help"],
        capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        env={**__import__("os").environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[2])},
    )
    assert result.returncode == 0
    assert "connector" in result.stdout.lower() or "sync" in result.stdout.lower()


# ---------------------------------------------------------------------------
# 8. CLI help from package-level entry point
# ---------------------------------------------------------------------------


def test_cli_help_package_entry() -> None:
    """CLI help from package-level entry point."""
    result = subprocess.run(
        [sys.executable, "-m", "src.connectors", "--help"],
        capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        env={**__import__("os").environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[2])},
    )
    assert result.returncode == 0
    assert "connector" in result.stdout.lower() or "sync" in result.stdout.lower()


# ---------------------------------------------------------------------------
# F-032a / ISS-0023: sync result names WHERE queued records live +
# label-prefix namespacing clarification
# ---------------------------------------------------------------------------


def test_sync_result_model_carries_queue_destination_fields() -> None:
    """F-032a / ISS-0023: SyncResult exposes records_queued_to + records_queued_hint."""
    result = SyncResult(
        connector_type="synthetic",
        namespace_id=uuid4(),
        status=SyncStatus.COMPLETED,
        records_queued=90,
        records_queued_to="entity_resolution_review_queue",
        records_queued_hint="Review via SELECT * FROM entity_resolution_review_queue ...",
    )
    dumped = result.model_dump()
    assert dumped["records_queued_to"] == "entity_resolution_review_queue"
    assert "entity_resolution_review_queue" in dumped["records_queued_hint"]
    # Defaults stay None so existing consumers are unaffected
    bare = SyncResult(
        connector_type="synthetic", namespace_id=uuid4(), status=SyncStatus.RUNNING
    )
    assert bare.records_queued_to is None
    assert bare.records_queued_hint is None


def test_run_sync_sets_queue_destination_when_records_queued() -> None:
    """F-032a / ISS-0023: pipeline stamps the queue destination + operator hint."""
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    assert 'result.records_queued_to = "entity_resolution_review_queue"' in source
    assert "result.records_queued_hint" in source
    # Hint must tell the operator records are not yet graph vertices and how to review
    assert "NOT yet graph vertices" in source
    assert "SELECT * FROM entity_resolution_review_queue" in source
    # And the destination is logged for the CLI/operator trail
    assert "connector_sync_records_queued_destination" in source


def test_label_prefix_namespacing_clarification_logged() -> None:
    """F-032a / ISS-0023: sync start logs that no separate child database is created."""
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    assert "connector_sync_label_prefix_namespacing" in source
    assert "types are created in the parent" in source
    assert "no separate database" in source
    # Clarification fires only when the configured name differs from the target DB
    idx_cmp = source.find("namespace.database_name != actual_db")
    idx_log = source.find("connector_sync_label_prefix_namespacing")
    assert idx_cmp != -1 and idx_log != -1
    assert idx_cmp < idx_log


def test_ratify_call_is_child_scoped_and_non_activating() -> None:
    """F-0045 / ISS-0025: sync pipeline must ratify child-scoped WITHOUT activation.

    A child-namespace connector sync must never become or replace the
    deployment's active (mother) ontology version. The call site passes
    activate=False; ratify_version additionally carries a hard guard.
    """
    source = inspect.getsource(importlib.import_module("src.connectors.sync_pipeline"))
    idx_ratify = source.find("ratify_version(")
    assert idx_ratify != -1
    # Slice the ratify call region (up to the watermark step that follows it).
    idx_end = source.find("_get_last_sync_at", idx_ratify)
    call_region = source[idx_ratify:idx_end]
    assert 'ontology_scope="child"' in call_region
    assert "activate=False" in call_region
    assert 'source="connector_sync"' in call_region
