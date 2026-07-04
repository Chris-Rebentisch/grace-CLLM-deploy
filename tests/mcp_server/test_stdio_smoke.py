"""Spec §11.2 item 15 — stdio round-trip smoke.

Spawns ``python -m src.mcp_server`` as a subprocess, opens an MCP
``ClientSession`` against its stdio transport, lists tools, and
calls ``grace_explain_capabilities`` (the one tool that needs no
backend). Closing the client's context closes stdin and the
subprocess exits cleanly.

Marked ``@pytest.mark.mcp_stdio`` (spec §11.2). The marker is
informational — this test runs as part of the default suite per
spec §11.3 ("pytest -v --tb=short" with no marker flags) because
the meta tool has no HTTP dependency.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


@pytest.mark.mcp_stdio
@pytest.mark.asyncio
async def test_stdio_roundtrip():
    # Inherit caller env so PATH/HOME/etc. are present; override the
    # MCP-specific vars to a safe known state.
    child_env = {
        **os.environ,
        "MCP_GRACE_BASE_URL": "http://127.0.0.1:8000",
        "MCP_TIMEOUT_SECONDS": "30",
        # Ensure src.mcp_server is importable from the project root.
        "PYTHONPATH": PROJECT_ROOT
        + (
            os.pathsep + os.environ["PYTHONPATH"]
            if os.environ.get("PYTHONPATH")
            else ""
        ),
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_server"],
        cwd=PROJECT_ROOT,
        env=child_env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            # 33 tools per CLAUDE.md (Chunk 72a): 18 @readonly_tool + 4 @mcp.tool()
            # + 11 @writable_review_tool.
            assert len(tools_result.tools) == 37

            tool_names = {t.name for t in tools_result.tools}
            assert "grace_explain_capabilities" in tool_names

            # Safe smoke target: no upstream dependency.
            result = await session.call_tool(
                "grace_explain_capabilities", {}
            )
            assert result.content, "tool returned empty content"
            first = result.content[0]
            text = getattr(first, "text", None)
            assert isinstance(text, str)
            assert text.strip()
