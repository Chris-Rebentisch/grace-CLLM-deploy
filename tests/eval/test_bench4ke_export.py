"""Bench4KE export tests (Chunk 34, D261)."""

from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.discovery.cq_models import (
    CompetencyQuestion,
    CQPriority,
    CQSource,
    CQStatus,
    CQType,
    CQVerificationStatus,
)
from src.eval import bench4ke_export


def _cq(
    *,
    canonical_text: str,
    verification_status: CQVerificationStatus,
    cq_type: CQType = CQType.SCOPING,
) -> CompetencyQuestion:
    return CompetencyQuestion(
        id=uuid4(),
        canonical_text=canonical_text,
        raw_user_input=None,
        cq_type=cq_type,
        domain="legal",
        priority=CQPriority.UNSET,
        source=CQSource.SYSTEM_GENERATED,
        source_pass=None,
        template_id=None,
        status=CQStatus.DRAFT,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        version=1,
        previous_text=None,
        generation_confidence=0.0,
        verification_confidence=0.0,
        verification_status=verification_status,
        verification_path=None,
        verification_gap=None,
        linked_document_ids=[],
        cluster_id=None,
        metadata_extra={"ontology_project": "grace", "ontology_iri": "http://x/y"},
        embedding_cq_type=CQType.UNCLASSIFIED.value,
        embedding_cq_type_confidence=0.0,
        rule_cq_type=CQType.UNCLASSIFIED.value,
        type_agreement=False,
    )


def test_round_trip_via_string_default_filter():
    """Default filter excludes anything outside PASS / HUMAN_CONFIRMED."""
    cqs = [
        _cq(canonical_text="kept-pass", verification_status=CQVerificationStatus.PASS),
        _cq(
            canonical_text="kept-confirmed",
            verification_status=CQVerificationStatus.HUMAN_CONFIRMED,
        ),
        _cq(canonical_text="dropped", verification_status=CQVerificationStatus.UNTESTED),
    ]
    with patch("src.eval.bench4ke_export.list_cqs", return_value=cqs):
        csv_text = bench4ke_export.export_to_string(db_session=MagicMock())

    rows = list(csv.DictReader(StringIO(csv_text)))
    assert len(rows) == 2
    assert {r["cq_text"] for r in rows} == {"kept-pass", "kept-confirmed"}
    # Schema columns pinned per Bench4KE upstream.
    assert set(rows[0].keys()) == {
        "cq_id",
        "ontology_project",
        "ontology_iri",
        "cq_text",
        "expected_answer_type",
    }


def test_include_unverified_flag_flips_filter():
    cqs = [
        _cq(canonical_text="kept", verification_status=CQVerificationStatus.PASS),
        _cq(canonical_text="now-included", verification_status=CQVerificationStatus.UNTESTED),
    ]
    with patch("src.eval.bench4ke_export.list_cqs", return_value=cqs):
        csv_text = bench4ke_export.export_to_string(
            include_unverified=True, db_session=MagicMock()
        )
    rows = list(csv.DictReader(StringIO(csv_text)))
    assert len(rows) == 2
    assert {r["cq_text"] for r in rows} == {"kept", "now-included"}
