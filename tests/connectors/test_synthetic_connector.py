"""Tests for SyntheticConnector (CP3, D412)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.connectors.models import ConnectorConfig, ConnectorHealthStatus, ConnectorRecord
from src.connectors.synthetic_connector import (
    SYNA_TYPES,
    SYNB_TYPES,
    SyntheticConnector,
    _OVERLAP_PAIRS,
)


def _make_config(seed: int = 42, scale: float = 1.0) -> ConnectorConfig:
    return ConnectorConfig(
        connector_type="synthetic",
        namespace_id=uuid4(),
        config_overrides={"synthetic_seed": seed, "scale_factor": scale},
    )


async def _collect_async(connector: SyntheticConnector, method: str = "initial_load", **kwargs) -> list[ConnectorRecord]:
    records = []
    gen = getattr(connector, method)(**kwargs)
    async for r in gen:
        records.append(r)
    return records


def _collect(connector: SyntheticConnector, method: str = "initial_load", **kwargs) -> list[ConnectorRecord]:
    return asyncio.run(_collect_async(connector, method, **kwargs))


def test_discover_schema_types() -> None:
    """discover_schema returns 5 SynA types and 4 SynB types."""
    conn = SyntheticConnector(_make_config())
    schema = conn.discover_schema()
    syna_types = [k for k, v in schema.items() if v.get("source_system") == "SyntheticA"]
    synb_types = [k for k, v in schema.items() if v.get("source_system") == "SyntheticB"]
    assert len(syna_types) == 5
    assert len(synb_types) == 4


def test_initial_load_determinism() -> None:
    """Two initial_load runs with same seed produce identical ordered streams."""
    conn1 = SyntheticConnector(_make_config(seed=42))
    conn2 = SyntheticConnector(_make_config(seed=42))
    records1 = _collect(conn1)
    records2 = _collect(conn2)
    assert len(records1) == len(records2)
    for r1, r2 in zip(records1, records2):
        assert r1.source_record_id == r2.source_record_id
        assert r1.name == r2.name
        assert r1.entity_type == r2.entity_type
        assert r1.source_system == r2.source_system


def test_incremental_sync_filters() -> None:
    """incremental_sync returns only post-since records."""
    conn = SyntheticConnector(_make_config())
    cutoff = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=130)
    records = _collect(conn, "incremental_sync", since=cutoff)
    assert len(records) > 0
    for r in records:
        assert r.source_updated_at > cutoff


def test_overlap_pairs_present() -> None:
    """10 known overlap pairs present with embedding-similar names."""
    conn = SyntheticConnector(_make_config())
    records = _collect(conn)
    names = {r.name for r in records}
    for syna_name, synb_name in _OVERLAP_PAIRS:
        assert syna_name in names, f"Missing SynA overlap name: {syna_name}"
        assert synb_name in names, f"Missing SynB overlap name: {synb_name}"


def test_unresolved_entities_present() -> None:
    """5 unresolved-per-system entities present."""
    conn = SyntheticConnector(_make_config())
    records = _collect(conn)
    syna_unresolved = [r for r in records if r.source_system == "SyntheticA" and r.name.startswith("UniqueA_")]
    synb_unresolved = [r for r in records if r.source_system == "SyntheticB" and r.name.startswith("UniqueB_")]
    assert len(syna_unresolved) == 5
    assert len(synb_unresolved) == 5


def test_check_connectivity() -> None:
    """check_connectivity returns True."""
    conn = SyntheticConnector(_make_config())
    assert conn.check_connectivity() is True


def test_health_check() -> None:
    """health_check returns ConnectorHealthStatus(status='healthy')."""
    conn = SyntheticConnector(_make_config())
    health = conn.health_check()
    assert isinstance(health, ConnectorHealthStatus)
    assert health.status == "healthy"


def test_scale_factor_doubles() -> None:
    """scale_factor 2.0 doubles entity counts."""
    conn_1x = SyntheticConnector(_make_config(scale=1.0))
    conn_2x = SyntheticConnector(_make_config(scale=2.0))
    records_1x = _collect(conn_1x)
    records_2x = _collect(conn_2x)
    # 1x: 50 + 40 = 90, 2x: 100 + 80 = 180
    assert len(records_1x) == 90
    assert len(records_2x) == 180


def test_discover_schema_valid_structure() -> None:
    """discover_schema output has valid JSON-Schema-like structure."""
    conn = SyntheticConnector(_make_config())
    schema = conn.discover_schema()
    for type_name, type_schema in schema.items():
        assert "type" in type_schema
        assert type_schema["type"] == "object"
        assert "properties" in type_schema
        assert isinstance(type_schema["properties"], dict)


def test_overlap_pair_names_distinct_between_systems() -> None:
    """Overlap pair names are distinct between SynA/SynB but embedding-similar."""
    conn = SyntheticConnector(_make_config())
    records = _collect(conn)
    syna_names = {r.name for r in records if r.source_system == "SyntheticA"}
    synb_names = {r.name for r in records if r.source_system == "SyntheticB"}
    for syna_name, synb_name in _OVERLAP_PAIRS:
        assert syna_name in syna_names
        assert synb_name in synb_names
        assert syna_name != synb_name


def test_overlap_records_carry_company_types() -> None:
    """F-0047a: overlap names are company names — types must align.

    The round-robin zip previously produced e.g. Project_Site "Summit
    Builders" and Equipment_Unit "Pacific Steel Works". Overlap records
    must be Construction_Company (SynA) / Business_Entity (SynB).
    """
    conn = SyntheticConnector(_make_config())
    records = _collect(conn)
    by_name = {r.name: r for r in records}
    for syna_name, synb_name in _OVERLAP_PAIRS:
        assert by_name[syna_name].entity_type == "Construction_Company", (
            f"SynA overlap {syna_name!r} typed {by_name[syna_name].entity_type!r}"
        )
        assert by_name[synb_name].entity_type == "Business_Entity", (
            f"SynB overlap {synb_name!r} typed {by_name[synb_name].entity_type!r}"
        )


def test_non_company_types_do_not_get_company_style_overlap_names() -> None:
    """F-0047a: the two originally-misaligned pairs are aligned end-to-end."""
    conn = SyntheticConnector(_make_config())
    records = _collect(conn)
    misaligned = [
        r
        for r in records
        if r.name in ("Summit Builders", "Pacific Steel Works")
        and r.entity_type in ("Project_Site", "Equipment_Unit")
    ]
    assert misaligned == []


def test_remaining_entities_have_type_appropriate_names() -> None:
    """F-0047a: non-overlap round-robin entities get names matching their type."""
    conn = SyntheticConnector(_make_config())
    records = _collect(conn)
    for r in records:
        if r.name.startswith(("UniqueA_", "UniqueB_")):
            continue  # unresolved markers are type-agnostic by design
        if r.entity_type == "Material_Order":
            assert r.name.startswith("PO-"), f"Material_Order named {r.name!r}"
        if r.entity_type in ("Project_Site", "Job_Location"):
            assert "Site" in r.name, f"{r.entity_type} named {r.name!r}"
        if r.entity_type in ("Equipment_Unit", "Asset_Item"):
            assert "Unit #" in r.name, f"{r.entity_type} named {r.name!r}"
