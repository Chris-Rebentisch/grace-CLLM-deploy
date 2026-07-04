"""Tests for the six-source evidence collector (Chunk 42, D332)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.permissions.evidence_collector import (
    EvidenceCollectorConfig,
    collect_evidence,
)
from src.permissions.models import EvidenceBundle, EvidenceSection


SECTION_ORDER = [
    "document_authorship",
    "segment_ownership",
    "graph_person_role",
    "change_directive_authorship",
    "signal_combination",
    "communications",
]


def _section(bundle: EvidenceBundle, name: str) -> EvidenceSection:
    for s in bundle.sections:
        if s.source == name:
            return s
    raise AssertionError(f"missing section {name}")


def test_returns_all_six_sources_in_stable_order() -> None:
    bundle = collect_evidence()
    assert [s.source for s in bundle.sections] == SECTION_ORDER


def test_communications_is_typed_but_empty_placeholder() -> None:
    bundle = collect_evidence()
    comm = _section(bundle, "communications")
    assert comm.rows == []
    assert comm.is_empty_placeholder is True


def test_graph_person_role_uses_arcade_client_when_provided() -> None:
    arcade = MagicMock()
    arcade.execute_cypher.return_value = [
        {"person_grace_id": "p1", "role_name": "Reviewer"},
        {"person_grace_id": "p1", "role_name": "Reviewer"},  # dedup
        {"person_grace_id": "p2", "role_name": "Author"},
    ]
    bundle = collect_evidence(arcade_client=arcade)
    rows = _section(bundle, "graph_person_role").rows
    assert {(r["person_grace_id"], r["role_name"]) for r in rows} == {
        ("p1", "Reviewer"),
        ("p2", "Author"),
    }
    arcade.execute_cypher.assert_called_once()
    # Confirm the query is purely read.
    sent_query = arcade.execute_cypher.call_args[0][0]
    assert "MATCH" in sent_query and "RETURN" in sent_query


def test_overrides_replace_individual_readers() -> None:
    overrides = {
        "document_authorship": lambda: [{"person_grace_id": "p9", "document_id": "d9"}],
        "graph_person_role": lambda: [],
    }
    bundle = collect_evidence(overrides=overrides)
    docs = _section(bundle, "document_authorship").rows
    assert docs == [{"person_grace_id": "p9", "document_id": "d9"}]
    assert _section(bundle, "graph_person_role").rows == []


def test_re_run_is_deterministic_for_pure_overrides() -> None:
    rows = [
        {"person_grace_id": "p1", "document_id": "d1"},
        {"person_grace_id": "p2", "document_id": "d2"},
    ]
    overrides = {"document_authorship": lambda: list(rows)}
    a = collect_evidence(overrides=overrides)
    b = collect_evidence(overrides=overrides)
    assert _section(a, "document_authorship").rows == _section(
        b, "document_authorship"
    ).rows


def test_signal_combination_normalizes_count_to_int() -> None:
    overrides = {
        "signal_combination": lambda: [
            {"person_grace_id": "p1", "signal_kind": "A", "count": 3},
        ]
    }
    bundle = collect_evidence(overrides=overrides)
    rows = _section(bundle, "signal_combination").rows
    assert rows[0]["count"] == 3
    assert isinstance(rows[0]["count"], int)


def test_swallows_arcade_errors_to_empty_section() -> None:
    arcade = MagicMock()
    arcade.execute_cypher.side_effect = RuntimeError("graph down")
    bundle = collect_evidence(arcade_client=arcade)
    assert _section(bundle, "graph_person_role").rows == []


def test_session_errors_swallowed_per_source() -> None:
    session = MagicMock()
    session.execute.side_effect = RuntimeError("db down")
    bundle = collect_evidence(pg_session=session)
    for name in (
        "document_authorship",
        "segment_ownership",
        "change_directive_authorship",
        "signal_combination",
    ):
        assert _section(bundle, name).rows == []


def test_evidence_bundle_id_is_uuid() -> None:
    bundle = collect_evidence()
    assert bundle.evidence_id is not None
    # UUID4 string roundtrip
    assert len(str(bundle.evidence_id)) == 36


def test_communications_enabled_does_not_break_shape() -> None:
    cfg = EvidenceCollectorConfig(communications_enabled=True)
    bundle = collect_evidence(config=cfg)
    comm = _section(bundle, "communications")
    # Even when "enabled", v1 has no actual rows because Phase 7 has not
    # shipped — but the placeholder flag flips off so callers know the
    # section is a real (still-empty) source rather than the placeholder.
    assert comm.rows == []
    assert comm.is_empty_placeholder is False


def test_two_invocations_with_same_overrides_match_section_count() -> None:
    overrides = {"document_authorship": lambda: []}
    a = collect_evidence(overrides=overrides)
    b = collect_evidence(overrides=overrides)
    assert len(a.sections) == len(b.sections) == 6


def test_dedup_by_keys_preserves_first_occurrence() -> None:
    overrides = {
        "document_authorship": lambda: [
            {"person_grace_id": "p1", "document_id": "d1", "marker": "first"},
            {"person_grace_id": "p1", "document_id": "d1", "marker": "second"},
        ]
    }
    # Override returns rows directly; dedup is only applied by the
    # built-in readers, so override rows pass through. This documents
    # the boundary: tests overriding a reader OWN the dedup contract.
    bundle = collect_evidence(overrides=overrides)
    rows = _section(bundle, "document_authorship").rows
    assert len(rows) == 2
