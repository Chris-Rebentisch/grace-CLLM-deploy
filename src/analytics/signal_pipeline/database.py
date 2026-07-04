"""SQLAlchemy MetaData for analytics_signals + signal_runs (Chunk 32).

Module-level Table definitions so the dashboard contract lint
(``tests/analytics/test_dashboard_contract.py``) can discover the
schema and verify SQL panel columns. The writer in
``signal_record_writer.py`` keeps its own local Table objects to remain
decoupled.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

metadata = MetaData()

signal_runs = Table(
    "signal_runs",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("status", Text, nullable=False),
    Column("triggered_by", Text, nullable=False),
    Column("config_hash", Text, nullable=False),
)

analytics_signals = Table(
    "analytics_signals",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("run_id", PG_UUID(as_uuid=True), nullable=False),
    Column("signal_type", Text, nullable=False),
    Column("ontology_module", Text, nullable=False),
    Column("strength", Float, nullable=False),
    Column("evidence_snapshot", JSONB, nullable=False),
    Column("detected_at", DateTime(timezone=True), nullable=False),
)
