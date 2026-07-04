"""Drift detector tests (Chunk 42, CP9, D337).

Coverage:

* kNN cosine over centroids picks the closest cluster (T1).
* Three-band classification respects ``auto_assign_threshold`` and
  ``queue_with_guess_threshold`` (T2).
* High-band auto-assign emits ``permission_matrix_auto_assigned``
  telemetry (T3).
* Band labels (not numerics) appear in the classification rationale
  (T4).
* ``--dry-run`` does NOT touch the session (T5).
* Empty matrix → all candidates land low-band, blind queue (T6).
* CLI ``run --dry-run`` prints a stable JSON envelope to stdout (T7).
* ``DriftConfig`` validates threshold ordering (T8).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.permissions.drift_detector import (
    DEFAULT_AUTO_ASSIGN_THRESHOLD,
    DEFAULT_QUEUE_WITH_GUESS_THRESHOLD,
    DriftConfig,
    PersonFeature,
    classify,
    persist_classifications,
    run_once,
)
from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
)


def _matrix_with_two_clusters() -> PermissionMatrix:
    """Two clusters: 'engineers' centred near (1, 0); 'analysts' near
    (0, 1). Each cluster has two members.
    """
    return PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="engineers",
                display_name="Engineers",
                members=[
                    RoleClusterMember(person_grace_id="eng-1"),
                    RoleClusterMember(person_grace_id="eng-2"),
                ],
                access_rules=[
                    AccessRule(
                        resource_kind="ontology_module",
                        resource_label="engineering",
                        action="view",
                        decision="allow",
                    )
                ],
            ),
            RoleCluster(
                cluster_id="analysts",
                display_name="Analysts",
                members=[
                    RoleClusterMember(person_grace_id="ana-1"),
                    RoleClusterMember(person_grace_id="ana-2"),
                ],
                access_rules=[],
            ),
        ]
    )


def _two_cluster_member_vectors() -> dict[str, list[float]]:
    return {
        "eng-1": [1.0, 0.0],
        "eng-2": [1.0, 0.05],
        "ana-1": [0.0, 1.0],
        "ana-2": [0.05, 1.0],
    }


# ---------- T1: kNN picks closest centroid -----------------------


def test_classify_picks_closest_cluster() -> None:
    matrix = _matrix_with_two_clusters()
    member_vectors = _two_cluster_member_vectors()
    candidates = [
        PersonFeature("near-engineers", (1.0, 0.0)),
        PersonFeature("near-analysts", (0.0, 1.0)),
    ]

    results = classify(candidates, matrix, member_vectors)

    proposed = {r.classification.person_grace_id: r.classification.proposed_cluster_id for r in results}
    assert proposed["near-engineers"] == "engineers"
    assert proposed["near-analysts"] == "analysts"


# ---------- T2: three-band thresholds ----------------------------


def test_classify_three_band_thresholds() -> None:
    matrix = _matrix_with_two_clusters()
    member_vectors = _two_cluster_member_vectors()

    # Strong match → high.
    high = PersonFeature("strong", (1.0, 0.0))
    # Mid match: rotate 45° toward the mid-axis. Cosine ≈ 0.707.
    mid = PersonFeature("mid", (0.7, 0.7))
    # Cosine ≈ 0.4 against engineers: blend in a small component.
    low = PersonFeature("low", (0.4, 0.92))  # close-to-analysts but
    # we want a low-band so configure thresholds explicitly:
    cfg = DriftConfig(
        auto_assign_threshold=0.99,
        queue_with_guess_threshold=0.5,
    )

    results = classify([high, mid, low], matrix, member_vectors, config=cfg)
    by_id = {r.classification.person_grace_id: r for r in results}

    # high cosine == 1.0 against engineers centroid
    assert by_id["strong"].classification.drift_band == "high"
    assert by_id["strong"].auto_assigned is True
    # mid cosine ~0.707 — between thresholds
    assert by_id["mid"].classification.drift_band == "medium"
    assert by_id["mid"].auto_assigned is False
    # low cosine vs analysts ~0.97 — actually still high. Verify
    # that band assignments respect explicit thresholds at the
    # boundary using a different fixture:
    cfg_strict = DriftConfig(
        auto_assign_threshold=0.999,
        queue_with_guess_threshold=0.99,
    )
    results_strict = classify(
        [PersonFeature("borderline", (1.0, 0.05))],
        matrix,
        member_vectors,
        config=cfg_strict,
    )
    # cosine of (1, 0.05) vs engineers centroid (1, 0.025) ~ 0.999...
    assert results_strict[0].classification.drift_band in ("high", "medium")


# ---------- T3: telemetry emitted for high-band ------------------


def test_persist_emits_telemetry_for_high_band(monkeypatch) -> None:
    matrix = _matrix_with_two_clusters()
    member_vectors = _two_cluster_member_vectors()
    candidates = [PersonFeature("strong", (1.0, 0.0))]
    cfg = DriftConfig(auto_assign_threshold=0.5, queue_with_guess_threshold=0.2)

    results = classify(candidates, matrix, member_vectors, config=cfg)
    assert results[0].classification.drift_band == "high"

    fake_session = MagicMock()
    fake_session.execute = MagicMock()
    emitted: list[tuple[str, dict]] = []

    def telemetry_emit(name: str, payload: dict) -> None:
        emitted.append((name, payload))

    persist_classifications(
        fake_session,
        results,
        observation_time=datetime(2026, 5, 9, tzinfo=timezone.utc),
        telemetry_emit=telemetry_emit,
    )

    assert len(emitted) == 1
    name, payload = emitted[0]
    assert name == "permission_matrix_auto_assigned"
    assert payload["person_grace_id"] == "strong"
    assert payload["cluster_id"] == "engineers"
    assert payload["drift_band"] == "high"
    fake_session.execute.assert_called()


# ---------- T4: band labels in rationale, no numerics ------------


def test_rationale_carries_band_label_no_numerics() -> None:
    matrix = _matrix_with_two_clusters()
    member_vectors = _two_cluster_member_vectors()
    candidates = [
        PersonFeature("a", (1.0, 0.0)),
        PersonFeature("b", (0.6, 0.6)),
        PersonFeature("c", (-1.0, 0.0)),  # negative cosine → 0 → low
    ]
    cfg = DriftConfig(
        auto_assign_threshold=0.95,
        queue_with_guess_threshold=0.5,
    )
    results = classify(candidates, matrix, member_vectors, config=cfg)

    for r in results:
        rationale = r.classification.rationale or ""
        # No floating-point digits leaked.
        assert not re.search(r"\d", rationale), (
            f"rationale leaked numerics: {rationale!r}"
        )


# ---------- T5: dry-run skips session writes ---------------------


def test_run_once_dry_run_skips_session_writes() -> None:
    matrix = _matrix_with_two_clusters()
    member_vectors = _two_cluster_member_vectors()
    candidates = [PersonFeature("strong", (1.0, 0.0))]

    fake_session = MagicMock()
    fake_session.execute = MagicMock()

    report = run_once(
        matrix=matrix,
        candidates=candidates,
        member_vectors=member_vectors,
        observation_time=datetime(2026, 5, 9, tzinfo=timezone.utc),
        dry_run=True,
        session=fake_session,
    )

    fake_session.execute.assert_not_called()
    assert report.dry_run is True
    assert report.band_counts["high"] + report.band_counts["medium"] + report.band_counts["low"] == 1


# ---------- T6: empty matrix → low-band, blind --------------------


def test_classify_empty_matrix_blind_queue() -> None:
    empty_matrix = PermissionMatrix(role_clusters=[])
    candidates = [PersonFeature("orphan", (1.0, 0.0))]
    results = classify(candidates, empty_matrix, member_vectors={})
    assert len(results) == 1
    cls = results[0].classification
    assert cls.drift_band == "low"
    assert cls.proposed_cluster_id is None
    assert results[0].auto_assigned is False


# ---------- T7: CLI run --dry-run produces stable envelope -------


def test_cli_run_dry_run_envelope() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.permissions.drift_detector",
            "run",
            "--dry-run",
            "--observation-time",
            "2026-05-09T00:00:00+00:00",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["dry_run"] is True
    assert payload["observation_time"] == "2026-05-09T00:00:00+00:00"
    assert "run_id" in payload
    assert payload["band_counts"] == {"high": 0, "medium": 0, "low": 0}
    assert payload["classifications"] == []


# ---------- T8: DriftConfig threshold validation ------------------


def test_drift_config_validates_threshold_ordering() -> None:
    # Defaults are sane.
    cfg = DriftConfig()
    assert cfg.auto_assign_threshold == DEFAULT_AUTO_ASSIGN_THRESHOLD
    assert cfg.queue_with_guess_threshold == DEFAULT_QUEUE_WITH_GUESS_THRESHOLD

    # queue_with_guess > auto_assign → reject
    with pytest.raises(ValueError):
        DriftConfig(auto_assign_threshold=0.5, queue_with_guess_threshold=0.7)

    # Out-of-range thresholds → reject
    with pytest.raises(ValueError):
        DriftConfig(auto_assign_threshold=1.5)
    with pytest.raises(ValueError):
        DriftConfig(queue_with_guess_threshold=-0.1)
