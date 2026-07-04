"""Layer 5 decision logic tests (Chunk 41, CP6, D320)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.decomposition import layer5_decision, run_repository
from src.decomposition.layer5_decision import (
    VALID_DECISION_KINDS,
    ReformulationCapExceededError,
    record_layer5_decision,
    trigger_reformulation_path_b,
)
from src.decomposition.segmentation_map_models import Layer5DecisionPayload


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


def _make_run_with_layers(db_session, archive: Path) -> dict:
    pred = run_repository.create_run(
        db_session, archive_root=str(archive), operator=None
    )
    run_repository.finalize_run(
        db_session,
        pred["run_id"],
        status="paused_pre_layer5",
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
            "layer3_decision": {
                "document_count": 0,
                "edge_count": 0,
                "leiden_runs": [],
                "selected_seed": 1,
                "selected_modularity": 0.0,
                "mean_pairwise_ari": 0.7,
                "low_stability_flag": False,
                "community_assignments": {},
            },
        },
    )
    db_session.commit()
    return pred


def _payload(decision_kind: str, **extra) -> Layer5DecisionPayload:
    return Layer5DecisionPayload(
        decision_kind=decision_kind,
        rationale=extra.get("rationale", ""),
        decided_at=datetime.now(timezone.utc),
        modifications=extra.get("modifications", []),
        selected_hypothesis_name=extra.get("selected_hypothesis_name"),
    )


# ---------- Five decision_kind values reachable ----------


def test_valid_decision_kinds_set_matches_d320():
    assert VALID_DECISION_KINDS == {
        "accepted_segmented",
        "accepted_null",
        "rerun_finer",
        "rerun_coarser",
        "reject_all_reformulate",
    }


@pytest.mark.parametrize(
    "decision_kind",
    [
        "accepted_segmented",
        "accepted_null",
        "rerun_finer",
        "rerun_coarser",
        "reject_all_reformulate",
    ],
)
def test_record_layer5_decision_accepts_each_kind(
    db_session, tmp_path: Path, decision_kind: str
):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_run_with_layers(db_session, archive)
    extra = {}
    if decision_kind == "accepted_segmented":
        extra["selected_hypothesis_name"] = "Hypothesis A"
    payload = _payload(decision_kind, **extra)
    out = record_layer5_decision(
        db_session, run_id=pred["run_id"], payload=payload
    )
    db_session.commit()
    assert out["layer5_decision"] is not None
    assert out["layer5_decision"]["decision_kind"] == decision_kind


# ---------- First-write-only enforcement ----------


def test_record_layer5_decision_second_write_raises(
    db_session, tmp_path: Path
):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_run_with_layers(db_session, archive)
    record_layer5_decision(
        db_session,
        run_id=pred["run_id"],
        payload=_payload("accepted_null"),
    )
    db_session.commit()
    with pytest.raises(Exception):
        record_layer5_decision(
            db_session,
            run_id=pred["run_id"],
            payload=_payload("rerun_finer"),
        )
        db_session.commit()
    db_session.rollback()


# ---------- Path B reformulation INSERT ----------


def test_trigger_reformulation_path_b_inserts_successor(
    db_session, tmp_path: Path
):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_run_with_layers(db_session, archive)
    # Record the rejection on the predecessor first (so reformulation
    # cap counter sees it).
    record_layer5_decision(
        db_session,
        run_id=pred["run_id"],
        payload=_payload("reject_all_reformulate", rationale="too coarse"),
    )
    db_session.commit()

    succ = trigger_reformulation_path_b(
        db_session,
        predecessor_run_id=pred["run_id"],
        operator_rationale="needs deeper segmentation",
        cap_override=2,  # allow two passes for this test
    )
    db_session.commit()

    refreshed = run_repository.get_run(db_session, succ["run_id"])
    assert refreshed is not None
    assert refreshed["resumed_from_run_id"] == pred["run_id"]
    # Layers 1–3 carried forward.
    assert refreshed["layer1_summary"] is not None
    assert refreshed["layer2_decision"] is not None
    assert refreshed["layer3_decision"] is not None
    # Layer 4 + 5 + 6 NOT carried (recompute path).
    assert refreshed["layer4_hypotheses"] is None
    assert refreshed["layer5_decision"] is None


def test_reformulation_single_pass_cap_rejects_second_pass(
    db_session, tmp_path: Path
):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_run_with_layers(db_session, archive)
    record_layer5_decision(
        db_session,
        run_id=pred["run_id"],
        payload=_payload("reject_all_reformulate"),
    )
    db_session.commit()

    # cap_override=1 is the default — chain already has one
    # reformulation; the second attempt must raise.
    with pytest.raises(ReformulationCapExceededError):
        trigger_reformulation_path_b(
            db_session,
            predecessor_run_id=pred["run_id"],
            operator_rationale="second pass attempt",
            cap_override=1,
        )
    db_session.rollback()


# ---------- Predecessor is append-only ----------


def test_trigger_reformulation_does_not_modify_predecessor(
    db_session, tmp_path: Path
):
    archive = tmp_path / "archive"
    archive.mkdir()
    pred = _make_run_with_layers(db_session, archive)
    record_layer5_decision(
        db_session,
        run_id=pred["run_id"],
        payload=_payload("reject_all_reformulate"),
    )
    db_session.commit()

    pred_before = run_repository.get_run(db_session, pred["run_id"])
    trigger_reformulation_path_b(
        db_session,
        predecessor_run_id=pred["run_id"],
        operator_rationale="test",
        cap_override=2,
    )
    db_session.commit()
    pred_after = run_repository.get_run(db_session, pred["run_id"])
    assert pred_after["layer1_summary"] == pred_before["layer1_summary"]
    assert pred_after["layer5_decision"] == pred_before["layer5_decision"]


def test_record_layer5_decision_rejects_unknown_kind():
    """Pure validator surface — does not require DB."""
    # Pydantic on the payload itself enforces the Literal at construction
    # time; layer5_decision validates again as defense-in-depth.
    with pytest.raises(Exception):
        Layer5DecisionPayload(
            decision_kind="bogus",  # type: ignore[arg-type]
            rationale="",
            decided_at=datetime.now(timezone.utc),
        )
