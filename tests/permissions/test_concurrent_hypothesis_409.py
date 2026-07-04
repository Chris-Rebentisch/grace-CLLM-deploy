"""D341 concurrent hypothesis-trigger 409 test (Chunk 46, D378.c).

Two concurrent requests for the same ``evidence_id`` must produce
exactly one 202 and one 409 (DV4 partial-unique-index race protection).
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest

from src.api.main import app

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
        reason="Postgres not available",
    ),
    pytest.mark.asyncio,
]


async def _post_hypothesis(base_url: str, evidence_id: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=transport,
        base_url=base_url,
        headers={"X-Admin-Key": "test-key"},
    ) as client:
        return await client.post(
            "/api/permissions/matrix/hypothesis/generate",
            json={"evidence_id": evidence_id},
        )


async def test_concurrent_same_evidence_id_produces_202_and_409():
    """Two concurrent POSTs with the same evidence_id → one 202 + one 409."""
    evidence_id = str(uuid4())
    os.environ["GRACE_ADMIN_KEY"] = "test-key"
    try:
        r1, r2 = await asyncio.gather(
            _post_hypothesis("http://test", evidence_id),
            _post_hypothesis("http://test", evidence_id),
        )
    finally:
        os.environ.pop("GRACE_ADMIN_KEY", None)
    status_codes = {r1.status_code, r2.status_code}
    assert status_codes == {202, 409}, f"Expected {{202, 409}}, got {status_codes}"


async def test_concurrent_different_evidence_ids_both_succeed():
    """Two concurrent POSTs with different evidence_ids → both 202."""
    eid_a = str(uuid4())
    eid_b = str(uuid4())
    os.environ["GRACE_ADMIN_KEY"] = "test-key"
    try:
        r1, r2 = await asyncio.gather(
            _post_hypothesis("http://test", eid_a),
            _post_hypothesis("http://test", eid_b),
        )
    finally:
        os.environ.pop("GRACE_ADMIN_KEY", None)
    assert r1.status_code == 202, f"Expected 202 for eid_a, got {r1.status_code}"
    assert r2.status_code == 202, f"Expected 202 for eid_b, got {r2.status_code}"
