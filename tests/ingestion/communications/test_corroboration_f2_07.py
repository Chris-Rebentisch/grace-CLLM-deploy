"""F2-07 regression tests: better ER must not starve corroboration.

Validation-run evidence: the CLOSING-FACT vertex carried produced_by edges to 4 email
Extraction_Events from 3 distinct senders, but canonical merge left
evidence_origin='document' and the scorer's candidate filter never saw it;
sender resolution also failed 4/4 on a fresh graph (no email aliases).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ingestion.communications.corroboration_scorer import _resolve_sender


# ---------------------------------------------------------------------------
# evidence_origin merge (graph_writer, source-pinned like the F-38 tests)
# ---------------------------------------------------------------------------


def test_graph_writer_merges_origin_to_hybrid():
    from src.extraction import graph_writer

    src = inspect.getsource(graph_writer)
    assert "evidence_origin = 'hybrid'" in src
    # The merge only fires when origins differ and existing isn't already hybrid.
    assert 'existing_origin != "hybrid"' in src


# ---------------------------------------------------------------------------
# display-name fallback sender resolution
# ---------------------------------------------------------------------------


def _client(rows_by_needle: dict):
    client = MagicMock()

    async def fake(query, params=None):
        return {"result": rows_by_needle.get((params or {}).get("needle"), [])}

    client.execute_cypher = AsyncMock(side_effect=fake)
    return client


@pytest.mark.asyncio
async def test_display_name_fallback_resolves_fresh_graph_sender():
    """Email misses (no aliases on a fresh graph); display name hits Person.name."""
    client = _client({"Karen Ellison": [{"gid": "person-karen"}]})
    cache: dict = {}
    diag: dict = {}
    gid, category = await _resolve_sender(
        client, "kellison@lakeshore.example", ["Person"],
        cache=cache, diag=diag, display_name="Karen Ellison",
    )
    assert gid == "person-karen"
    assert category == "canonical"


@pytest.mark.asyncio
async def test_email_alias_still_first_needle():
    client = _client(
        {
            "kellison@lakeshore.example": [{"gid": "person-by-alias"}],
            "Karen Ellison": [{"gid": "person-by-name"}],
        }
    )
    gid, _ = await _resolve_sender(
        client, "kellison@lakeshore.example", ["Person"],
        cache={}, diag={}, display_name="Karen Ellison",
    )
    assert gid == "person-by-alias"


@pytest.mark.asyncio
async def test_ambiguous_needle_never_misattributes():
    """Two Persons matching the needle -> unresolved (distinct-sender echo key)."""
    client = _client({"Karen Ellison": [{"gid": "a"}, {"gid": "b"}]})
    diag: dict = {}
    gid, category = await _resolve_sender(
        client, "kellison@lakeshore.example", ["Person"],
        cache={}, diag=diag, display_name="Karen Ellison",
    )
    assert category == "unknown"
    assert gid == "kellison@lakeshore.example"
    assert diag.get("ambiguous_senders", 0) >= 1


@pytest.mark.asyncio
async def test_unresolved_counts_in_diag():
    client = _client({})
    diag: dict = {}
    _, category = await _resolve_sender(
        client, "stranger@nowhere.example", ["Person"],
        cache={}, diag=diag, display_name="Total Stranger",
    )
    assert category == "unknown"
    assert diag.get("unresolved_senders") == 1
