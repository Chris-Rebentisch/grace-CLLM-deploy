"""Unit tests for the sensitivity report generator (Chunk 43, CP3 / D344).

Covers:

* Schema composition (tag inventory, coverage breakdown, untagged rules).
* 1000-row truncation cap on ``untagged_rules`` (smaller cap exercised
  via monkeypatch for speed).
* Scope-filter narrowing.
* Deterministic ordering of inventory + breakdown + hygiene findings.
* Below-floor carve-out (no tags anywhere → ``corpus_below_floor=True``,
  ``coverage_band=None``).
* Levenshtein-based hygiene findings.
* Three-band threshold correctness (``classify_band``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.permissions import sensitivity_report as report_engine
from src.permissions.models import (
    AccessRule,
    FrameworkMapping,
    PermissionMatrix,
    RoleCluster,
    SensitivityTag,
)


def _matrix(*, role_clusters: list[RoleCluster]) -> PermissionMatrix:
    return PermissionMatrix(role_clusters=role_clusters, default_decision="deny")


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


# ---------- classify_band -----------------------------------------------


def test_classify_band_high_at_threshold():
    assert report_engine.classify_band(0.80) == "high"
    assert report_engine.classify_band(1.0) == "high"


def test_classify_band_medium_range():
    assert report_engine.classify_band(0.79999) == "medium"
    assert report_engine.classify_band(0.50) == "medium"


def test_classify_band_low_range():
    assert report_engine.classify_band(0.499999) == "low"
    assert report_engine.classify_band(0.0) == "low"


def test_classify_band_none_passthrough():
    assert report_engine.classify_band(None) is None


# ---------- generate: schema composition --------------------------------


def test_generate_populates_inventory_breakdown_and_score():
    matrix_id = uuid4()
    cluster = RoleCluster(
        cluster_id="cl-1",
        display_name="cl-1",
        access_rules=[
            AccessRule(
                resource_kind="ontology_module",
                resource_label="finance",
                action="view",
                decision="allow",
                sensitivity_tags=[
                    SensitivityTag(
                        name="pii",
                        framework_mappings=[
                            FrameworkMapping(framework="gdpr_art_9", code="9.1"),
                        ],
                    ),
                ],
            ),
            AccessRule(
                resource_kind="ontology_module",
                resource_label="finance",
                action="edit",
                decision="allow",
            ),
        ],
    )
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=matrix_id,
        generated_at=_now(),
    )
    assert report.permission_matrix_id == matrix_id
    assert report.coverage_band == "medium"  # 1/2 = 0.5
    assert report.coverage_score == pytest.approx(0.5)
    assert report.corpus_below_floor is False
    assert len(report.tag_inventory) == 1
    assert report.tag_inventory[0].tag_name == "pii"
    assert report.tag_inventory[0].rule_count == 1
    assert report.tag_inventory[0].cluster_count == 1
    assert report.tag_inventory[0].framework_codes[0].framework == "gdpr_art_9"
    assert len(report.coverage_breakdown) == 2
    assert len(report.untagged_rules) == 1


def test_generate_rolls_up_cluster_count_across_clusters():
    cluster_a = RoleCluster(
        cluster_id="cl-a",
        display_name="A",
        access_rules=[
            AccessRule(
                resource_kind="segment",
                resource_label="legal",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="restricted")],
            )
        ],
    )
    cluster_b = RoleCluster(
        cluster_id="cl-b",
        display_name="B",
        access_rules=[
            AccessRule(
                resource_kind="segment",
                resource_label="legal",
                action="edit",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="restricted")],
            )
        ],
    )
    report = report_engine.generate(
        _matrix(role_clusters=[cluster_a, cluster_b]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    assert len(report.tag_inventory) == 1
    inv = report.tag_inventory[0]
    assert inv.tag_name == "restricted"
    assert inv.rule_count == 2
    assert inv.cluster_count == 2


def test_generate_below_floor_when_no_tags_anywhere():
    cluster = RoleCluster(
        cluster_id="cl-1",
        display_name="cl-1",
        access_rules=[
            AccessRule(
                resource_kind="ontology_module",
                resource_label="x",
                action="view",
                decision="allow",
            )
        ],
    )
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    assert report.corpus_below_floor is True
    assert report.coverage_band is None
    assert report.coverage_score is None


def test_generate_empty_matrix_is_below_floor():
    report = report_engine.generate(
        _matrix(role_clusters=[]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    assert report.corpus_below_floor is True
    assert report.coverage_band is None


def test_generate_high_coverage_band_at_or_above_80_pct():
    rules = [
        AccessRule(
            resource_kind="ontology_module",
            resource_label=f"r{i}",
            action="view",
            decision="allow",
            sensitivity_tags=[SensitivityTag(name="pii")],
        )
        for i in range(8)
    ] + [
        AccessRule(
            resource_kind="ontology_module",
            resource_label=f"u{i}",
            action="view",
            decision="allow",
        )
        for i in range(2)
    ]
    cluster = RoleCluster(cluster_id="c", display_name="c", access_rules=rules)
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    assert report.coverage_band == "high"
    assert report.coverage_score == pytest.approx(0.8)


# ---------- generate: 1000-cap truncation -------------------------------


def test_generate_truncates_untagged_rules_at_cap(monkeypatch):
    # Shrink the cap so the test stays fast.
    monkeypatch.setattr(report_engine, "UNTAGGED_RULES_CAP", 3)
    rules = [
        AccessRule(
            resource_kind="ontology_module",
            resource_label=f"r{i}",
            action="view",
            decision="allow",
        )
        for i in range(7)
    ]
    # Add one tagged rule so we don't fall into below-floor.
    rules.append(
        AccessRule(
            resource_kind="ontology_module",
            resource_label="rt",
            action="view",
            decision="allow",
            sensitivity_tags=[SensitivityTag(name="pii")],
        )
    )
    cluster = RoleCluster(cluster_id="c", display_name="c", access_rules=rules)
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    assert len(report.untagged_rules) == 3
    assert report.truncated is True


def test_generate_truncated_false_when_below_cap():
    rules = [
        AccessRule(
            resource_kind="ontology_module",
            resource_label="rt",
            action="view",
            decision="allow",
            sensitivity_tags=[SensitivityTag(name="pii")],
        ),
        AccessRule(
            resource_kind="ontology_module",
            resource_label="ru",
            action="view",
            decision="allow",
        ),
    ]
    cluster = RoleCluster(cluster_id="c", display_name="c", access_rules=rules)
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    assert report.truncated is False


# ---------- generate: scope filter --------------------------------------


def test_generate_scope_filter_excludes_non_matching_rules():
    cluster = RoleCluster(
        cluster_id="c",
        display_name="c",
        access_rules=[
            AccessRule(
                resource_kind="ontology_module",
                resource_label="finance",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="pii")],
            ),
            AccessRule(
                resource_kind="segment",
                resource_label="legal",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="restricted")],
            ),
        ],
    )
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
        scope_filter={"resource_kind": "ontology_module"},
    )
    assert {row.tag_name for row in report.tag_inventory} == {"pii"}
    assert all(
        row.resource_kind == "ontology_module"
        for row in report.coverage_breakdown
    )


# ---------- generate: deterministic ordering ---------------------------


def test_generate_inventory_sorted_by_name():
    cluster = RoleCluster(
        cluster_id="c",
        display_name="c",
        access_rules=[
            AccessRule(
                resource_kind="ontology_module",
                resource_label="r1",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="zebra")],
            ),
            AccessRule(
                resource_kind="ontology_module",
                resource_label="r2",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="alpha")],
            ),
        ],
    )
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    names = [row.tag_name for row in report.tag_inventory]
    assert names == sorted(names)


# ---------- generate: hygiene findings ---------------------------------


def test_generate_emits_hygiene_finding_for_near_duplicate_tags():
    cluster = RoleCluster(
        cluster_id="c",
        display_name="c",
        access_rules=[
            AccessRule(
                resource_kind="ontology_module",
                resource_label="r1",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="pii")],
            ),
            AccessRule(
                resource_kind="ontology_module",
                resource_label="r2",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="pii_")],
            ),
        ],
    )
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    pairs = {(f.tag_name, f.similar_to) for f in report.tag_hygiene_findings}
    assert ("pii", "pii_") in pairs


def test_generate_no_hygiene_finding_for_distant_tags():
    cluster = RoleCluster(
        cluster_id="c",
        display_name="c",
        access_rules=[
            AccessRule(
                resource_kind="ontology_module",
                resource_label="r1",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="finance_pii")],
            ),
            AccessRule(
                resource_kind="ontology_module",
                resource_label="r2",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name="hr_records")],
            ),
        ],
    )
    report = report_engine.generate(
        _matrix(role_clusters=[cluster]),
        permission_matrix_id=uuid4(),
        generated_at=_now(),
    )
    assert report.tag_hygiene_findings == []
