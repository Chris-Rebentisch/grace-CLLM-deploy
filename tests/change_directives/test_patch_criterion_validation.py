"""ISS-0054 PATCH follow-up — patch_criterion validation parity tests.

F-0047c / ISS-0054 closed the create-path gap (schema-grounded compile +
vocabulary check). This file covers the deferred PATCH half: the
``approve`` / ``edit`` / ``manual_override`` actions must run the SAME
validation ladder the create path runs (vocabulary membership + two-stage
ArcadeDB EXPLAIN), with the same graceful degradation when ArcadeDB is
unreachable. Pure unit tests — mocked Session, mocked httpx transport,
no live services, no ``grace`` DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from src.change_directives.evidence_criterion import (
    validate_operator_cypher,
    vocabulary_error_detail,
)
from src.change_directives.models import CriterionPatchRequest
from src.change_directives.routes import patch_criterion

_SEGMENT_SCHEMA = {
    "entity_types": {
        "Legal_Entity": {"description": "A legal entity"},
        "Insurance_Policy": {"description": "A policy"},
    },
    "relationships": {
        "participates_in": {"description": "participation edge"},
    },
}

_ON_SCHEMA_CYPHER = (
    "MATCH (e:Legal_Entity)-[:participates_in]->(p:Insurance_Policy) "
    "RETURN count(e) AS c"
)
_OFF_SCHEMA_CYPHER = "MATCH (p:Property)-[:has_zoning]->(z:Zoning) RETURN z"


# ---------------------------------------------------------------------------
# validate_operator_cypher — the shared create-path ladder (unit level)
# ---------------------------------------------------------------------------


def test_vocabulary_error_detail_names_off_schema_tokens() -> None:
    detail = vocabulary_error_detail(_OFF_SCHEMA_CYPHER, _SEGMENT_SCHEMA)
    assert detail is not None
    assert "off_schema_tokens" in detail
    for token in ("Property", "Zoning", "has_zoning"):
        assert token in detail


def test_vocabulary_error_detail_none_for_on_schema() -> None:
    assert vocabulary_error_detail(_ON_SCHEMA_CYPHER, _SEGMENT_SCHEMA) is None


@pytest.mark.asyncio
async def test_validate_off_schema_fails_before_explain() -> None:
    """Vocabulary check runs FIRST — no ArcadeDB round-trip on failure."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"result": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok, err = await validate_operator_cypher(
            _OFF_SCHEMA_CYPHER, _SEGMENT_SCHEMA, explain_client=client
        )
    assert ok is False
    assert err is not None
    assert "off_schema_tokens" in err
    assert "Zoning" in err and "has_zoning" in err
    assert calls["n"] == 0  # never reached EXPLAIN


