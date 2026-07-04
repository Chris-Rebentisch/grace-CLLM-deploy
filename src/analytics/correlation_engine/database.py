"""SQLAlchemy MetaData for correlation_runs + diagnostic_records + alert_events
(Chunk 33).

Module-level Table definitions so the dashboard contract lint
(``tests/analytics/test_dashboard_contract.py``) can discover the schema
and verify SQL panel columns. The writer in
``correlation_record_writer.py`` keeps its own local Table objects to
remain decoupled.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

metadata = MetaData()

correlation_runs = Table(
    "correlation_runs",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("status", Text, nullable=False),
    Column("triggered_by", Text, nullable=False),
    Column("config_hash", Text, nullable=False),
)

diagnostic_records = Table(
    "diagnostic_records",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("run_id", PG_UUID(as_uuid=True), nullable=False),
    Column("pattern_name", Text, nullable=False),
    Column("ontology_module", Text, nullable=False),
    Column("suspected_root_cause_module", Text, nullable=False),
    Column("correlation_strength", Float, nullable=False),
    Column("contributing_signals", JSONB, nullable=False),
    Column("evidence_snapshot", JSONB, nullable=False),
    Column("human_summary", Text, nullable=False),
    Column("detected_at", DateTime(timezone=True), nullable=False),
)

alert_events = Table(
    "alert_events",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("alertname", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("ontology_module", Text, nullable=True),
    Column("state", Text, nullable=False),
    Column("fired_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("labels", JSONB, nullable=False),
    Column("annotations", JSONB, nullable=False),
    Column("webhook_payload_hash", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)
