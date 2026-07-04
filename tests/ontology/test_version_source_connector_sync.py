"""F-44 regression: VersionSource must include 'connector_sync' so connector
syncs record accurate provenance (previously mislabeled 'manual' because the
enum lacked the value and ratify would otherwise reject it)."""

from __future__ import annotations

from src.ontology.models import VersionSource


def test_connector_sync_is_a_valid_version_source():
    assert VersionSource("connector_sync") is VersionSource.CONNECTOR_SYNC
    assert VersionSource.CONNECTOR_SYNC.value == "connector_sync"


def test_all_legacy_sources_still_present():
    values = {s.value for s in VersionSource}
    assert {"discovery", "guided_review", "adaptive_evolution", "manual"} <= values
