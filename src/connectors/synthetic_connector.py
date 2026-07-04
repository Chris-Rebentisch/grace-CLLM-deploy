"""Synthetic test connector producing deterministic data (D412).

Generates SyntheticA (50 entities / 5 types / 80 relationships) and
SyntheticB (40 entities / 4 types / 60 relationships), with 10 known
overlap pairs having embedding-similar names and 5 unresolved-per-system
entities. All counts are multiplied by ``scale_factor``.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from faker import Faker

from src.connectors.base import BaseConnector
from src.connectors.models import (
    ConnectorConfig,
    ConnectorHealthStatus,
    ConnectorRecord,
    ConnectorRelationship,
)
from src.connectors.registry import register_connector

# ---------------------------------------------------------------------------
# Type definitions for each system
# ---------------------------------------------------------------------------

SYNA_TYPES: list[str] = [
    "Construction_Company",
    "Project_Site",
    "Equipment_Unit",
    "Worker_Profile",
    "Material_Order",
]

SYNB_TYPES: list[str] = [
    "Business_Entity",
    "Job_Location",
    "Asset_Item",
    "Personnel_Record",
]

# Overlap pairs: SynA name ↔ SynB name (embedding-similar but distinct).
# The seed ensures these are deterministically generated.
_OVERLAP_PAIRS: list[tuple[str, str]] = [
    ("Acme Construction", "Acme Construction Group"),
    ("Summit Builders", "Summit Builders Inc"),
    ("Pacific Steel Works", "Pacific Steel Works LLC"),
    ("Mountain View Excavation", "Mountain View Excavation Co"),
    ("River Delta Engineering", "River Delta Engineering Corp"),
    ("Coastal Concrete Supply", "Coastal Concrete Supplies"),
    ("Northern Timber Corp", "Northern Timber Corporation"),
    ("Valley Equipment Rental", "Valley Equipment Rentals"),
    ("Harbor Logistics", "Harbor Logistics Services"),
    ("Pioneer Safety Solutions", "Pioneer Safety Solutions Ltd"),
]


def _name_for_type(faker: Faker, entity_type: str, i: int) -> str:
    """Generate a type-appropriate deterministic name.

    F-0047a / ISS-0025 family: the fixture previously zipped
    ``faker.company()`` names onto round-robin types, emitting misaligned
    payloads (e.g. a Material_Order named like a company). Names now match
    the entity type they are attached to.
    """
    if entity_type in ("Construction_Company", "Business_Entity"):
        return faker.company()
    if entity_type in ("Project_Site", "Job_Location"):
        return f"{faker.city()} Site {i}"
    if entity_type in ("Equipment_Unit", "Asset_Item"):
        return f"{faker.word().capitalize()} Unit #{i:03d}"
    if entity_type in ("Worker_Profile", "Personnel_Record"):
        return faker.name()
    if entity_type == "Material_Order":
        return f"PO-{2025}{i:04d}"
    return faker.company()


def _base_entity_counts(scale: float) -> tuple[int, int]:
    """Return (syna_count, synb_count) scaled and rounded."""
    return max(1, round(50 * scale)), max(1, round(40 * scale))


def _base_rel_counts(scale: float) -> tuple[int, int]:
    return max(0, round(80 * scale)), max(0, round(60 * scale))


def _overlap_count(scale: float) -> int:
    return max(0, round(10 * scale))


def _unresolved_count(scale: float) -> int:
    return max(0, round(5 * scale))


@register_connector("synthetic")
class SyntheticConnector(BaseConnector):
    """Deterministic test-data connector for federation integration testing.

    Produces two synthetic source systems (SyntheticA, SyntheticB) with
    controllable overlap and unresolved entities. All randomness is seeded
    via ``synthetic_seed`` for full determinism.
    """

    connector_type = "synthetic"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._seed: int = config.config_overrides.get("synthetic_seed", 42)
        self._scale: float = config.config_overrides.get("scale_factor", 1.0)
        self._faker_a = Faker()
        self._faker_b = Faker()
        Faker.seed(self._seed)
        self._faker_a.seed_instance(self._seed)
        self._faker_b.seed_instance(self._seed + 1)

    # ---- Schema discovery ------------------------------------------------

    def discover_schema(self) -> dict:
        """Return JSON-Schema-like dict keyed by PascalCase type names."""
        schema: dict = {}
        for t in SYNA_TYPES:
            schema[t] = {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source_system": {"type": "string", "const": "SyntheticA"},
                    "created_at": {"type": "string", "format": "date-time"},
                },
                "source_system": "SyntheticA",
            }
        for t in SYNB_TYPES:
            schema[t] = {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source_system": {"type": "string", "const": "SyntheticB"},
                    "created_at": {"type": "string", "format": "date-time"},
                },
                "source_system": "SyntheticB",
            }
        return schema

    # ---- Connectivity / health -------------------------------------------

    def check_connectivity(self) -> bool:
        return True

    def health_check(self) -> ConnectorHealthStatus:
        return ConnectorHealthStatus(
            status="healthy",
            detail="Synthetic connector is always healthy",
            checked_at=datetime.now(UTC),
        )

    # ---- Data generation -------------------------------------------------

    def _generate_records(self) -> list[ConnectorRecord]:
        """Build the full deterministic record set."""
        # Reset seeds for determinism on every call.
        Faker.seed(self._seed)
        self._faker_a.seed_instance(self._seed)
        self._faker_b.seed_instance(self._seed + 1)

        records: list[ConnectorRecord] = []
        syna_count, synb_count = _base_entity_counts(self._scale)
        syna_rels, synb_rels = _base_rel_counts(self._scale)
        overlap = _overlap_count(self._scale)
        unresolved = _unresolved_count(self._scale)

        base_time = datetime(2025, 1, 1, tzinfo=UTC)

        # --- SynA entities ---
        syna_ids: list[str] = []
        # First: overlap entities (use fixed names, capped at available pairs)
        effective_overlap = min(overlap, len(_OVERLAP_PAIRS))
        for i in range(min(effective_overlap, syna_count)):
            rid = f"syna-{i:04d}"
            syna_ids.append(rid)
            records.append(
                ConnectorRecord(
                    source_record_id=rid,
                    # F-0047a / ISS-0025 family: the overlap names are ALL
                    # company names, but the round-robin `SYNA_TYPES[i % 5]`
                    # zip misaligned them (i=1 → Project_Site "Summit
                    # Builders", i=2 → Equipment_Unit "Pacific Steel Works"),
                    # feeding entity resolution cross-type company pairs.
                    # Overlap records are companies — pin the company type.
                    entity_type=SYNA_TYPES[0],  # Construction_Company
                    name=_OVERLAP_PAIRS[i][0],
                    properties={"industry": self._faker_a.bs(), "region": self._faker_a.city()},
                    source_system="SyntheticA",
                    source_updated_at=base_time + timedelta(hours=i),
                )
            )

        # Then: unresolved entities
        for i in range(effective_overlap, effective_overlap + min(unresolved, syna_count - effective_overlap)):
            rid = f"syna-{i:04d}"
            syna_ids.append(rid)
            # Highly unique names that won't match SynB
            records.append(
                ConnectorRecord(
                    source_record_id=rid,
                    entity_type=SYNA_TYPES[i % len(SYNA_TYPES)],
                    name=f"UniqueA_{self._faker_a.lexify('????????').upper()}_{i}",
                    properties={"unique_marker": True},
                    source_system="SyntheticA",
                    source_updated_at=base_time + timedelta(hours=i),
                )
            )

        # Remaining SynA entities
        for i in range(effective_overlap + unresolved, syna_count):
            rid = f"syna-{i:04d}"
            syna_ids.append(rid)
            # F-0047a: name must align with the round-robin type (was
            # faker.company() for every type — see _name_for_type).
            entity_type = SYNA_TYPES[i % len(SYNA_TYPES)]
            records.append(
                ConnectorRecord(
                    source_record_id=rid,
                    entity_type=entity_type,
                    name=_name_for_type(self._faker_a, entity_type, i),
                    properties={"industry": self._faker_a.bs()},
                    source_system="SyntheticA",
                    source_updated_at=base_time + timedelta(hours=i),
                )
            )

        # SynA relationships
        for i in range(min(syna_rels, max(0, len(syna_ids) - 1))):
            src_idx = i % len(syna_ids)
            tgt_idx = (i + 1) % len(syna_ids)
            records[src_idx].relationships.append(
                ConnectorRelationship(
                    target_record_id=syna_ids[tgt_idx],
                    relationship_type="Linked_To",
                    properties={"weight": (i % 10) / 10.0},
                )
            )

        # --- SynB entities ---
        synb_ids: list[str] = []
        # Overlap entities (capped at available pairs)
        for i in range(min(effective_overlap, synb_count)):
            rid = f"synb-{i:04d}"
            synb_ids.append(rid)
            records.append(
                ConnectorRecord(
                    source_record_id=rid,
                    # F-0047a / ISS-0025 family: same zip misalignment as the
                    # SynA overlap loop — overlap names are company names, so
                    # pin the SynB company type (was SYNB_TYPES[i % 4]).
                    entity_type=SYNB_TYPES[0],  # Business_Entity
                    name=_OVERLAP_PAIRS[i][1],
                    properties={"sector": self._faker_b.bs(), "city": self._faker_b.city()},
                    source_system="SyntheticB",
                    source_updated_at=base_time + timedelta(hours=i + 100),
                )
            )

        # Unresolved entities
        for i in range(effective_overlap, effective_overlap + min(unresolved, synb_count - effective_overlap)):
            rid = f"synb-{i:04d}"
            synb_ids.append(rid)
            records.append(
                ConnectorRecord(
                    source_record_id=rid,
                    entity_type=SYNB_TYPES[i % len(SYNB_TYPES)],
                    name=f"UniqueB_{self._faker_b.lexify('????????').upper()}_{i}",
                    properties={"unique_marker": True},
                    source_system="SyntheticB",
                    source_updated_at=base_time + timedelta(hours=i + 100),
                )
            )

        # Remaining SynB entities
        for i in range(effective_overlap + unresolved, synb_count):
            rid = f"synb-{i:04d}"
            synb_ids.append(rid)
            # F-0047a: name must align with the round-robin type (was
            # faker.company() for every type — see _name_for_type).
            entity_type = SYNB_TYPES[i % len(SYNB_TYPES)]
            records.append(
                ConnectorRecord(
                    source_record_id=rid,
                    entity_type=entity_type,
                    name=_name_for_type(self._faker_b, entity_type, i),
                    properties={"sector": self._faker_b.bs()},
                    source_system="SyntheticB",
                    source_updated_at=base_time + timedelta(hours=i + 100),
                )
            )

        # SynB relationships
        synb_start = syna_count  # offset into records list
        for i in range(min(synb_rels, max(0, len(synb_ids) - 1))):
            src_idx = synb_start + (i % len(synb_ids))
            tgt_idx = (i + 1) % len(synb_ids)
            records[src_idx].relationships.append(
                ConnectorRelationship(
                    target_record_id=synb_ids[tgt_idx],
                    relationship_type="Associated_With",
                    properties={"weight": (i % 10) / 10.0},
                )
            )

        return records

    async def initial_load(self) -> AsyncIterator[ConnectorRecord]:
        """Yield all records in deterministic order."""
        for record in self._generate_records():
            yield record

    async def incremental_sync(self, since: datetime) -> AsyncIterator[ConnectorRecord]:
        """Yield only records with source_updated_at > since."""
        for record in self._generate_records():
            if record.source_updated_at > since:
                yield record
