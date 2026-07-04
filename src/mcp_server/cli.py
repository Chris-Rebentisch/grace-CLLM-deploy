"""CLI entrypoint for ``python -m src.mcp_server`` (spec §4.2).

Responsibilities:

1. Validate ``MCP_GRACE_BASE_URL`` at startup per §5.3:
   literal hostname check + DNS loopback check.
2. Import the tool modules so the ``@readonly_tool`` decorator side
   effects register all 13 tools on the shared ``FastMCP`` instance.
3. Run the FastMCP stdio transport; the MCP host owns the process
   lifecycle.

Exit codes:
* ``0`` — clean shutdown (stdin closed by the MCP host).
* ``1`` — any other unhandled exception; logged to stderr.
* ``2`` — startup config error (invalid ``MCP_GRACE_BASE_URL``).
* ``3`` — airgap violation raised at startup.
"""

from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import urlparse

from src.mcp_server.errors import MCPAirgapViolation
from src.mcp_server.http_client import (
    _assert_loopback,
    _literal_hostname_check,
)


async def _startup_validate() -> None:
    """Re-parse ``MCP_GRACE_BASE_URL`` from the live environment and
    run both checkpoints from §5.3.

    Defined as a free function (not a ``main``-local closure) so
    tests can call it directly with monkeypatched env vars without
    spinning up the stdio transport.
    """
    base_url = os.environ.get(
        "MCP_GRACE_BASE_URL", "http://127.0.0.1:8000"
    )
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            "MCP_GRACE_BASE_URL must use http or https scheme"
        )
    if parsed.hostname is None:
        raise MCPAirgapViolation(
            f"MCP_GRACE_BASE_URL {base_url!r} has no hostname component"
        )
    _literal_hostname_check(parsed.hostname)
    port = parsed.port or (443 if parsed.scheme == "https" else 8000)
    await _assert_loopback(parsed.hostname, port)


def main() -> None:
    """Program entry point.

    Runs the startup validation, imports tool modules to register
    tools on the shared ``FastMCP`` instance, and starts the stdio
    transport.
    """
    try:
        asyncio.run(_startup_validate())
    except MCPAirgapViolation as exc:
        print(f"[mcp-server] airgap violation: {exc}", file=sys.stderr)
        sys.exit(3)
    except (ValueError, KeyError) as exc:
        print(f"[mcp-server] config error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # pragma: no cover
        print(f"[mcp-server] unhandled: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        # Import for registration side effects (@readonly_tool /
        # @mcp.tool() / @writable_review_tool decorators register the
        # tools on the shared FastMCP instance at import time).
        # Phase-9 fix: ``tools_review`` and ``tools_session`` were
        # missing from this list, so the 8 Chunk-44 review / session
        # tools (grace_session_start, grace_review_decide,
        # grace_teachback_capture, etc.) were never registered with
        # the MCP server even though the modules existed in src/.
        # Only 15 of the documented 23 tools were reaching agents.
        from src.mcp_server import (  # noqa: F401
            tools_change_directives,
            tools_discovery,
            tools_extraction,
            tools_graph,
            tools_meta,
            tools_ontology,
            tools_retrieval,
            tools_review,
            tools_session,
        )
        from src.mcp_server.server import mcp

        # F-51 — hydrate the permission enforcer from permission_matrices so
        # writable review tools (grace_session_start, grace_review_decide, ...)
        # see a ratified matrix. The enforcer previously only rehydrated inside
        # the API's in-process ratify route, so the MCP process always ran with
        # matrix=None → writable tools denied "no_active_matrix" despite a
        # ratified matrix in the DB (D528 known gap). Best-effort.
        try:
            from src.permissions.enforcer import hydrate_enforcer_from_db

            hydrate_enforcer_from_db()
        except Exception as exc:  # noqa: BLE001
            print(f"[mcp-server] enforcer hydration skipped: {exc}", file=sys.stderr)

        mcp.run()
    except MCPAirgapViolation as exc:
        print(f"[mcp-server] airgap violation: {exc}", file=sys.stderr)
        sys.exit(3)
    except (ValueError, KeyError) as exc:
        print(f"[mcp-server] config error: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        print(f"[mcp-server] unhandled: {exc}", file=sys.stderr)
        sys.exit(1)
