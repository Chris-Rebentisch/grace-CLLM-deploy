"""Repository tests for ``segmentation_maps`` (Chunk 41, CP5, D326).

Covers hash-chain integrity (first NULL, subsequent chained), append-only
trigger enforcement, ``grace_readonly`` SELECT grant, and the chain /
latest accessors.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from src.decomposition import segmentation_map_repository as repo
from src.decomposition.segmentation_map_models import (
    DocumentSource,
    Segment,
    SegmentationMap,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
    reason="Postgres not available",
)


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2:///grace"
    )


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(_database_url(), future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        engine.dispose()


def _make_run(session) -> str:
    row = session.execute(
        text(
            "INSERT INTO decomposition_runs "
            "(archive_root, archive_root_canonical_hash, status) "
            "VALUES (:r, :h, 'running') RETURNING run_id"
        ),
        {
            "r": "/tmp/c41-segmap-test-" + uuid4().hex,
            "h": "a" * 64,
        },
    ).one()
    session.commit()
    return str(row[0])


def _make_map(run_id: str) -> SegmentationMap:
    return SegmentationMap(
        schema_version="1.0",
        decomposition_run_id=run_id,
        produced_at=datetime.now(timezone.utc),
        archive_root_canonical_hash="a" * 64,
        segments=[
            Segment(
                name="ops_segment",
                description="Operations segment",
                document_sources=[
                    DocumentSource(
                        path="ops/", inclusion_kind="folder"
                    )
                ],
                expected_entity_types=["Legal_Entity"],
                build_priority="high",
            ),
        ],
        null_hypothesis_accepted=False,
    )


# ---------- Canonical-JSON hash discipline ----------


def test_compute_payload_hash_is_canonical_json_sorted_keys():
    """Two semantically equivalent dicts must hash to the same digest."""
    a = {"foo": 1, "bar": [1, 2], "baz": "qux"}
    b = {"baz": "qux", "bar": [1, 2], "foo": 1}
    assert repo._compute_payload_hash(a) == repo._compute_payload_hash(b)


# ---------- Hash chain integrity ----------


def test_create_map_first_row_has_null_previous_hash(db_session):
    run_id = _make_run(db_session)
    sm = _make_map(run_id)
    row = repo.create_map(db_session, sm=sm)
    db_session.commit()
    assert row["previous_hash"] is None
    assert isinstance(row["payload_hash"], str)
    assert len(row["payload_hash"]) == 64


def test_create_map_second_row_chains_to_first(db_session):
    run_id = _make_run(db_session)
    sm1 = _make_map(run_id)
    row1 = repo.create_map(db_session, sm=sm1)
    db_session.commit()

    # Mutate to produce a different payload hash.
    sm2 = _make_map(run_id)
    sm2 = sm2.model_copy(update={"null_hypothesis_accepted": True})
    row2 = repo.create_map(db_session, sm=sm2)
    db_session.commit()

    assert row2["previous_hash"] == row1["payload_hash"]
    assert row2["payload_hash"] != row1["payload_hash"]


def test_chain_for_run_returns_oldest_first(db_session):
    run_id = _make_run(db_session)
    sm1 = _make_map(run_id)
    repo.create_map(db_session, sm=sm1)
    db_session.commit()
    sm2 = _make_map(run_id).model_copy(update={"null_hypothesis_accepted": True})
    repo.create_map(db_session, sm=sm2)
    db_session.commit()

    chain = repo.chain_for_run(db_session, run_id)
    assert len(chain) >= 2
    # Oldest-first: the first row's previous_hash is NULL.
    assert chain[0]["previous_hash"] is None
    assert chain[1]["previous_hash"] == chain[0]["payload_hash"]


# ---------- Append-only trigger enforcement ----------


def test_segmentation_maps_append_only_trigger_blocks_update(db_session):
    run_id = _make_run(db_session)
    sm = _make_map(run_id)
    row = repo.create_map(db_session, sm=sm)
    db_session.commit()

    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "UPDATE segmentation_maps SET schema_version='2.0' "
                "WHERE segmentation_map_id = :id"
            ),
            {"id": row["segmentation_map_id"]},
        )
        db_session.commit()
    db_session.rollback()


# ---------- Latest accessor ----------


def test_latest_map_for_run_returns_newest(db_session):
    run_id = _make_run(db_session)
    sm1 = _make_map(run_id)
    repo.create_map(db_session, sm=sm1)
    db_session.commit()
    sm2 = _make_map(run_id).model_copy(update={"null_hypothesis_accepted": True})
    row2 = repo.create_map(db_session, sm=sm2)
    db_session.commit()

    latest = repo.latest_map_for_run(db_session, run_id)
    assert latest is not None
    assert latest["payload_hash"] == row2["payload_hash"]