@pytest.mark.asyncio
async def test_validate_valid_cypher_passes_all_three_stages() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"result": [{"plan": "ok"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok, err = await validate_operator_cypher(
            _ON_SCHEMA_CYPHER, _SEGMENT_SCHEMA, explain_client=client
        )
    assert ok is True
    assert err is None
    assert calls["n"] == 2  # Stage 1 (syntactic) + Stage 2 (semantic)


@pytest.mark.asyncio
async def test_validate_arcade_unreachable_degrades_like_create() -> None:
    """ArcadeDB down -> named failure (create-path parity), not a crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok, err = await validate_operator_cypher(
            _ON_SCHEMA_CYPHER, _SEGMENT_SCHEMA, explain_client=client
        )
    assert ok is False
    assert err is not None
    assert err.startswith("syntactic:")
    assert "arcade_unreachable" in err


@pytest.mark.asyncio
async def test_validate_semantic_stage_failure_named() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"result": [{"plan": "ok"}]})
        return httpx.Response(500, json={"error": "execution-plan: boom"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok, err = await validate_operator_cypher(
            _ON_SCHEMA_CYPHER, _SEGMENT_SCHEMA, explain_client=client
        )
    assert ok is False
    assert (err or "").startswith("semantic:")


# ---------------------------------------------------------------------------
# patch_criterion route behavior (mocked Session / Request)
# ---------------------------------------------------------------------------

_AUTHOR = uuid4()
_DIRECTIVE_ID = uuid4()
_CRITERION_ID = uuid4()


def _request() -> Any:
    return SimpleNamespace(headers={"X-Requesting-User": str(_AUTHOR)})


def _mock_db(stored_row: dict[str, Any] | None) -> MagicMock:
    """Session mock: SELECT stored row -> UPDATE -> SELECT final row."""
    db = MagicMock()
    sel_stored = MagicMock()
    sel_stored.mappings.return_value.first.return_value = stored_row
    upd = MagicMock()
    final_row = dict(stored_row or {})
    sel_final = MagicMock()
    sel_final.mappings.return_value.first.return_value = final_row
    db.execute.side_effect = [sel_stored, upd, sel_final]
    return db


def _stored_row(compiled_query: str | None) -> dict[str, Any]:
    return {
        "criterion_id": str(_CRITERION_ID),
        "directive_id": str(_DIRECTIVE_ID),
        "compiled_query": compiled_query,
        "compilation_status": "proposed",
        "error_detail": None,
    }


def _update_params(db: MagicMock) -> dict[str, Any]:
    """Params bound to the UPDATE statement (second execute call)."""
    return db.execute.call_args_list[1].args[1]


async def _run_patch(
    db: MagicMock,
    action: str,
    compiled_query: str | None = None,
    explain_result: tuple[bool, str | None] = (True, None),
) -> dict[str, Any]:
    async def _fake_explain(cypher, *, client, semantic=False):
        return explain_result

    with (
        patch(
            "src.change_directives.routes.repository.get_by_id",
            return_value={"authored_by": str(_AUTHOR)},
        ),
        patch(
            "src.change_directives.routes._ratified_segment_schema",
            return_value=_SEGMENT_SCHEMA,
        ),
        patch(
            "src.change_directives.evidence_criterion._explain_query",
            new=_fake_explain,
        ),
    ):
        return await patch_criterion(
            _DIRECTIVE_ID,
            _CRITERION_ID,
            CriterionPatchRequest(action=action, compiled_query=compiled_query),
            _request(),
            db=db,
        )


@pytest.mark.asyncio
async def test_edit_off_schema_lands_proposed_with_named_tokens() -> None:
    """edit with off-schema Cypher -> proposed + tokens in error_detail."""
    db = _mock_db(_stored_row("MATCH (n:Legal_Entity) RETURN n"))
    await _run_patch(db, "edit", compiled_query=_OFF_SCHEMA_CYPHER)
    params = _update_params(db)
    assert params["cs"] == "proposed"  # never manually_authored-as-valid
    assert params["err"] is not None
    assert "off_schema_tokens" in params["err"]
    for token in ("Property", "Zoning", "has_zoning"):
        assert token in params["err"]
    assert params["cq"] == _OFF_SCHEMA_CYPHER  # query stored for repair


@pytest.mark.asyncio
async def test_manual_override_valid_cypher_passes_all_stages() -> None:
    db = _mock_db(_stored_row(None))
    await _run_patch(
        db, "manual_override", compiled_query=_ON_SCHEMA_CYPHER
    )
    params = _update_params(db)
    assert params["cs"] == "manually_authored"
    assert params["err"] is None
    assert params["cq"] == _ON_SCHEMA_CYPHER


@pytest.mark.asyncio
async def test_edit_arcade_unreachable_degrades_to_proposed() -> None:
    """EXPLAIN degradation mirrors create: named failure, stays proposed."""
    db = _mock_db(_stored_row(None))
    await _run_patch(
        db,
        "edit",
        compiled_query=_ON_SCHEMA_CYPHER,
        explain_result=(False, "arcade_unreachable: connection refused"),
    )
    params = _update_params(db)
    assert params["cs"] == "proposed"
    assert "arcade_unreachable" in params["err"]
    assert params["err"].startswith("syntactic:")


@pytest.mark.asyncio
async def test_approve_rechecks_vocabulary_and_blocks_off_schema() -> None:
    """approve re-checks the STORED query (belt-and-braces, ISS-0054)."""
    db = _mock_db(_stored_row(_OFF_SCHEMA_CYPHER))
    await _run_patch(db, "approve")
    params = _update_params(db)
    assert params["cs"] == "proposed"  # NOT approved
    assert "off_schema_tokens" in params["err"]
    assert "Zoning" in params["err"]
    assert "cq" not in params  # stored query untouched


@pytest.mark.asyncio
async def test_approve_on_schema_stored_query_flips_to_approved() -> None:
    db = _mock_db(_stored_row(_ON_SCHEMA_CYPHER))
    await _run_patch(db, "approve")
    params = _update_params(db)
    assert params["cs"] == "approved"
    assert params["err"] is None  # stale error cleared


@pytest.mark.asyncio
async def test_approve_without_stored_query_returns_422() -> None:
    db = _mock_db(_stored_row(None))
    with pytest.raises(HTTPException) as exc:
        await _run_patch(db, "approve")
    assert exc.value.status_code == 422
    assert exc.value.detail == {"error": "approve_requires_compiled_query"}


@pytest.mark.asyncio
async def test_missing_criterion_row_returns_404() -> None:
    db = _mock_db(None)
    with pytest.raises(HTTPException) as exc:
        await _run_patch(db, "approve")
    assert exc.value.status_code == 404
    assert exc.value.detail == {"error": "criterion_not_found"}
