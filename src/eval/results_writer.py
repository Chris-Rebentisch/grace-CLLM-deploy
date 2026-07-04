"""Eval results persistence (Chunk 34, D259).

Writes one ``eval_runs`` row + N ``deepeval_results`` rows in a single
SQLAlchemy transaction. Hash helpers mirror the Chunk 32/33 pattern
(``hashlib.sha256(json.dumps(model_dump(mode="json"), sort_keys=True)
.encode()).hexdigest()[:16]``) so promotion-key parity is preserved.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from src.eval.deepeval_runner import EvalRun


# Lightweight Core Table definitions matching the c34 migration. Used for
# bulk INSERT without depending on a global ORM declarative base — keeps
# this module isolated from the larger registry.
_metadata = MetaData()


eval_runs_pg = Table(
    "eval_runs",
    _metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("status", Text(), nullable=False),
    Column("triggered_by", Text(), nullable=False),
    Column("config_hash", Text(), nullable=False),
    Column("golden_dataset_hash", Text(), nullable=False),
    Column("total_cases", Integer(), nullable=False),
    Column("passed_warn_floor", Integer(), nullable=False),
    Column("passed_fail_floor", Integer(), nullable=False),
    CheckConstraint(
        "status IN ('running','success','partial_failure','error')",
        name="ck_eval_runs_status",
    ),
    CheckConstraint(
        "triggered_by IN ('cli','api')",
        name="ck_eval_runs_triggered_by",
    ),
)


deepeval_results_pg = Table(
    "deepeval_results",
    _metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "run_id",
        PG_UUID(as_uuid=True),
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("case_id", Text(), nullable=False),
    Column("query_text", Text(), nullable=False),
    Column("metric_name", Text(), nullable=False),
    Column("metric_score", Float(), nullable=False),
    Column("passed_warn_floor", Boolean(), nullable=False),
    Column("passed_fail_floor", Boolean(), nullable=False),
    Column("latency_ms", Integer(), nullable=True),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "run_id", "case_id", "metric_name",
        name="uq_deepeval_results_run_case_metric",
    ),
)


def _stable_hash(payload: Any) -> str:
    """SHA-256 prefix-16 of a stable JSON serialization (Chunk 32/33 parity)."""
    if isinstance(payload, BaseModel):
        as_obj = payload.model_dump(mode="json")
    elif hasattr(payload, "model_dump") and callable(payload.model_dump):
        try:
            as_obj = payload.model_dump(mode="json")
        except TypeError:
            as_obj = payload.model_dump()
    else:
        as_obj = payload
    blob = json.dumps(as_obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def hash_config(config: Any) -> str:
    """Stable 16-char prefix-hash of the eval config."""
    if hasattr(config, "__dict__"):
        return _stable_hash(getattr(config, "__dict__"))
    return _stable_hash(config)


def hash_golden_dataset(cases: list[Any]) -> str:
    """Stable 16-char prefix-hash of the golden case list (order-independent on case_id)."""
    serialized = []
    for c in cases:
        if hasattr(c, "model_dump"):
            serialized.append(c.model_dump(mode="json"))
        else:
            serialized.append(dict(c))
    serialized.sort(key=lambda d: d.get("case_id", ""))
    return _stable_hash(serialized)


def write_run(session: Session, run: EvalRun) -> None:
    """Persist one ``eval_runs`` row + the per-case ``deepeval_results`` rows.

    Single transaction. The caller is responsible for ``session.commit()``
    or ``session.rollback()``; the function flushes but does not commit so
    the integration tests can use ``Session.begin()``-style scopes.
    """
    run_payload = {
        "id": run.id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "config_hash": run.config_hash,
        "golden_dataset_hash": run.golden_dataset_hash,
        "total_cases": run.total_cases,
        "passed_warn_floor": run.passed_warn_floor,
        "passed_fail_floor": run.passed_fail_floor,
    }
    session.execute(eval_runs_pg.insert().values(**run_payload))

    if run.results:
        rows = [
            {
                "id": uuid4(),
                "run_id": run.id,
                "case_id": r.case_id,
                "query_text": r.query_text,
                "metric_name": r.metric_name,
                "metric_score": float(r.metric_score),
                "passed_warn_floor": bool(r.passed_warn_floor),
                "passed_fail_floor": bool(r.passed_fail_floor),
                "latency_ms": r.latency_ms,
                "evaluated_at": run.completed_at or run.started_at,
            }
            for r in run.results
        ]
        session.execute(deepeval_results_pg.insert(), rows)
    session.flush()
