"""E2E integration test for privileged email extraction + governance (D519–D521).

Verifies the full chain:
1. Extraction pipeline threads sensitivity_tags to EntityCreate.
2. Cypher rewriter injects sensitivity predicates for restricted principals.
3. Post-fetch enforce drops forbidden-tagged entities.
4. Bridge propagates email tags → extraction → retrieval enforcement (AC12).
5. Edge endpoint inheritance when both endpoints carry forbidden tags (AC11).

In-memory tests avoid DB/network; bridge chain uses mocked session/pipeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.retrieval_routes import _apply_permission_post_filter
from src.graph.entity_models import EntityCreate
from src.permissions.cypher_rewriter import rewrite as _rewrite_raw
from src.permissions.enforcer import rebuild_enforcer
from src.permissions.models import (
    PermissionMatrix,
    RoleCluster,
    SensitivityTag,
)
from src.permissions.principal_context import User
from src.permissions.sensitivity_resolver import (
    D426_VOCABULARY,
    resolve_forbidden_tags,
)
from src.permissions.system_principal import SYSTEM_PRINCIPAL
from src.retrieval.retrieval_models import RankedResult, RetrievalResponse


def test_privileged_entity_create_and_rewriter_filter() -> None:
    """Full chain: entity with |privileged| tag → rewriter blocks
    restricted principal → allows full-visibility principal."""
    # Step 1: EntityCreate carries sensitivity_tags
    entity = EntityCreate(
        entity_type="Legal_Entity",
        name="Acme Corp",
        properties={},
        sensitivity_tags="|privileged|pii_dense|",
    )
    assert entity.sensitivity_tags == "|privileged|pii_dense|"

    # Step 2: Restricted principal (pii_dense only) → rewriter blocks
    restricted_matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="analyst",
                display_name="Analyst",
                sensitivity_tags=[SensitivityTag(name="pii_dense")],
            ),
        ],
    )
    principal = User()

    result = _rewrite_raw(
        "MATCH (n:Legal_Entity) RETURN n",
        principal=principal,
        allow_modules=["finance"],
        active_matrix=restricted_matrix,
    )
    # Should inject NOT CONTAINS for privileged (forbidden)
    assert "NOT (n.sensitivity_tags CONTAINS '|privileged|')" in result.query

    # Step 3: Full-visibility principal → no sensitivity filter
    full_matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="admin",
                display_name="Admin",
                sensitivity_tags=[
                    SensitivityTag(name=tag) for tag in D426_VOCABULARY
                ],
            ),
        ],
    )
    full_result = _rewrite_raw(
        "MATCH (n:Legal_Entity) RETURN n",
        principal=principal,
        allow_modules=["finance"],
        active_matrix=full_matrix,
    )
    assert "CONTAINS" not in full_result.query


def test_system_principal_bypasses_sensitivity_filter() -> None:
    """SYSTEM_PRINCIPAL with no active matrix → no sensitivity predicate."""
    result = _rewrite_raw(
        "MATCH (n:Entity) RETURN n",
        principal=SYSTEM_PRINCIPAL,
        allow_modules=["finance"],
        active_matrix=None,
    )
    assert "CONTAINS" not in result.query
    assert "ontology_module" in result.query


def test_post_fetch_filter_drops_forbidden_entity() -> None:
    """Simulated post-fetch: entity with forbidden tag is dropped."""
    from src.ingestion.communications.sensitivity_tagger import (
        tags_from_bar_form,
    )

    # Restricted principal — only pii_dense visible
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="shared",
                display_name="Shared",
                sensitivity_tags=[SensitivityTag(name="pii_dense")],
            ),
        ],
    )
    principal = User()
    forbidden = resolve_forbidden_tags(principal, matrix)

    # Simulated retrieval results
    class FakeResult:
        def __init__(self, gid: str, tags: str):
            self.grace_id = gid
            self.properties = {"sensitivity_tags": tags}

    results = [
        FakeResult("e1", "|privileged|"),
        FakeResult("e2", "|pii_dense|"),
        FakeResult("e3", ""),
        FakeResult("e4", "|external_boundary|privileged|"),
    ]

    # Apply post-fetch filter logic (mirrors retrieval_routes.py)
    surviving = []
    for r in results:
        entity_tags_str = r.properties.get("sensitivity_tags", "")
        if entity_tags_str and forbidden:
            entity_tags = set(tags_from_bar_form(entity_tags_str))
            if entity_tags & forbidden:
                continue
        surviving.append(r)

    # e1 (privileged) → dropped
    # e2 (pii_dense only) → kept
    # e3 (no tags) → kept
    # e4 (external_boundary + privileged) → dropped
    assert len(surviving) == 2
    assert [r.grace_id for r in surviving] == ["e2", "e3"]


def _shared_zone_matrix() -> PermissionMatrix:
    """Principal sees pii_dense only — privileged is forbidden."""
    return PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="shared",
                display_name="Shared",
                sensitivity_tags=[SensitivityTag(name="pii_dense")],
            ),
        ],
        default_decision="allow",
    )


def _privileged_zone_matrix() -> PermissionMatrix:
    """Principal sees all D426 tags including privileged."""
    return PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="privileged",
                display_name="Privileged",
                sensitivity_tags=[
                    SensitivityTag(name=tag) for tag in D426_VOCABULARY
                ],
            ),
        ],
        default_decision="allow",
    )


async def _endpoint_survives_post_fetch(
    grace_id: str,
    sensitivity_tags: str,
    matrix: PermissionMatrix,
) -> bool:
    """Return True when a vertex survives D521 post-fetch filter.

    F-50: the post-filter became ``async`` (graph tag fetch); this helper is
    now a coroutine. Sync callers wrap it in ``asyncio.run``.
    """
    rebuild_enforcer(matrix)
    response = RetrievalResponse(
        query="privileged entity",
        results=[
            RankedResult(
                grace_id=grace_id,
                entity_type="Legal_Entity",
                name=grace_id,
                properties={"sensitivity_tags": sensitivity_tags},
                rerank_score=1.0,
                rrf_score=1.0,
                contributing_strategies=["semantic"],
            )
        ],
        serialized_context="",
        serialization_format="template",
        total_candidates=1,
        strategy_contributions={"semantic": 1},
        latency_ms={"total": 0.0},
    )
    req = MagicMock()
    req.state = MagicMock(
        user_id=None,
        user_display_name=None,
        admin_key_present=False,
    )
    # F-50: the post-filter is now async and may fetch sensitivity_tags from
    # the graph. This helper seeds tags via ``properties`` (the union source),
    # so we stub the graph fetch to a no-op to keep the test DB/network-free.
    from unittest.mock import patch

    async def _no_graph(_ids):
        return {}

    with patch(
        "src.api.retrieval_routes._fetch_sensitivity_tags_for_ids", _no_graph
    ):
        filtered = await _apply_permission_post_filter(response, req)
    return any(r.grace_id == grace_id for r in filtered.results)


@pytest.fixture(autouse=True)
def _reset_enforcer():
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)


def test_edge_endpoint_inheritance_both_endpoints_filtered() -> None:
    """AC11: edge between two |privileged| vertices unreachable when both filtered.

    Vertex-only governance (D519): edges inherit visibility via endpoints.
    When both endpoints carry forbidden tags, neither survives post-fetch
    filtering — the connecting edge path is unreachable.
    """
    matrix = _shared_zone_matrix()
    forbidden = resolve_forbidden_tags(User(), matrix)
    assert "privileged" in forbidden

    endpoint_a_tags = "|privileged|"
    endpoint_b_tags = "|privileged|"

    import asyncio

    a_visible = asyncio.run(
        _endpoint_survives_post_fetch("vertex-a", endpoint_a_tags, matrix)
    )
    b_visible = asyncio.run(
        _endpoint_survives_post_fetch("vertex-b", endpoint_b_tags, matrix)
    )
    assert a_visible is False
    assert b_visible is False

    # Edge traversable only when BOTH endpoints visible (endpoint inheritance).
    edge_reachable = a_visible and b_visible
    assert edge_reachable is False


@pytest.mark.asyncio
async def test_privileged_email_bridge_to_retrieval_enforcement() -> None:
    """AC12: privileged email → bridge → tagged vertex → query enforcement.

    Chain: communication_events row with |privileged| → _process_email passes
    tags to extract_document → persisted vertex → shared-zone query excludes,
    privileged-principal query includes.
    """
    from src.extraction.extraction_bridge import _process_email

    captured: dict[str, str] = {}

    async def _fake_extract_document(**kwargs):
        captured["sensitivity_tags"] = kwargs.get("sensitivity_tags", "")
        batch = MagicMock()
        batch.entities = []
        return batch

    pipeline = MagicMock()
    pipeline.extract_document = AsyncMock(side_effect=_fake_extract_document)

    session = MagicMock()
    session.execute.return_value.fetchone.return_value = None

    row = {
        "message_id": "msg-ac12-test",
        "sender_email": "counsel@example.com",
        "sender_display_name": "Counsel",
        "subject": "Privileged matter",
        "sent_at": None,
        "received_at": None,
        "ingested_at": None,
        "body_plain": "Confidential discussion.",
        "sensitivity_tags": "|privileged|",
    }

    outcome = await _process_email(
        row,
        pipeline,
        session,
        arcade_client=None,
        skip_privileged=False,
    )
    assert outcome == "success"
    assert captured["sensitivity_tags"] == "|privileged|"

    tagged_vertex_tags = captured["sensitivity_tags"]

    # Shared-zone principal: privileged vertex omitted from retrieval results.
    assert (
        await _endpoint_survives_post_fetch(
            "extracted-entity",
            tagged_vertex_tags,
            _shared_zone_matrix(),
        )
        is False
    )

    # Privileged principal: vertex retained in retrieval results.
    assert (
        await _endpoint_survives_post_fetch(
            "extracted-entity",
            tagged_vertex_tags,
            _privileged_zone_matrix(),
        )
        is True
    )
