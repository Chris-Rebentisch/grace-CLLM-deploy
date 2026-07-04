"""Error types and Layer B envelope for the GrACE MCP server.

Two layers exist, and they do not overlap (D186, spec §7.4):

* Layer A — JSON-RPC native errors from the `mcp` SDK. Tool input that
  fails the registered JSON Schema is rejected before the tool
  function runs; the MCP host receives a JSON-RPC 2.0 error object
  with ``code == -32602`` (``InvalidParams``). GrACE does not define
  this shape.
* Layer B — tool/runtime errors GrACE owns. Once the SDK has invoked
  a tool function, every error GrACE returns is shaped as
  ``MCPErrorEnvelope`` (below). Covers upstream HTTP failures,
  timeouts, airgap / read-only violations, and semantic two-mode
  input conflicts that the JSON Schema cannot express.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MCPAirgapViolation(Exception):
    """Raised when the configured upstream does not resolve to a
    loopback address. Fails closed on resolution errors, empty
    results, and any non-loopback address (D183, spec §5)."""


class MCPReadOnlyViolation(Exception):
    """Raised when a tool targets a ``(method, path)`` tuple not in
    ``READONLY_ROUTES`` (D186, spec §6). Normally fires at import
    time via the ``readonly_tool`` decorator; the call-time check in
    ``http_client.call`` is a belt-and-suspenders guard."""


# Frozen set of valid Layer B error codes. Tests assert the envelope's
# ``code`` field is always one of these. Extensions require a spec
# amendment (D186).
VALID_ERROR_CODES: frozenset[str] = frozenset(
    {
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
        "UPSTREAM_NOT_FOUND",
        "SEMANTIC_INVALID_PARAMS",
        "AIRGAP_VIOLATION",
        "READONLY_VIOLATION",
    }
)


class MCPErrorEnvelope(BaseModel):
    """Layer B error envelope returned by MCP tools (spec §7.4).

    Tools never re-raise raw ``httpx`` exceptions or unhandled Python
    tracebacks to the MCP host. Instead they construct this envelope
    and return ``envelope.model_dump()`` as the tool response. The
    host LLM receives a structured dict it can reason over and
    surface to the user as a clean message.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description=(
            "Fixed-catalog error code. One of UPSTREAM_TIMEOUT, "
            "UPSTREAM_UNAVAILABLE, UPSTREAM_NOT_FOUND, "
            "SEMANTIC_INVALID_PARAMS, AIRGAP_VIOLATION, "
            "READONLY_VIOLATION."
        )
    )
    message: str = Field(
        description=(
            "Human-readable, safe to show to the MCP host user. "
            "No PII, no secrets, no upstream body echoes."
        )
    )
    tool: str = Field(
        description=(
            "Name of the tool that produced the envelope, e.g. "
            "'grace_search'."
        )
    )
    status: int | None = Field(
        default=None,
        description=(
            "Upstream HTTP status code when applicable (e.g. 404, "
            "502); otherwise None."
        ),
    )
    details: dict | None = Field(
        default=None,
        description=(
            "Optional structured context. Never contains upstream "
            "response bodies, PII, or secrets."
        ),
    )
