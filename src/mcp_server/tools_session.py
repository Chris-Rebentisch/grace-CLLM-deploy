"""Session-lifecycle MCP write tools (Chunk 44, CP4, D365).

Three tools wrapping existing review routes for chat-native guided
review via MCP:

- ``grace_session_start`` — starts a review session.
- ``grace_session_advance_phase`` — advances the phase machine.
- ``grace_session_close`` — closes the session (summary + confirm).

All decorated with ``@writable_review_tool`` + ``@permission_gated_tool``.
Static string literal descriptions (D182). Agent identity threaded via
``agent_adapter.resolve_principal_with_agent()``.

D193 non-violation note: ``grace_session_close`` calls
``POST /api/regeneration/close-summary`` and ``close-confirm`` — these
are HTTP calls to existing routes, NOT modifications to
``src/regeneration/*`` source files.
"""

from __future__ import annotations

import os
from uuid import uuid4

from src.mcp_server import http_client
from src.mcp_server.server import permission_gated_tool, writable_review_tool


@writable_review_tool("POST", "/api/ontology/review/start")
@permission_gated_tool("ontology_module", "global", "edit")
async def grace_session_start(
    phase_state: str = "prepare",
) -> dict:
    """Start a new guided review session with an initial phase.
    Returns the session identifier and confirmation text. Pass
    phase_state to set the starting phase (prepare, open,
    structure, clarify, close)."""
    body = {
        "merge_run_id": f"mcp-session-{uuid4().hex[:8]}",
        "reviewer": os.environ.get("GRACE_AGENT_ID", "mcp-user"),
        "seed_schema_data": {},
    }
    result = await http_client.call(
        "POST",
        "/api/ontology/review/start",
        tool="grace_session_start",
        json_body=body,
    )
    if "code" in result:
        return result

    # F-0046 / ISS-0048 (validation run 2026-07-03): POST
    # /api/ontology/review/start returns the ReviewSession model, whose
    # primary-key field is ``id`` — there is no ``session_id`` key in the
    # response. Reading only "session_id" made every MCP-driven start report
    # session_id="unknown", and grace_session_close then 422'd trying to
    # parse "unknown" as a UUID. Prefer ``id`` (the real key); keep
    # ``session_id`` as a fallback for forward-compat if the route ever
    # renames the field.
    session_id = result.get("id") or result.get("session_id") or "unknown"
    return {
        "session_id": session_id,
        "message": f"Session started: {session_id}",
        "phase_state": phase_state,
    }


@writable_review_tool("POST", "/api/elicitation/events")
@permission_gated_tool("ontology_module", "global", "edit")
async def grace_session_advance_phase(
    session_id: str,
    target_phase: str,
) -> dict:
    """Advance the review session to the next phase in the
    elicitation protocol. Valid phases: prepare, open, structure,
    clarify, close. Records a phase_entered event for audit."""
    from datetime import datetime

    body = {
        "event_id": str(uuid4()),
        "event_type": "phase_entered",
        "session_id": session_id,
        "actor_type": os.environ.get("GRACE_AGENT_ID", "") and "agent" or "human",
        "phase_name": target_phase,
        "emitted_at": datetime.now().isoformat(),
        "schema_version": 1,
        "grace_version": "0.44.0",
        "payload": {
            "entered_phase": target_phase,
            "entered_at": datetime.now().isoformat(),
        },
        "payload_schema_version": 1,
    }
    agent_id = os.environ.get("GRACE_AGENT_ID", "")
    if agent_id:
        body["agent_id"] = agent_id
        body["agent_display_name"] = os.environ.get(
            "GRACE_AGENT_DISPLAY_NAME", ""
        )
        body["delegation_source"] = "agent_on_behalf"

    result = await http_client.call(
        "POST",
        "/api/elicitation/events",
        tool="grace_session_advance_phase",
        json_body=body,
    )
    if "code" in result:
        return result

    return {
        "session_id": session_id,
        "target_phase": target_phase,
        "message": f"Phase advanced to: {target_phase}",
    }


@writable_review_tool("POST", "/api/regeneration/close-summary")
@permission_gated_tool("ontology_module", "global", "edit")
async def grace_session_close(
    session_id: str,
) -> dict:
    """Close the review session by generating and confirming the
    session summary. Invokes close-summary then close-confirm in
    sequence. Returns the session narrative on success."""
    # Step 1: generate summary.
    summary_result = await http_client.call(
        "POST",
        "/api/regeneration/close-summary",
        tool="grace_session_close",
        json_body={"session_id": session_id},
    )
    if "code" in summary_result:
        return summary_result

    # Step 2: confirm the summary.
    confirm_result = await http_client.call(
        "POST",
        "/api/regeneration/close-confirm",
        tool="grace_session_close",
        json_body={"session_id": session_id},
    )
    if "code" in confirm_result:
        return confirm_result

    return {
        "session_id": session_id,
        "message": f"Session closed: {session_id}",
        "summary": summary_result,
    }
