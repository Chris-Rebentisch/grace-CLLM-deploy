"""Missing Chunk 36 test: 429 rate-limit for gap-report force-regenerate (D378.d).

Tests the in-memory rate limiter that rejects a second ``?force=true``
regeneration within 60 seconds for the same session.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from src.api.recon_routes import (
    _check_force_regen_rate_limit,
    _force_regen_last,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
    reason="Postgres not available",
)


def test_force_regen_rate_limit_429_on_second_call():
    """Second ``?force=true`` within 60s for the same session → 429."""
    sid = uuid4()
    # Clear any prior state for this session
    _force_regen_last.pop(str(sid), None)

    # First call succeeds
    _check_force_regen_rate_limit(sid)

    # Second call within the window raises 429
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _check_force_regen_rate_limit(sid)
    assert exc_info.value.status_code == 429


def test_force_regen_different_sessions_allowed():
    """Different sessions can force-regenerate concurrently."""
    sid_a = uuid4()
    sid_b = uuid4()
    _force_regen_last.pop(str(sid_a), None)
    _force_regen_last.pop(str(sid_b), None)

    _check_force_regen_rate_limit(sid_a)
    _check_force_regen_rate_limit(sid_b)  # Must not raise
