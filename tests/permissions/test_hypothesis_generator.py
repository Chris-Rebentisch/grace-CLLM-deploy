"""Tests for the hypothesis generator (Chunk 42, D333)."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any
from uuid import uuid4

import pytest

from src.permissions.evidence_collector import collect_evidence
from src.permissions.hypothesis_generator import (
    NarratedCluster,
    generate,
    narrate_cluster_default,
)
from src.permissions.models import (
    EvidenceBundle,
    EvidenceSection,
    NullHypothesis,
    RoleClusterHypothesisSet,
    SegmentedHypothesis,
)


def _make_bundle(*, person_doc_pairs: list[tuple[str, str]]) -> EvidenceBundle:
    return EvidenceBundle(
        sections=[
            EvidenceSection(
                source="document_authorship",
                rows=[{"person_grace_id": p, "document_id": d} for p, d in person_doc_pairs],
            ),
            EvidenceSection(source="segment_ownership", rows=[]),
            EvidenceSection(source="graph_person_role", rows=[]),
            EvidenceSection(source="change_directive_authorship", rows=[]),
            EvidenceSection(source="signal_combination", rows=[]),
            EvidenceSection(
                source="communications", rows=[], is_empty_placeholder=True
            ),
        ]
    )


def test_generate_includes_mandatory_null_hypothesis() -> None:
    bundle = _make_bundle(person_doc_pairs=[])
    result = generate(bundle, dry_run=True)
    nulls = [h for h in result.hypotheses if isinstance(h, NullHypothesis)]
    assert len(nulls) == 1


def test_generate_constrains_members_to_leiden_output() -> None:
    bundle = _make_bundle(
        person_doc_pairs=[("p1", "d1"), ("p2", "d1"), ("p3", "d2")]
    )

    invocations: list[list[str]] = []

    def malicious_llm(members: list[str], context: dict[str, Any]) -> NarratedCluster:
        invocations.append(members)
        return NarratedCluster(
            display_name="injected",
            description="d",
            confidence_band="strong",
            rationale=None,
        )

    result = generate(bundle, dry_run=False, llm_call=malicious_llm)
    # Members in the result come from Leiden output, not from the LLM
    # (the test name documents the constraint contract).
    all_member_ids: set[str] = set()
    for h in result.hypotheses:
        if isinstance(h, SegmentedHypothesis):
            for m in h.cluster.members:
                all_member_ids.add(m.person_grace_id)
    # Only persons that appeared as authors should appear.
    assert all_member_ids.issubset({"p1", "p2", "p3"})
    # LLM was called with the Leiden members.
    assert invocations  # at least once


def test_dry_run_is_deterministic() -> None:
    bundle = _make_bundle(person_doc_pairs=[("p1", "d1"), ("p2", "d1")])
    a = generate(bundle, dry_run=True, run_id=uuid4())
    b = generate(bundle, dry_run=True, run_id=uuid4())
    a_named = sorted(
        h.cluster.display_name
        for h in a.hypotheses
        if isinstance(h, SegmentedHypothesis)
    )
    b_named = sorted(
        h.cluster.display_name
        for h in b.hypotheses
        if isinstance(h, SegmentedHypothesis)
    )
    assert a_named == b_named


def test_confidence_band_is_a_known_literal() -> None:
    bundle = _make_bundle(
        person_doc_pairs=[("p1", "d1"), ("p2", "d1"), ("p3", "d1")]
    )
    result = generate(bundle, dry_run=True)
    for h in result.hypotheses:
        if isinstance(h, SegmentedHypothesis):
            assert h.confidence_band in ("strong", "moderate", "weak")


def test_empty_evidence_yields_only_null_hypothesis() -> None:
    bundle = _make_bundle(person_doc_pairs=[])
    result = generate(bundle, dry_run=True)
    segmented = [h for h in result.hypotheses if isinstance(h, SegmentedHypothesis)]
    nulls = [h for h in result.hypotheses if isinstance(h, NullHypothesis)]
    assert segmented == []
    assert len(nulls) == 1


def test_segment_ownership_is_consumed_for_pairs() -> None:
    bundle = EvidenceBundle(
        sections=[
            EvidenceSection(source="document_authorship", rows=[]),
            EvidenceSection(
                source="segment_ownership",
                rows=[
                    {"reviewer": "p1", "segment_name": "s1"},
                    {"reviewer": "p2", "segment_name": "s1"},
                ],
            ),
            EvidenceSection(source="graph_person_role", rows=[]),
            EvidenceSection(source="change_directive_authorship", rows=[]),
            EvidenceSection(source="signal_combination", rows=[]),
            EvidenceSection(
                source="communications", rows=[], is_empty_placeholder=True
            ),
        ]
    )
    result = generate(bundle, dry_run=True)
    seg_ids = set()
    for h in result.hypotheses:
        if isinstance(h, SegmentedHypothesis):
            for m in h.cluster.members:
                seg_ids.add(m.person_grace_id)
    assert seg_ids == {"p1", "p2"}


def test_default_narration_rationale_marks_dry_run() -> None:
    out = narrate_cluster_default(["p1", "p2"], {"structural_band": "moderate"})
    assert "dry-run" in (out.rationale or "")
    assert out.confidence_band == "moderate"


def test_run_id_appears_on_result() -> None:
    bundle = _make_bundle(person_doc_pairs=[])
    rid = uuid4()
    result = generate(bundle, dry_run=True, run_id=rid)
    assert result.run_id == rid


def test_evidence_id_threads_through() -> None:
    bundle = _make_bundle(person_doc_pairs=[])
    result = generate(bundle, dry_run=True)
    assert result.evidence_id == bundle.evidence_id


def test_cli_dry_run_produces_stable_json() -> None:
    eid = "00000000-0000-0000-0000-000000000000"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.permissions.cli",
            "hypothesis",
            "generate",
            "--evidence-id",
            eid,
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["evidence_id"] == eid
    # Mandatory null hypothesis is always present.
    assert any(h.get("kind") == "null" for h in payload["hypotheses"])


def test_cli_drift_run_dry_run_emits_no_op() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.permissions.cli",
            "drift",
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
    assert payload["result"] == "no_op_dry_run"
    assert payload["observation_time"] == "2026-05-09T00:00:00+00:00"
