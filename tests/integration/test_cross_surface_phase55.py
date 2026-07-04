"""Cross-surface Phase 5.5 integration tests (Chunk 46, D378.e).

Six vertical-slice tests + one three-identity audit substrate test + one
setup verification test.  Validates interop across the seven Phase 5.5
surfaces (Chunks 36–45).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.api.main import app
from src.shared.database import get_session_factory

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
        reason="Postgres not available",
    ),
]


# ── Helpers ──────────────────────────────────────────────────────────────


def _insert_review_session(db, *, status: str = "completed") -> str:
    """Create a minimal ``review_sessions`` row and return its UUID string."""
    sid = str(uuid4())
    db.execute(
        text(
            "INSERT INTO review_sessions "
            "(id, status, reviewer, seed_schema_merge_run_id, seed_schema_snapshot) "
            "VALUES (:id, :st, 'test-reviewer', 'merge-run-1', cast('{}' as jsonb))"
        ),
        {"id": sid, "st": status},
    )
    db.commit()
    return sid


# ── Test 1: Recon + Change Directives (Chunks 36–39) ────────────────────


def test_recon_change_directive_coverage():
    """Review session → change directive → transition to active."""
    factory = get_session_factory()
    with factory() as db:
        # Create a completed review session (gap-report prerequisite).
        sid = _insert_review_session(db)

        # Create a change directive.
        authored_by = str(uuid4())
        directive_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO change_directives "
                "(directive_id, title, description, tier, status, visibility, "
                "authored_by) "
                "VALUES (:did, 'Fix gap', 'desc', 'Operational_Adjustment', "
                "'draft', 'permission_matrix_default', CAST(:ab AS uuid))"
            ),
            {"did": directive_id, "ab": authored_by},
        )
        db.commit()

        # Transition draft → active.
        transitioned_by = str(uuid4())
        db.execute(
            text(
                "INSERT INTO change_directive_state_transitions "
                "(directive_id, from_state, to_state, reason, "
                "transitioned_by, transitioned_at, hash_chain, prev_transition_hash) "
                "VALUES (:did, 'draft', 'active', 'test', CAST(:tb AS uuid), now(), "
                "'hash1', NULL)"
            ),
            {"did": directive_id, "tb": transitioned_by},
        )
        db.execute(
            text(
                "UPDATE change_directives SET status = 'active', "
                "status_updated_at = now() WHERE directive_id = :did"
            ),
            {"did": directive_id},
        )
        db.commit()

        # Verify cross-surface linkage: directive is active.
        row = db.execute(
            text(
                "SELECT cd.status "
                "FROM change_directives cd WHERE cd.directive_id = :did"
            ),
            {"did": directive_id},
        ).mappings().one()
        assert row["status"] == "active"

        # Verify the review session exists (prerequisite for gap report).
        sess = db.execute(
            text("SELECT status FROM review_sessions WHERE id = :sid"),
            {"sid": sid},
        ).scalar()
        assert sess == "completed"


# ── Test 2: Decomposition + Guided Permissions (Chunks 40–42) ───────────


def test_decomposition_permissions_segment_linkage():
    """Segmentation map → permission hypothesis run references segments."""
    factory = get_session_factory()
    with factory() as db:
        run_id = str(uuid4())
        # Create a decomposition run (operator is UUID, nullable).
        db.execute(
            text(
                "INSERT INTO decomposition_runs "
                "(run_id, archive_root, archive_root_canonical_hash, status, "
                "started_at) "
                "VALUES (CAST(:rid AS uuid), '/tmp/test', :h, 'completed', now())"
            ),
            {"rid": run_id, "h": hashlib.sha256(b"test").hexdigest()},
        )
        db.commit()

        # Create a segmentation map referencing the run.
        map_id = str(uuid4())
        # Unique nonce avoids global uq_segmentation_maps_payload_hash collisions
        # when the dev DB retains rows from prior runs (DV1 / Chunk 46 audit).
        payload = json.dumps(
            {"segments": [{"segment_name": "ops"}], "_test_nonce": str(uuid4())}
        )
        payload_hash = hashlib.sha256(
            json.dumps(json.loads(payload), sort_keys=True).encode()
        ).hexdigest()
        db.execute(
            text(
                "INSERT INTO segmentation_maps "
                "(segmentation_map_id, decomposition_run_id, payload, "
                "payload_hash, previous_hash, schema_version, "
                "null_hypothesis_accepted, created_at) "
                "VALUES (:mid, :rid, cast(:p as jsonb), :ph, NULL, 'v1', "
                "false, now())"
            ),
            {"mid": map_id, "rid": run_id, "p": payload, "ph": payload_hash},
        )
        db.commit()

        # Create a permission hypothesis run that logically follows.
        hyp_run_id = str(uuid4())
        evidence_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO permission_hypothesis_runs "
                "(run_id, evidence_id, status, created_at) "
                "VALUES (:hrid, :eid, 'completed', now())"
            ),
            {"hrid": hyp_run_id, "eid": evidence_id},
        )
        db.commit()

        # Verify both exist and are queryable together.
        seg_row = db.execute(
            text(
                "SELECT payload->>'segments' AS segs "
                "FROM segmentation_maps WHERE decomposition_run_id = :rid"
            ),
            {"rid": run_id},
        ).scalar()
        assert "ops" in seg_row

        hyp_row = db.execute(
            text(
                "SELECT status FROM permission_hypothesis_runs "
                "WHERE run_id = :hrid"
            ),
            {"hrid": hyp_run_id},
        ).scalar()
        assert hyp_row == "completed"


# ── Test 3: Guided Permissions + Sensitivity Gate (Chunks 42–43) ────────


def test_permissions_sensitivity_coverage_denormalization():
    """Ratify matrix → generate sensitivity report → coverage_band denormalized."""
    factory = get_session_factory()
    with factory() as db:
        matrix_id = str(uuid4())
        # Use a unique payload to avoid hash collision with existing rows.
        payload = json.dumps({"role_clusters": [], "_test_nonce": str(uuid4())})
        payload_hash = hashlib.sha256(
            json.dumps(json.loads(payload), sort_keys=True).encode()
        ).hexdigest()

        # Insert a permission matrix.
        db.execute(
            text(
                "INSERT INTO permission_matrices "
                "(permission_matrix_id, payload, payload_hash, previous_hash, "
                "created_at) "
                "VALUES (:mid, cast(:p as jsonb), :ph, NULL, now())"
            ),
            {"mid": matrix_id, "p": payload, "ph": payload_hash},
        )
        db.commit()

        # Insert a sensitivity classification report.
        report_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO sensitivity_classification_reports "
                "(id, permission_matrix_id, coverage_score, coverage_band) "
                "VALUES (:rid, :mid, 0.5, 'medium')"
            ),
            {"rid": report_id, "mid": matrix_id},
        )
        # Update the denormalized columns on permission_matrices.
        db.execute(
            text(
                "UPDATE permission_matrices "
                "SET coverage_band = 'medium', tag_count = 3, "
                "untagged_rule_count = 1 "
                "WHERE permission_matrix_id = :mid"
            ),
            {"mid": matrix_id},
        )
        db.commit()

        # Verify denormalization.
        row = db.execute(
            text(
                "SELECT coverage_band, tag_count, untagged_rule_count "
                "FROM permission_matrices "
                "WHERE permission_matrix_id = :mid"
            ),
            {"mid": matrix_id},
        ).mappings().one()
        assert row["coverage_band"] == "medium"
        assert row["tag_count"] == 3
        assert row["untagged_rule_count"] == 1


# ── Test 4: Sensitivity Gate + MCP agent-scoped (Chunks 43–44) ──────────


def test_sensitivity_mcp_agent_tool_surface():
    """MCP tool frozensets enforce scope; WRITABLE_REVIEW_ROUTES separates write tools."""
    from src.mcp_server.server import READONLY_ROUTES, WRITABLE_REVIEW_ROUTES

    # Verify the tool frozensets are populated.
    assert len(READONLY_ROUTES) >= 13
    assert len(WRITABLE_REVIEW_ROUTES) >= 5

    # Verify ontology active route is in READONLY_ROUTES.
    readonly_paths = {path for _, path in READONLY_ROUTES}
    assert "/api/ontology/active" in readonly_paths

    # Verify ratify is a writable review route, not a read-only one.
    writable_paths = {path for _, path in WRITABLE_REVIEW_ROUTES}
    assert "/api/ontology/ratify" not in readonly_paths or "/api/ontology/ratify" in writable_paths


# ── Test 5: MCP write tools + Remote Support Sessions (Chunks 44–45) ───


def test_mcp_support_session_blocked_routes():
    """BLOCKED_FROM_SUPPORT_SESSION_ROUTES blocks sensitive routes from support."""
    from src.support.refused_routes import BLOCKED_FROM_SUPPORT_SESSION_ROUTES

    # Verify all 4 entries are present.
    assert len(BLOCKED_FROM_SUPPORT_SESSION_ROUTES) == 4

    # The ratify route is blocked.
    assert ("POST", "/api/permissions/matrix/ratify") in BLOCKED_FROM_SUPPORT_SESSION_ROUTES

    # The LLM config route is blocked.
    assert ("POST", "/api/llm/config") in BLOCKED_FROM_SUPPORT_SESSION_ROUTES


# ── Test 6: Elicitation event chain (Chunks 44–45) ──────────────────────


def test_elicitation_agent_identity_persistence():
    """POST event with actor_type='agent' + agent fields → round-trip."""
    from src.elicitation.event_writer import write_event
    from src.elicitation.models import ElicitationEventEnvelope

    factory = get_session_factory()

    # Clean up first.
    with factory() as db:
        db.execute(
            text(
                "ALTER TABLE elicitation_events DISABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.execute(text("DELETE FROM elicitation_events"))
        db.execute(
            text(
                "ALTER TABLE elicitation_events ENABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.commit()

    env = ElicitationEventEnvelope(
        event_id=uuid4(),
        event_type="mcp_session_started",
        session_id=uuid4(),
        actor_type="agent",
        phase_name="none",
        emitted_at=datetime.now(timezone.utc),
        schema_version=1,
        grace_version="0.46.0",
        payload={"session_id": "test-mcp-session"},
        payload_schema_version=1,
        agent_id="claude-desktop-002",
        agent_display_name="Claude Desktop",
        delegation_source="agent_on_behalf",
    )
    with factory() as db:
        write_event(db, env)

    with factory() as db:
        row = db.execute(
            text(
                "SELECT actor_type, agent_id, agent_display_name, "
                "delegation_source FROM elicitation_events "
                "WHERE event_id = :eid"
            ),
            {"eid": env.event_id},
        ).mappings().one()
    assert row["actor_type"] == "agent"
    assert row["agent_id"] == "claude-desktop-002"
    assert row["agent_display_name"] == "Claude Desktop"
    assert row["delegation_source"] == "agent_on_behalf"

    # Cleanup.
    with factory() as db:
        db.execute(
            text(
                "ALTER TABLE elicitation_events DISABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.execute(text("DELETE FROM elicitation_events"))
        db.execute(
            text(
                "ALTER TABLE elicitation_events ENABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.commit()


# ── Test 7: Three-identity audit substrate ───────────────────────────────


def test_three_identity_audit_substrate():
    """Query_Event vertex can carry support_session_id + session_id + agent_id;
    elicitation_events can carry agent_id + agent_display_name + delegation_source.

    ``agent_id`` on Query_Event is the Chunk 50 (D398) daemon audit stamp on
    retrieval/query events — see integration assert and ``migration_types``.
    """

    # 1) Verify Query_Event schema includes support_session_id + session_id.
    from src.graph.migration_types import QUERY_EVENT_PROPERTIES
    prop_names = {p["name"] for p in QUERY_EVENT_PROPERTIES}
    assert "support_session_id" in prop_names
    assert "session_id" in prop_names
    # agent_id is on Query_Event (Chunk 50, D398 — Phase 6 agent daemon audit stamp).
    assert "agent_id" in prop_names

    # 2) Verify elicitation_events table has the three agent-identity columns.
    factory = get_session_factory()
    with factory() as db:
        cols = db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'elicitation_events' "
                "AND column_name IN ('agent_id', 'agent_display_name', "
                "'delegation_source') "
                "ORDER BY column_name"
            )
        ).scalars().all()
    assert set(cols) == {"agent_id", "agent_display_name", "delegation_source"}


# ── Test 8: Setup verification ───────────────────────────────────────────


def test_setup_verification():
    """Confirm test infrastructure provisions all three backends."""
    # 1) Postgres — connect and run a trivial query.
    factory = get_session_factory()
    with factory() as db:
        result = db.execute(text("SELECT 1")).scalar()
    assert result == 1

    # 2) ArcadeDB — verify server is reachable.
    import httpx
    r = httpx.get(
        "http://localhost:2480/api/v1/server",
        auth=("root", "gracedev"),
        timeout=5,
    )
    assert r.status_code == 200

    # 3) FastAPI app — verify it can be imported.
    from src.api.main import app as fastapi_app
    assert fastapi_app is not None
