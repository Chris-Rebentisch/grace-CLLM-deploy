"""Repository tests for ±1.5× re-run flow (Chunk 41, CP5, D321)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.decomposition import rerun_repository, run_repository
from src.decomposition.rerun_repository import (
    RERUN_HARD_CAP,
    ArchiveDriftError,
    RerunCapExceededError,
    create_rerun_run,
    lineage_depth,
)
from src.decomposition.run_repository import create_run, finalize_run


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


def _make_initial(db_session, archive: Path) -> dict:
    pred = create_run(
        db_session, archive_root=str(archive), operator=None
    )
    # Seed Layer 1 + Layer 2 so the re-run can copy them forward via INSERT.
    finalize_run(
        db_session,
        pred["run_id"],
        status="completed",
        completed_at=None,
        layer_artifacts={
            "layer1_summary": {
                "archive_root": str(archive),
                "total_files": 0,
                "files": [],
                "folders": [],
            },
            "layer2_decision": {
                "algorithm": "hdbscan",
                "cluster_count": 0,
                "outlier_count": 0,
                "outlier_ratio_at_gate": 0.0,
                "outlier_ratio_gate": 0.30,
                "cluster_labels": [],
                "umap": {
                    "n_components": 10,
                    "n_neighbors": 15,
                    "min_dist": 0.1,
                    "metric": "cosine",
                    "random_state": 42,
                },
                "embedding": {
                    "model": "mock",
                    "dimension": 4,
                    "document_count": 0,
                },
            },
        },
    )
    db_session.commit()
    return pred


# ---------- Happy path: predecessor copy-forward + direction wiring ----------


def test_create_rerun_run_copies_layers_1_and_2_forward(
    db_session, tmp_path: Path
):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_initial(db_session, archive)

    succ = create_rerun_run(
        db_session,
        predecessor_run_id=pred["run_id"],
        direction="finer",
    )
    db_session.commit()

    refreshed = run_repository.get_run(db_session, succ["run_id"])
    assert refreshed is not None
    assert refreshed["resumed_from_run_id"] == pred["run_id"]
    assert refreshed["layer1_summary"] is not None
    assert refreshed["layer2_decision"] is not None
    # Layer 3 NOT carried (recomputes under new resolution).
    assert refreshed["layer3_decision"] is None
    assert succ["direction"] == "finer"
    assert succ["lineage_depth"] == 1


def test_create_rerun_run_invalid_direction_raises(db_session, tmp_path: Path):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_initial(db_session, archive)
    with pytest.raises(ValueError, match="finer"):
        create_rerun_run(
            db_session,
            predecessor_run_id=pred["run_id"],
            direction="sideways",  # type: ignore[arg-type]
        )
    db_session.rollback()


# ---------- Hard cap enforcement ----------


def test_create_rerun_run_raises_at_cap(db_session, tmp_path: Path):
    """Walk a chain to depth = RERUN_HARD_CAP; the next attempt raises."""
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_initial(db_session, archive)

    # Build a chain at the cap (5 successors of the original).
    current = pred
    for i in range(RERUN_HARD_CAP):
        succ = create_rerun_run(
            db_session,
            predecessor_run_id=current["run_id"],
            direction="finer" if i % 2 == 0 else "coarser",
        )
        db_session.commit()
        current = run_repository.get_run(db_session, succ["run_id"])

    # Lineage at the deepest tip is RERUN_HARD_CAP.
    assert lineage_depth(db_session, current["run_id"]) == RERUN_HARD_CAP

    with pytest.raises(RerunCapExceededError):
        create_rerun_run(
            db_session,
            predecessor_run_id=current["run_id"],
            direction="finer",
        )
    db_session.rollback()


# ---------- Drift fail-loud ----------


def test_create_rerun_run_archive_drift_raises(
    db_session, tmp_path: Path, monkeypatch
):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_initial(db_session, archive)

    monkeypatch.setattr(
        rerun_repository, "_canonical_hash", lambda _root: "f" * 64
    )
    with pytest.raises(ArchiveDriftError):
        create_rerun_run(
            db_session,
            predecessor_run_id=pred["run_id"],
            direction="coarser",
        )
    db_session.rollback()


# ---------- Predecessor immutability ----------


def test_create_rerun_run_does_not_modify_predecessor(
    db_session, tmp_path: Path
):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_initial(db_session, archive)
    pred_layer1 = run_repository.get_run(db_session, pred["run_id"])[
        "layer1_summary"
    ]

    create_rerun_run(
        db_session,
        predecessor_run_id=pred["run_id"],
        direction="finer",
    )
    db_session.commit()

    refreshed = run_repository.get_run(db_session, pred["run_id"])
    # Predecessor row is append-only; layer1 unchanged.
    assert refreshed["layer1_summary"] == pred_layer1
    assert refreshed["status"] == "completed"
