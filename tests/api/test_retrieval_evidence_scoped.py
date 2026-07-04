"""F-0047b / ISS-0055 Layer 2 — evidence_scoped CP5 enforcement tests.

Pure unit tests: graph fetches are monkeypatched; no services.

Covers:
- posture deny (default) remains the exact legacy behavior (regression pin
  lives in test_retrieval_post_filter.py; here we pin the deny-matrix path
  through the new posture plumbing).
- evidence_scoped serves a partial-inherited vertex with privileged props
  scrubbed from result properties AND serialized context.
- universal-tagged vertex drops in evidence_scoped.
- provenance-less tagged vertex (pre-Layer-1 / self-tagged Document_Chunk)
  drops in evidence_scoped.
- forbidden-tagged edge lines are scrubbed from serialized context.
- anonymous principal always gets deny behavior.
- enrichment failure degrades to deny (fail-safe).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.api.retrieval_routes import (
    _apply_permission_post_filter,
    _classify_evidence_scoped,
)
from src.permissions.enforcer import rebuild_enforcer
from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
    SensitivityTag,
)
from src.retrieval.retrieval_models import RankedResult, RetrievalResponse

USER_ID = uuid4()


def _matrix(posture: str = "evidence_scoped") -> PermissionMatrix:
    """Member cluster sees only external_boundary -> privileged is forbidden."""
    return PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="c1",
                display_name="C1",
                members=[RoleClusterMember(person_grace_id=str(USER_ID))],
                access_rules=[
                    AccessRule(
                        resource_kind="graph_entity",
                        resource_label="*",
                        action="view",
                        decision="allow",
                        sensitivity_tags=[SensitivityTag(name="external_boundary")],
                    )
                ],
                sensitivity_tags=[SensitivityTag(name="external_boundary")],
            )
        ],
        default_decision="allow",
        inherited_tag_posture=posture,
    )


def _request(user_id=USER_ID) -> MagicMock:
    req = MagicMock()
    req.state = MagicMock(
        user_id=str(user_id) if user_id else None,
        user_display_name=None,
        admin_key_present=False,
    )
    return req


def _result(grace_id: str, name: str, properties: dict | None = None) -> RankedResult:
    return RankedResult(
        grace_id=grace_id,
        entity_type="Person",
        name=name,
        properties=properties or {},
        rerank_score=1.0,
        rrf_score=1.0,
        contributing_strategies=["semantic"],
    )


def _response(results, context="") -> RetrievalResponse:
    return RetrievalResponse(
        query="q",
        results=results,
        serialized_context=context,
        serialization_format="template",
        total_candidates=len(results),
        strategy_contributions={"semantic": len(results)},
        latency_ms={"total": 0.0},
    )


@pytest.fixture(autouse=True)
def _reset_enforcer():
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)


def _run(response, request):
    return asyncio.run(_apply_permission_post_filter(response, request))


def _partial_record(priv_props=("secret_note",)) -> dict:
    return {
        "gid": "partial",
        "tags": "|privileged|",
        "tag_sources": json.dumps(
            {"privileged": {"ids": ["doc-1"], "overflow": 0, "count": 1}}
        ),
        "source_total": 3,
        "privileged_props": json.dumps(list(priv_props)),
    }


def _universal_record() -> dict:
    return {
        "gid": "universal",
        "tags": "|privileged|",
        "tag_sources": json.dumps(
            {"privileged": {"ids": ["doc-1", "doc-2"], "overflow": 0, "count": 2}}
        ),
        "source_total": 2,
        "privileged_props": "[]",
    }


def _patch_fetches(monkeypatch, records: dict, edges: list | None = None,
                   deny_tags: dict | None = None):
    from src.api import retrieval_routes

    async def _fake_records(ids):
        return {k: v for k, v in records.items() if k in ids}

    async def _fake_edges(ids, forbidden):
        return list(edges or [])

    async def _fake_deny_tags(ids):
        return dict(deny_tags or {})

    monkeypatch.setattr(
        retrieval_routes, "_fetch_sensitivity_records_for_ids", _fake_records
    )
    monkeypatch.setattr(
        retrieval_routes, "_fetch_forbidden_edge_pairs", _fake_edges
    )
    monkeypatch.setattr(
        retrieval_routes, "_fetch_sensitivity_tags_for_ids", _fake_deny_tags
    )


# ---------------------------------------------------------------------------
# evidence_scoped serving + scrubbing
# ---------------------------------------------------------------------------


def test_partial_inherited_vertex_served_with_props_and_context_scrubbed(
    monkeypatch,
) -> None:
    rebuild_enforcer(_matrix("evidence_scoped"))
    _patch_fetches(monkeypatch, {"partial": _partial_record()})

    context = (
        "Vivian Marsh (Person): role=matriarch\n"
        "Vivian Marsh secret_note: settlement strategy detail\n"
        "Beta LLC (Legal_Entity): jurisdiction=DE"
    )
    response = _response(
        [
            _result(
                "partial",
                "Vivian Marsh",
                {"secret_note": "settlement strategy detail", "role": "matriarch"},
            ),
            _result("clean", "Beta LLC"),
        ],
        context=context,
    )
    out = _run(response, _request())
    ids = [r.grace_id for r in out.results]
    assert ids == ["partial", "clean"], "partial-inherited vertex must be SERVED"
    served = out.results[0]
    assert "secret_note" not in served.properties, "privileged prop must be scrubbed"
    assert served.properties.get("role") == "matriarch"
    # Context: the line mentioning the entity AND the privileged prop drops;
    # the entity's clean line and other entities survive.
    assert "settlement strategy detail" not in out.serialized_context
    assert "role=matriarch" in out.serialized_context
    assert "Beta LLC" in out.serialized_context


def test_universal_tagged_vertex_dropped_in_evidence_scoped(monkeypatch) -> None:
    rebuild_enforcer(_matrix("evidence_scoped"))
    _patch_fetches(monkeypatch, {"universal": _universal_record()})

    response = _response(
        [_result("universal", "Privileged Matter"), _result("clean", "Beta LLC")]
    )
    out = _run(response, _request())
    assert [r.grace_id for r in out.results] == ["clean"]


def test_provenance_less_tagged_vertex_dropped_in_evidence_scoped(
    monkeypatch,
) -> None:
    """Pre-Layer-1 vertices and self-tagged Document_Chunk / Image_Asset rows
    carry tags but no provenance -> existence-privileged, drop (fail-safe)."""
    rebuild_enforcer(_matrix("evidence_scoped"))
    _patch_fetches(
        monkeypatch,
        {
            "chunk": {
                "gid": "chunk",
                "tags": "|privileged|",
                "tag_sources": None,
                "source_total": None,
                "privileged_props": None,
            }
        },
    )
    response = _response([_result("chunk", "Chunk 7 of privileged memo")])
    out = _run(response, _request())
    assert out.results == []


def test_missing_graph_row_with_property_tag_dropped(monkeypatch) -> None:
    rebuild_enforcer(_matrix("evidence_scoped"))
    _patch_fetches(monkeypatch, {})
    response = _response(
        [_result("ghost", "Ghost", {"sensitivity_tags": "|privileged|"})]
    )
    out = _run(response, _request())
    assert out.results == []


def test_forbidden_edge_lines_scrubbed_from_context(monkeypatch) -> None:
    rebuild_enforcer(_matrix("evidence_scoped"))
    _patch_fetches(
        monkeypatch,
        {"partial": _partial_record()},
        edges=[
            {
                "a_gid": "partial",
                "a_name": "Vivian Marsh",
                "b_gid": "clean",
                "b_name": "Beta LLC",
            }
        ],
    )
    context = (
        "Vivian Marsh (Person): role=matriarch\n"
        "Vivian Marsh -[negotiates_settlement_with]-> Beta LLC\n"
        "Beta LLC (Legal_Entity): jurisdiction=DE"
    )
    response = _response(
        [_result("partial", "Vivian Marsh"), _result("clean", "Beta LLC")],
        context=context,
    )
    out = _run(response, _request())
    assert "negotiates_settlement_with" not in out.serialized_context
    assert "role=matriarch" in out.serialized_context
    assert "jurisdiction=DE" in out.serialized_context


# ---------------------------------------------------------------------------
# deny discipline
# ---------------------------------------------------------------------------


def test_anonymous_principal_gets_deny_even_with_evidence_scoped_matrix(
    monkeypatch,
) -> None:
    rebuild_enforcer(_matrix("evidence_scoped"))
    _patch_fetches(
        monkeypatch,
        {"partial": _partial_record()},
        deny_tags={"partial": "|privileged|"},
    )
    response = _response([_result("partial", "Vivian Marsh")])
    out = _run(response, _request(user_id=None))
    assert out.results == [], "anonymous principal must get deny behavior"


def test_deny_posture_matrix_drops_partial_inherited_vertex(monkeypatch) -> None:
    """Default posture: partial-inherited provenance does NOT rescue the
    vertex — byte-identical legacy drop."""
    rebuild_enforcer(_matrix("deny"))
    _patch_fetches(
        monkeypatch,
        {"partial": _partial_record()},
        deny_tags={"partial": "|privileged|"},
    )
    response = _response([_result("partial", "Vivian Marsh")])
    out = _run(response, _request())
    assert out.results == []


def test_enrichment_failure_degrades_to_deny(monkeypatch) -> None:
    from src.api import retrieval_routes

    rebuild_enforcer(_matrix("evidence_scoped"))

    async def _boom(ids):
        raise ConnectionError("graph down")

    async def _fake_deny_tags(ids):
        return {"partial": "|privileged|"}

    monkeypatch.setattr(
        retrieval_routes, "_fetch_sensitivity_records_for_ids", _boom
    )
    monkeypatch.setattr(
        retrieval_routes, "_fetch_sensitivity_tags_for_ids", _fake_deny_tags
    )
    response = _response([_result("partial", "Vivian Marsh")])
    out = _run(response, _request())
    assert out.results == [], "evidence_scoped machinery failure must fail to deny"


# ---------------------------------------------------------------------------
# classifier unit coverage
# ---------------------------------------------------------------------------


def test_classifier_serve_when_no_forbidden_hit():
    verdict, props = _classify_evidence_scoped("", None, {"privileged"})
    assert (verdict, props) == ("serve", [])


def test_classifier_partial_returns_scrub_with_props():
    verdict, props = _classify_evidence_scoped(
        "", _partial_record(("secret_note", "strategy")), {"privileged"}
    )
    assert verdict == "scrub"
    assert set(props) == {"secret_note", "strategy"}


def test_classifier_multiple_forbidden_tags_all_must_be_partial():
    record = {
        "tags": "|pii_dense|privileged|",
        "tag_sources": json.dumps(
            {
                "privileged": {"ids": ["d1"], "overflow": 0, "count": 1},
                "pii_dense": {"ids": ["d1", "d2"], "overflow": 0, "count": 2},
            }
        ),
        "source_total": 2,
        "privileged_props": "[]",
    }
    # pii_dense is universal (2/2) even though privileged is partial -> drop.
    verdict, _ = _classify_evidence_scoped(
        "", record, {"privileged", "pii_dense"}
    )
    assert verdict == "drop"


def test_classifier_legacy_record_without_count_uses_id_math():
    record = {
        "tags": "|privileged|",
        "tag_sources": json.dumps({"privileged": {"ids": ["d1"], "overflow": 0}}),
        "source_total": 4,
        "privileged_props": "[]",
    }
    verdict, _ = _classify_evidence_scoped("", record, {"privileged"})
    assert verdict == "scrub"


def test_classifier_incoherent_provenance_drops():
    record = {
        "tags": "|privileged|",
        "tag_sources": "{corrupt",
        "source_total": 4,
        "privileged_props": "[]",
    }
    verdict, _ = _classify_evidence_scoped("", record, {"privileged"})
    assert verdict == "drop"
