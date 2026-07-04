"""Spec §11.2 item 8 — happy-path test for the three ontology tools.

* ``grace_get_active_schema``
* ``grace_get_module_schema`` (default + version_id)
* ``grace_list_schema_versions`` (default + custom limit)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.mcp_server.conftest import build_async_client


@pytest.mark.asyncio
async def test_ontology_tools_happy_path(loopback_dns):
    from src.mcp_server.tools_ontology import (
        grace_get_active_schema,
        grace_get_module_schema,
        grace_list_schema_versions,
    )

    # --- grace_get_active_schema -----
    active = {"version_number": 5, "schema_json": {}, "schema_modules": {}}
    client = build_async_client(status_code=200, json_body=active)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client
    ):
        result = await grace_get_active_schema()
    assert result == active
    assert client.request.call_args.args == ("GET", "/api/ontology/active")

    # --- grace_get_module_schema without version_id -----
    module = {"module_name": "corporate", "schema": {}}
    client2 = build_async_client(status_code=200, json_body=module)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client2
    ):
        result = await grace_get_module_schema(module_name="corporate")
    assert result == module
    call = client2.request.call_args
    assert call.args == ("GET", "/api/ontology/modules/corporate")
    assert call.kwargs.get("params") is None

    # --- grace_get_module_schema with version_id -----
    client3 = build_async_client(status_code=200, json_body=module)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client3
    ):
        result = await grace_get_module_schema(
            module_name="corporate", version_id="v3"
        )
    call = client3.request.call_args
    assert call.args == ("GET", "/api/ontology/modules/corporate")
    assert call.kwargs.get("params") == {"version_id": "v3"}

    # --- grace_list_schema_versions default -----
    versions = [{"version_number": 5}]
    client4 = build_async_client(status_code=200, json_body=versions)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client4
    ):
        result = await grace_list_schema_versions()
    assert result == versions
    call = client4.request.call_args
    assert call.args == ("GET", "/api/ontology/versions")
    assert call.kwargs.get("params") == {"limit": 20, "offset": 0}

    # --- grace_list_schema_versions custom limit -----
    client5 = build_async_client(status_code=200, json_body=versions)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client5
    ):
        result = await grace_list_schema_versions(limit=5)
    call = client5.request.call_args
    assert call.kwargs.get("params") == {"limit": 5, "offset": 0}
