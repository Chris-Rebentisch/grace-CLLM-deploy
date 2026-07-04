"""Review-decision MCP tools (Chunk 44, CP5, D366/D367).

Five tools for the review workflow:
- Two GET-wrapping read tools (verb bypass, no frozenset entry needed).
- Three POST-wrapping write tools (decorated with @writable_review_tool).

All include @permission_gated_tool. Static string literal descriptions
(D182). Deep-link handoff to existing /review page.
"""

from __future__ import annotations

import os
from uuid import uuid4

from src.mcp_server import http_client
from src.mcp_server.server import (
    mcp,
    permission_gated_tool,
    writable_review_tool,
)


# --- GET-wrapping read tools (verb bypass per CP1) ---


@mcp.tool()
@permission_gated_tool("ontology_module", "global", "view")
async def grace_review_next_element(
    session_id: str,
) -> dict:
    """Get the next un-reviewed schema element in the session.
    Returns element details with competency-question context and
    certainty band. Use this to iterate through elements requiring
    review decisions."""
    result = await http_client.call(
        "GET",
        "/api/ontology/review/{session_id}/elements",
        tool="grace_review_next_element",
        path_params={"session_id": session_id},
    )
    return result


@mcp.tool()
@permission_gated_tool("ontology_module", "global", "view")
async def grace_review_session_summary(
    session_id: str,
) -> dict:
    """Get the current review session completion status and
    progress summary. Returns reviewed and total element counts
    with per-decision-type breakdown."""
    result = await http_client.call(
        "GET",
        "/api/ontology/review/{session_id}/progress",
        tool="grace_review_session_summary",
        path_params={"session_id": session_id},
    )
    return result


# Set __grace_route__ manually for GET-wrapping tools so endpoint
# mapping tests can verify them via the verb-bypass path.
grace_review_next_element.__grace_route__ = (  # type: ignore[attr-defined]
    "GET", "/api/ontology/review/{session_id}/elements"
)
grace_review_session_summary.__grace_route__ = (  # type: ignore[attr-defined]
    "GET", "/api/ontology/review/{session_id}/progress"
)


# --- POST-wrapping write tools ---


@writable_review_tool("POST", "/api/ontology/review/{session_id}/decide")
@permission_gated_tool("ontology_module", "global", "edit")
async def grace_review_decide(
    session_id: str,
    element_name: str,
    decision: str,
    rationale: str | None = None,
) -> dict:
    """Record a single review decision for one schema element.
    Valid decisions: approved, renamed, edited, split, merged,
    rejected, redirected, reclassified, auto_approved. Returns
    confirmation with the recorded decision. Per-element only,
    no batch decisions."""
    body = {
        "element_type": "entity_type",
        "element_name": element_name,
        "decision": decision,
        "reviewer": os.environ.get("GRACE_AGENT_ID", "mcp-user"),
        "notes": rationale,
    }

    result = await http_client.call(
        "POST",
        "/api/ontology/review/{session_id}/decide",
        tool="grace_review_decide",
        path_params={"session_id": session_id},
        json_body=body,
    )
    if "code" in result:
        return result

    deep_link = (
        f"localhost:3000/review?session_id={session_id}"
        f"&step=element_review"
    )
    return {
        "session_id": session_id,
        "element_name": element_name,
        "decision": decision,
        "message": f"Decision recorded: {decision} for {element_name}",
        "deep_link": deep_link,
    }


@writable_review_tool("POST", "/api/elicitation/events")
@permission_gated_tool("ontology_module", "global", "edit")
async def grace_laddering_followup(
    session_id: str,
    element_name: str,
    question: str,
) -> dict:
    """Emit a laddering follow-up question for a schema element.
    Records the question as a telemetry event with agent identity
    for audit trail. Use this to probe deeper into element
    semantics during clarify phase."""
    from datetime import datetime

    body = {
        "event_id": str(uuid4()),
        "event_type": "mcp_laddering_followup_emitted",
        "session_id": session_id,
        "actor_type": os.environ.get("GRACE_AGENT_ID", "") and "agent" or "human",
        "phase_name": "clarify",
        "emitted_at": datetime.now().isoformat(),
        "schema_version": 1,
        "grace_version": "0.44.0",
        "payload": {
            "session_id": session_id,
            "element_name": element_name,
            "question": question,
            "agent_id": os.environ.get("GRACE_AGENT_ID"),
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
        tool="grace_laddering_followup",
        json_body=body,
    )
    if "code" in result:
        return result

    return {
        "session_id": session_id,
        "element_name": element_name,
        "question": question,
        "message": f"Laddering follow-up emitted for: {element_name}",
    }


@writable_review_tool("POST", "/api/elicitation/events")
@permission_gated_tool("ontology_module", "global", "edit")
async def grace_teachback_capture(
    session_id: str,
    element_name: str,
    narrative: str,
) -> dict:
    """Record a teach-back narrative for a schema element. The
    narrative captures the reviewer's understanding in their own
    words. Records a telemetry event with agent identity for
    audit trail."""
    from datetime import datetime

    body = {
        "event_id": str(uuid4()),
        "event_type": "mcp_teachback_captured",
        "session_id": session_id,
        "actor_type": os.environ.get("GRACE_AGENT_ID", "") and "agent" or "human",
        "phase_name": "clarify",
        "emitted_at": datetime.now().isoformat(),
        "schema_version": 1,
        "grace_version": "0.44.0",
        "payload": {
            "session_id": session_id,
            "element_name": element_name,
            "narrative": narrative,
            "agent_id": os.environ.get("GRACE_AGENT_ID"),
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
        tool="grace_teachback_capture",
        json_body=body,
    )
    if "code" in result:
        return result

    return {
        "session_id": session_id,
        "element_name": element_name,
        "message": f"Teach-back narrative captured for: {element_name}",
    }
