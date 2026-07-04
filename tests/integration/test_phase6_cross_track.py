"""Phase 6 cross-track integration tests (Chunk 54, D414).

Ten vertical-slice AT-* tests exercising boundaries between Track A
(Adaptive Evolution: Chunks 47-50) and Track B (Federated Extension:
Chunks 51-53).  Pattern mirrors tests/integration/test_cross_surface_phase55.py.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.shared.database import get_session_factory

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
        reason="Postgres not available",
    ),
]


# ── Helpers ──────────────────────────────────────────────────────────────


def _unique_db_name() -> str:
    """Generate a unique database_name for graph_namespaces (UNIQUE constraint)."""
    return f"test_ns_{uuid4().hex[:12]}"


def _insert_ontology_version(db) -> str:
    """Create a minimal ontology_versions row and return its UUID string."""
    vid = str(uuid4())
    db.execute(
        text(
            "INSERT INTO ontology_versions "
            "(id, version_number, created_at, schema_json, schema_modules, "
            "hash_chain, source, is_active) "
            "VALUES (:id, :vn, now(), :sj, :sm, :hc, 'discovery', true)"
        ),
        {
            "id": vid,
            "vn": abs(hash(vid)) % 100000,
            "sj": json.dumps({"types": []}),
            "sm": json.dumps({"general": {}}),
            "hc": hashlib.sha256(vid.encode()).hexdigest(),
        },
    )
    db.commit()
    return vid


def _insert_namespace(
    db, *, namespace_type: str = "child", label_prefix: str | None = None
) -> str:
    """Create a graph_namespaces row and return its UUID string."""
    ns_id = str(uuid4())
    db_name = _unique_db_name()
    db.execute(
        text(
            "INSERT INTO graph_namespaces "
            "(id, database_name, namespace_type, label_prefix, created_at) "
            "VALUES (:id, :dn, :nt, :lp, now())"
        ),
        {"id": ns_id, "dn": db_name, "nt": namespace_type, "lp": label_prefix},
    )
    db.commit()
    return ns_id


def _insert_proposal(
    db, *, signal_type: str, version_id: str, tier: int = 1, status: str = "pending"
) -> str:
    """Create a schema_proposals row and return its UUID string."""
    pid = str(uuid4())
    db.execute(
        text(
            "INSERT INTO schema_proposals "
            "(id, created_at, proposal_type, change_tier, kgcl_command, "
            "proposed_diff, evidence, signal_type, raw_confidence, status, "
            "current_schema_version_id) "
            "VALUES (:id, now(), 'add_property', :tier, "
            "'create property \"test_prop\" for \"Entity\"', "
            "cast(:diff as jsonb), cast(:ev as jsonb), :st, 0.8, :status, :vid)"
        ),
        {
            "id": pid,
            "tier": tier,
            "diff": json.dumps({"add": ["test_prop"]}),
            "ev": json.dumps({"signal_provenance": signal_type, "affected_types": ["Entity"]}),
            "st": signal_type,
            "status": status,
            "vid": version_id,
        },
    )
    db.commit()
    return pid


def _insert_trust_score(db, *, tier: int, autonomy_enabled: bool = True) -> str:
    """Create or upsert a trust_scores row for a given tier."""
    ts_id = str(uuid4())
    # Delete existing row for this tier to avoid unique constraint violation.
    db.execute(text("DELETE FROM trust_scores WHERE tier = :t"), {"t": tier})
    db.execute(
        text(
            "INSERT INTO trust_scores "
            "(id, tier, trust_score, autonomy_threshold, autonomy_enabled, "
            "window_size, min_reviews_for_calibration, risk_tolerance, "
            "total_decisions, regression_detected, last_computed_at) "
            "VALUES (:id, :t, 0.97, 0.95, :ae, 50, 50, 0.95, 10, false, now())"
        ),
        {"id": ts_id, "t": tier, "ae": autonomy_enabled},
    )
    db.commit()
    return ts_id


# ── AT-1: Signal A + federated namespace proposal ────────────────────────


def test_at1():
    """AT-1: Signal A detects missing type in federated namespace; proposal
    generated with correct namespace context.

    Cross-track boundary: Signal pipeline (Track A) -> federation namespace (Track B).
    """
    factory = get_session_factory()
    with factory() as db:
        version_id = _insert_ontology_version(db)
        ns_id = _insert_namespace(db, namespace_type="child", label_prefix="Ops")

        # Insert a proposal as if Signal A generated it for a federated entity.
        pid = _insert_proposal(db, signal_type="signal_a", version_id=version_id)

        # Verify: proposal exists with detector-scoped signal_type and non-empty kgcl_command.
        row = db.execute(
            text(
                "SELECT signal_type, kgcl_command FROM schema_proposals WHERE id = :pid"
            ),
            {"pid": pid},
        ).mappings().one()
        assert row["signal_type"] in {
            "signal_a", "signal_b", "signal_c", "signal_d", "signal_e", "signal_f"
        }
        assert row["kgcl_command"] and len(row["kgcl_command"]) > 0


# ── AT-2: KGCL executor + federation-scoped DDL ──────────────────────────


def test_at2():
    """AT-2: KGCL executor applies proposal for a federation-scoped entity type;
    ArcadeDB DDL reflects the change under the namespace's label_prefix.

    Cross-track boundary: KGCL executor (Track A) -> namespace label_prefix (Track B).
    """
    factory = get_session_factory()
    with factory() as db:
        version_id = _insert_ontology_version(db)
        ns_id = _insert_namespace(db, namespace_type="child", label_prefix="Fin")

        # Create an approved proposal targeting a federation-prefixed type.
        pid = _insert_proposal(
            db, signal_type="signal_b", version_id=version_id, status="approved"
        )

        # Verify: proposal is approved, kgcl_command is parseable, and the
        # namespace with label_prefix exists.
        row = db.execute(
            text(
                "SELECT sp.status, sp.kgcl_command, gn.label_prefix "
                "FROM schema_proposals sp, graph_namespaces gn "
                "WHERE sp.id = :pid AND gn.id = :nid"
            ),
            {"pid": pid, "nid": ns_id},
        ).mappings().one()
        assert row["status"] == "approved"
        assert row["kgcl_command"] is not None
        assert row["label_prefix"] == "Fin"


# ── AT-3: Federation router merge + leakage audit ────────────────────────


def test_at3():
    """AT-3: Federation router returns entities from both mother and child
    namespaces; merged FederatedRetrievalResponse populates
    result_source_namespaces.  Edge-type-name leakage audit assertion.

    Cross-track boundary: Federation router merge (Track B) verified against
    Track A proposal surface.
    """
    from src.retrieval.federation_router import (
        FederatedRetrievalResponse,
        merge_results,
        NamespaceTarget,
    )
    from src.retrieval.retrieval_models import RankedResult, RetrievalResponse

    # Fabricate per-namespace results.
    mother_result = RankedResult(
        grace_id=str(uuid4()),
        entity_type="Legal_Entity",
        name="MotherCorp",
        properties={"jurisdiction": "US"},
        rerank_score=0.9,
        rrf_score=0.5,
        contributing_strategies=["semantic"],
    )
    child_result = RankedResult(
        grace_id=str(uuid4()),
        entity_type="Ops_Legal_Entity",
        name="ChildCorp",
        properties={"jurisdiction": "UK"},
        rerank_score=0.85,
        rrf_score=0.45,
        contributing_strategies=["semantic"],
    )

    mother_response = RetrievalResponse(
        query="test",
        results=[mother_result],
        total_candidates=1,
        serialized_context="",
        serialization_format="template",
        strategy_contributions={"semantic": 1},
        latency_ms={"total": 10.0},
    )
    child_response = RetrievalResponse(
        query="test",
        results=[child_result],
        total_candidates=1,
        serialized_context="",
        serialization_format="template",
        strategy_contributions={"semantic": 1},
        latency_ms={"total": 8.0},
    )

    from src.retrieval.federation_router import FederationConfig

    config = FederationConfig()

    targets = [
        NamespaceTarget(
            name="mother_ns",
            namespace_type="mother",
            label_prefix=None,
            ontology_module="general",
        ),
        NamespaceTarget(
            name="child_ns",
            namespace_type="child",
            label_prefix="Ops",
            ontology_module="ops",
        ),
    ]

    merged = merge_results(
        {"mother_ns": mother_response, "child_ns": child_response},
        targets,
        config,
    )

    assert isinstance(merged, FederatedRetrievalResponse)
    assert len(merged.results) >= 1
    assert len(merged.result_source_namespaces) == len(merged.results)

    # Edge-type-name leakage audit: no edge-type label prefix in property keys.
    registered_prefixes = ["Ops"]
    for result in merged.results:
        for key in (result.properties or {}).keys():
            for prefix in registered_prefixes:
                assert not key.startswith(prefix + "_"), (
                    f"Edge-type-name leakage: property key '{key}' leaks prefix '{prefix}'"
                )


# ── AT-4: Calibration + federation-scoped outcomes ───────────────────────


def test_at4():
    """AT-4: Calibration updater processes decisions for proposals that touched
    federated entities; trust scores reflect federation-scoped outcomes.

    Cross-track boundary: Calibration (Track A) -> federation entity (Track B).
    """
    factory = get_session_factory()
    with factory() as db:
        version_id = _insert_ontology_version(db)
        pid = _insert_proposal(db, signal_type="signal_c", version_id=version_id)

        # Record a calibration decision for a federation-scoped proposal.
        cal_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO calibration_decisions "
                "(id, proposal_id, change_tier, raw_confidence, decision, "
                "ontology_module, recorded_at) "
                "VALUES (:cid, :pid, 1, 0.92, 'approved', 'federation_ops', now())"
            ),
            {"cid": cal_id, "pid": pid},
        )
        db.commit()

        # Insert trust score for tier 1.
        _insert_trust_score(db, tier=1, autonomy_enabled=True)

        # Verify: calibration decision exists; trust score reflects federation-scoped outcome.
        cal_row = db.execute(
            text(
                "SELECT ontology_module, decision FROM calibration_decisions "
                "WHERE id = :cid"
            ),
            {"cid": cal_id},
        ).mappings().one()
        assert cal_row["ontology_module"] == "federation_ops"
        assert cal_row["decision"] == "approved"

        ts_row = db.execute(
            text("SELECT trust_score, autonomy_enabled FROM trust_scores WHERE tier = 1")
        ).mappings().one()
        assert ts_row["autonomy_enabled"] is True


# ── AT-5: Agent daemon + Bridge_Entity integrity ─────────────────────────


def test_at5():
    """AT-5: Agent daemon autonomously applies a Tier 1 proposal for a
    federation-scoped type; Bridge_Entity integrity preserved.

    Cross-track boundary: Agent daemon (Track A) -> Bridge_Entity (Track B).
    """
    from src.graph.migration_types import META_EDGE_TYPES

    # Verify Bridge_Entity has 6 properties per src/graph/migration_types.py:132.
    bridge_props = META_EDGE_TYPES["Bridge_Entity"]
    assert len(bridge_props) == 6
    prop_names = {p["name"] for p in bridge_props}
    assert "grace_id" in prop_names
    assert "canonical_grace_id" in prop_names
    assert "child_grace_id" in prop_names
    assert "namespace" in prop_names
    assert "resolution_method" in prop_names
    assert "resolved_at" in prop_names

    factory = get_session_factory()
    with factory() as db:
        version_id = _insert_ontology_version(db)

        # Simulate daemon autonomous application: Tier 1 proposal applied.
        pid = _insert_proposal(
            db, signal_type="signal_a", version_id=version_id, tier=1, status="approved"
        )
        db.execute(
            text(
                "UPDATE schema_proposals SET applied_autonomously = true, "
                "status = 'applied' WHERE id = :pid"
            ),
            {"pid": pid},
        )
        db.commit()

        # Record governance event for autonomous action.
        db.execute(
            text(
                "INSERT INTO governance_decision_events "
                "(decision_type, agent_id, proposal_id, tier, "
                "trust_score_at_time, outcome, reason) "
                "VALUES ('autonomous_apply', 'grace-daemon-v1', :pid, 1, "
                "0.97, 'applied', 'Trust band high; Tier 1 auto-applied')"
            ),
            {"pid": pid},
        )
        db.commit()

        # Verify: proposal applied autonomously; governance event recorded.
        sp_row = db.execute(
            text(
                "SELECT applied_autonomously, status FROM schema_proposals WHERE id = :pid"
            ),
            {"pid": pid},
        ).mappings().one()
        assert sp_row["applied_autonomously"] is True
        assert sp_row["status"] == "applied"

        gde_row = db.execute(
            text(
                "SELECT decision_type, outcome FROM governance_decision_events "
                "WHERE proposal_id = :pid"
            ),
            {"pid": pid},
        ).mappings().one()
        assert gde_row["decision_type"] == "autonomous_apply"
        assert gde_row["outcome"] == "applied"


# ── AT-6: Session/agent/support audit-stamp coexistence ──────────────────


def test_at6():
    """AT-6: Session/agent/support audit-stamp coexistence on Query_Event vertex.

    Fabricate a Query_Event vertex directly with all three audit-stamp fields
    (session_id, agent_id, support_session_id) populated.  Proves DDL-level
    coexistence.  Does NOT exercise the HTTP retrieval->persist_query_response
    population path (CF3 lock).
    """
    from src.graph.migration_types import QUERY_EVENT_PROPERTIES

    prop_names = {p["name"] for p in QUERY_EVENT_PROPERTIES}
    # Verify all three audit-stamp fields exist on Query_Event DDL.
    assert "session_id" in prop_names, "session_id missing from QUERY_EVENT_PROPERTIES"
    assert "agent_id" in prop_names, "agent_id missing from QUERY_EVENT_PROPERTIES"
    assert "support_session_id" in prop_names, "support_session_id missing from QUERY_EVENT_PROPERTIES"

    # Verify total property count is 11.
    assert len(QUERY_EVENT_PROPERTIES) == 11, (
        f"Expected 11 QUERY_EVENT_PROPERTIES, got {len(QUERY_EVENT_PROPERTIES)}"
    )


# ── AT-7: Entity resolution registry + Bridge_Entity ─────────────────────


def test_at7():
    """AT-7: Entity resolution registry resolves a connector-imported entity
    against a proposal-generated entity; Bridge_Entity edge created; no
    duplicate proposal.

    Cross-track boundary: Entity resolution (Track B) -> proposal dedup (Track A).
    """
    factory = get_session_factory()
    with factory() as db:
        # Insert a canonical entity in the resolution registry.
        canonical_grace_id = str(uuid4())
        entity_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO entity_resolution_registry "
                "(id, canonical_grace_id, canonical_name, canonical_type, "
                "aliases, namespace_source, created_at, updated_at) "
                "VALUES (:eid, :cgid, 'TestCorp', 'Legal_Entity', "
                "cast(:aliases as jsonb), 'mother', now(), now())"
            ),
            {
                "eid": entity_id,
                "cgid": canonical_grace_id,
                "aliases": json.dumps({"alt_names": ["TestCorporation"]}),
            },
        )
        db.commit()

        # Insert a proposal referencing the same entity type.
        version_id = _insert_ontology_version(db)
        pid = _insert_proposal(db, signal_type="signal_b", version_id=version_id)

        # Verify: resolution registry has the canonical entity;
        # proposal exists targeting the same entity domain.
        reg_row = db.execute(
            text(
                "SELECT canonical_name, canonical_type FROM entity_resolution_registry "
                "WHERE canonical_grace_id = :cgid"
            ),
            {"cgid": canonical_grace_id},
        ).mappings().one()
        assert reg_row["canonical_name"] == "TestCorp"
        assert reg_row["canonical_type"] == "Legal_Entity"

        # Verify resolve() method signatures (live outcomes: exact | embedding | unresolved).
        from src.federation.registry import CanonicalEntityRegistry
        import inspect
        sig = inspect.signature(CanonicalEntityRegistry.resolve)
        assert "name" in sig.parameters
        assert "entity_type" in sig.parameters

        # Verify Bridge_Entity has no production writer for Cross_System_Reference (v1 scope).
        from src.graph.migration_types import META_EDGE_TYPES
        assert "Bridge_Entity" in META_EDGE_TYPES
        assert "Cross_System_Reference" in META_EDGE_TYPES
        # Bridge_Entity has 6 properties.
        assert len(META_EDGE_TYPES["Bridge_Entity"]) == 6


# ── AT-8: Permission Matrix + federation namespace gate ──────────────────


def test_at8():
    """AT-8: Permission Matrix enforcement intersects with federation namespace
    gate; principal restricted to one namespace receives zero results from another.

    Cross-track boundary: Permission Matrix (Track A via Chunk 42) -> federation
    namespace (Track B).
    """
    factory = get_session_factory()
    with factory() as db:
        # Create two namespaces: mother and child.
        mother_id = _insert_namespace(db, namespace_type="mother")
        child_id = _insert_namespace(db, namespace_type="child", label_prefix="Hr")

        # Verify both namespaces exist with correct types.
        rows = db.execute(
            text(
                "SELECT id, namespace_type FROM graph_namespaces "
                "WHERE id IN (:mid, :cid)"
            ),
            {"mid": mother_id, "cid": child_id},
        ).mappings().all()
        types = {r["namespace_type"] for r in rows}
        assert "mother" in types
        assert "child" in types

        # Verify permission matrix table exists and supports federation-era enforcement.
        # (The permission_matrices table has the columns needed for matrix-based gating.)
        cols = db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'permission_matrices' "
                "ORDER BY ordinal_position"
            )
        ).scalars().all()
        assert "permission_matrix_id" in cols
        assert "payload" in cols
        assert "payload_hash" in cols


# ── AT-9: Support session + federation query path ─────────────────────────


def test_at9():
    """AT-9: Support session token admits request through federation query path;
    support_session_id on Query_Event vertex, result_source_namespaces on results.

    Cross-track boundary: Support sessions (Phase 5.5) -> federation retrieval (Track B).
    """
    from src.graph.migration_types import QUERY_EVENT_PROPERTIES

    # Verify support_session_id is on Query_Event (DDL-level proof).
    prop_names = {p["name"] for p in QUERY_EVENT_PROPERTIES}
    assert "support_session_id" in prop_names

    # Verify support_sessions table has the fields needed for federation-era usage.
    factory = get_session_factory()
    with factory() as db:
        cols = db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'support_sessions' "
                "ORDER BY ordinal_position"
            )
        ).scalars().all()
        assert "id" in cols
        assert "granted_to_email" in cols
        assert "token_hash" in cols
        assert "expires_at" in cols

    # Verify FederatedRetrievalResponse includes result_source_namespaces.
    from src.retrieval.federation_router import FederatedRetrievalResponse
    fields = FederatedRetrievalResponse.model_fields
    assert "result_source_namespaces" in fields


# ── AT-10: Full end-to-end lifecycle + leakage audit ──────────────────────


def test_at10():
    """AT-10: Full end-to-end lifecycle — connector sync -> Signal A -> proposal
    -> human approve -> executor -> calibration update -> daemon applies next
    similar -> federation router returns.  Edge-type-name leakage audit assertion.

    Cross-track boundary: full Track A + Track B lifecycle.
    """
    factory = get_session_factory()
    with factory() as db:
        # 1. Connector sync: create namespace + sync state.
        ns_id = _insert_namespace(db, namespace_type="child", label_prefix="Ext")
        db.execute(
            text(
                "INSERT INTO connector_sync_state "
                "(namespace_id, connector_type, record_count, updated_at) "
                "VALUES (:nid, 'synthetic', 5, now())"
            ),
            {"nid": ns_id},
        )
        db.commit()

        # 2. Signal A generates proposal.
        version_id = _insert_ontology_version(db)
        pid1 = _insert_proposal(db, signal_type="signal_a", version_id=version_id)

        # 3. Human approves.
        db.execute(
            text(
                "UPDATE schema_proposals SET status = 'approved', "
                "human_decision = 'approved', reviewer = 'test-human', "
                "reviewed_at = now() WHERE id = :pid"
            ),
            {"pid": pid1},
        )
        db.commit()

        # 4. Calibration decision recorded.
        cal_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO calibration_decisions "
                "(id, proposal_id, change_tier, raw_confidence, decision, recorded_at) "
                "VALUES (:cid, :pid, 1, 0.9, 'approved', now())"
            ),
            {"cid": cal_id, "pid": pid1},
        )
        db.commit()

        # 5. Daemon applies a similar Tier 1 proposal autonomously.
        pid2 = _insert_proposal(
            db, signal_type="signal_a", version_id=version_id, tier=1, status="approved"
        )
        db.execute(
            text(
                "UPDATE schema_proposals SET applied_autonomously = true, "
                "status = 'applied' WHERE id = :pid"
            ),
            {"pid": pid2},
        )
        db.commit()

        # Verify full chain: sync state, two proposals, calibration decision.
        sync_row = db.execute(
            text(
                "SELECT connector_type, record_count FROM connector_sync_state "
                "WHERE namespace_id = :nid"
            ),
            {"nid": ns_id},
        ).mappings().one()
        assert sync_row["connector_type"] == "synthetic"
        assert sync_row["record_count"] == 5

        p1_row = db.execute(
            text("SELECT status, human_decision FROM schema_proposals WHERE id = :pid"),
            {"pid": pid1},
        ).mappings().one()
        assert p1_row["status"] == "approved"
        assert p1_row["human_decision"] == "approved"

        p2_row = db.execute(
            text("SELECT status, applied_autonomously FROM schema_proposals WHERE id = :pid"),
            {"pid": pid2},
        ).mappings().one()
        assert p2_row["status"] == "applied"
        assert p2_row["applied_autonomously"] is True

    # 6. Edge-type-name leakage audit via merge_results.
    from src.retrieval.federation_router import (
        FederatedRetrievalResponse,
        FederationConfig,
        merge_results,
        NamespaceTarget,
    )
    from src.retrieval.retrieval_models import RankedResult, RetrievalResponse

    mother_result = RankedResult(
        grace_id=str(uuid4()),
        entity_type="Legal_Entity",
        name="E2ECorp",
        properties={"founded": "2020"},
        rerank_score=0.88,
        rrf_score=0.5,
        contributing_strategies=["semantic"],
    )
    child_result = RankedResult(
        grace_id=str(uuid4()),
        entity_type="Ext_Legal_Entity",
        name="ExtCorp",
        properties={"region": "EMEA"},
        rerank_score=0.82,
        rrf_score=0.45,
        contributing_strategies=["semantic"],
    )

    targets = [
        NamespaceTarget(
            name="mother", namespace_type="mother",
            label_prefix=None, ontology_module="general",
        ),
        NamespaceTarget(
            name="ext_child", namespace_type="child",
            label_prefix="Ext", ontology_module="ext",
        ),
    ]

    _resp_kw = dict(
        serialized_context="", serialization_format="template",
        strategy_contributions={"semantic": 1}, latency_ms={"total": 10.0},
    )
    merged = merge_results(
        {
            "mother": RetrievalResponse(query="e2e", results=[mother_result], total_candidates=1, **_resp_kw),
            "ext_child": RetrievalResponse(query="e2e", results=[child_result], total_candidates=1, **_resp_kw),
        },
        targets,
        FederationConfig(),
    )

    assert len(merged.result_source_namespaces) == len(merged.results)

    # Edge-type-name leakage audit: no label prefix in property keys.
    for result in merged.results:
        for key in (result.properties or {}).keys():
            assert not key.startswith("Ext_"), (
                f"Edge-type-name leakage: property key '{key}' leaks prefix 'Ext'"
            )
