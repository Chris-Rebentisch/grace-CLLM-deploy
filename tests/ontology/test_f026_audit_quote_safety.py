"""F-026 / ISS-0011 — ArcadeDB audit writes must survive quotes in KGCL text.

Every KGCL command contains single quotes (e.g. "add property 'closing_date'
to class 'Sale'"). The Migration_Event INSERT and the GovernanceDecision_Event
write previously interpolated that text into statement literals, breaking the
statement and silently dropping the ArcadeDB-side audit record on every
daemon-applied change. Both sites are now parameterized; these tests prove
the generated statement carries no raw KGCL text and the params do.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

QUOTED_KGCL = "add property 'closing_date' to class 'Sale'"


# ---------------------------------------------------------------------------
# Migration_Event insert (src/graph/schema_migration._create_migration_event)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_event_insert_is_parameterized_with_quoted_kgcl():
    from src.graph.schema_migration import _create_migration_event

    client = MagicMock()
    client.execute_query = AsyncMock(return_value={"result": []})

    await _create_migration_event(
        client=client,
        migration_id="mig-quote-test",
        from_version=1,
        to_version=2,
        ddl_executed_count=1,
        ddl_failed_count=0,
        types_added=["Sale"],
        types_deprecated=[],
        properties_added=["Sale.closing_date"],
        kgcl_commands=[QUOTED_KGCL],
        status="success",
    )

    client.execute_query.assert_awaited_once()
    args, kwargs = client.execute_query.call_args
    language, command = args[0], args[1]
    params = kwargs["params"]

    assert language == "sql"
    # Statement references named params — no interpolated payload text.
    assert ":kgcl_commands" in command
    assert ":status" in command
    assert "closing_date" not in command
    assert "'" not in command.replace("'system'", "")  # only the fixed literal remains
    # The quoted KGCL command rides in the out-of-band params, intact.
    assert params["kgcl_commands"] == json.dumps([QUOTED_KGCL])
    assert QUOTED_KGCL in params["kgcl_commands"]
    assert params["migration_id"] == "mig-quote-test"


@pytest.mark.asyncio
async def test_migration_event_insert_failure_is_log_and_continue():
    """ArcadeDB failure must not raise — migration itself already succeeded."""
    from src.graph.arcade_client import ArcadeDBError
    from src.graph.schema_migration import _create_migration_event

    client = MagicMock()
    client.execute_query = AsyncMock(side_effect=ArcadeDBError(500, "boom"))

    # Must not raise.
    await _create_migration_event(
        client=client,
        migration_id="mig-fail-test",
        from_version=1,
        to_version=2,
        ddl_executed_count=0,
        ddl_failed_count=0,
        types_added=[],
        types_deprecated=[],
        properties_added=[],
        kgcl_commands=[QUOTED_KGCL],
        status="success",
    )


# ---------------------------------------------------------------------------
# GovernanceDecision_Event write (src/ontology/agent_daemon._record_governance_event)
# ---------------------------------------------------------------------------


def _run_record_governance_event(mock_client, reason):
    from src.ontology.agent_daemon import _record_governance_event

    db = MagicMock()
    with patch("src.graph.arcade_client.ArcadeClient", return_value=mock_client):
        _record_governance_event(
            db,
            decision_type="autonomous_proposal_applied",
            agent_id="grace-agent-daemon",
            proposal_id=uuid4(),
            tier=1,
            outcome="applied",
            reason=reason,
        )
    return db


def test_governance_event_arcadedb_write_is_parameterized_with_quoted_kgcl():
    reason = f'applied ["{QUOTED_KGCL}"]'
    mock_client = MagicMock()
    mock_client.execute_cypher = AsyncMock(return_value={"result": []})

    db = _run_record_governance_event(mock_client, reason)

    # Postgres side written regardless.
    db.add.assert_called_once()
    db.flush.assert_called_once()

    mock_client.execute_cypher.assert_awaited_once()
    args, kwargs = mock_client.execute_cypher.call_args
    query = args[0]
    params = kwargs["params"]

    # Statement references $params — no interpolated payload text.
    assert "$reason" in query
    assert "$decision_type" in query
    assert "closing_date" not in query
    assert "'" not in query
    # The quoted reason rides in the out-of-band params, intact.
    assert params["reason"] == reason
    assert params["decision_type"] == "autonomous_proposal_applied"
    assert params["tier"] == 1


def test_governance_event_arcadedb_failure_is_log_and_continue():
    """ArcadeDB failure must not raise — Postgres row is authoritative."""
    mock_client = MagicMock()
    mock_client.execute_cypher = AsyncMock(side_effect=ConnectionError("down"))

    db = _run_record_governance_event(mock_client, f'applied ["{QUOTED_KGCL}"]')

    # No exception propagated; Postgres write still happened.
    db.add.assert_called_once()
    db.flush.assert_called_once()


def test_governance_event_none_fields_are_filtered_from_params():
    mock_client = MagicMock()
    mock_client.execute_cypher = AsyncMock(return_value={"result": []})

    from src.ontology.agent_daemon import _record_governance_event

    db = MagicMock()
    with patch("src.graph.arcade_client.ArcadeClient", return_value=mock_client):
        _record_governance_event(
            db,
            decision_type="kill_switch_engaged",
            agent_id="operator",
            reason=None,  # None fields must not appear in statement or params
        )

    args, kwargs = mock_client.execute_cypher.call_args
    assert "$reason" not in args[0]
    assert "reason" not in kwargs["params"]
