"""CP5 — grace_answer session_id parameter tests (D193 preservation).

Covers:
- session_id parameter accepted.
- session_id NOT passed to RegenerationQuery.
- D193 CI gate should still pass (verified at CP9).
"""

from __future__ import annotations

import pytest


def test_grace_answer_accepts_session_id():
    """grace_answer accepts a session_id parameter."""
    from src.mcp_server.tools_retrieval import grace_answer

    import inspect

    sig = inspect.signature(grace_answer)
    assert "session_id" in sig.parameters
    param = sig.parameters["session_id"]
    assert param.default is None


def test_session_id_not_in_regeneration_body():
    """session_id is NOT included in the RegenerationRequestBody."""
    from src.mcp_server.models import RegenerationRequestBody

    fields = RegenerationRequestBody.model_fields
    assert "session_id" not in fields, (
        "session_id must NOT be in RegenerationRequestBody — D193 holds"
    )


def test_session_id_not_in_request_body_model():
    """session_id cannot leak into the request body model (contract check)."""
    from src.mcp_server.models import RegenerationRequestBody

    # Construct a body the way grace_answer does and verify session_id absent.
    body = RegenerationRequestBody(
        query_text="test", phase_state="none"
    )
    dumped = body.model_dump()
    assert "session_id" not in dumped, (
        "session_id leaked into RegenerationRequestBody.model_dump() — D193"
    )


def test_grace_answer_still_readonly_tool():
    """grace_answer is still decorated with @readonly_tool."""
    from src.mcp_server.tools_retrieval import grace_answer

    route = getattr(grace_answer, "__grace_route__", None)
    assert route == ("POST", "/api/regeneration/query")
