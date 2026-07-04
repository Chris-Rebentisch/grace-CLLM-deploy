"""D292 — Change Directive repository tests.

Verifies the two-writer property: ``create()`` sets ``DRAFT`` on INSERT,
``transition()`` is the sole post-INSERT ``status`` writer, and
``patch_draft_metadata()`` never touches ``status``/visibility/identity
columns.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.change_directives.models import (
    ChangeDirectiveCreateRequest,
    ChangeDirectivePatchBody,
    DirectiveStatus,
)
from src.change_directives.repository import (
    compute_transition_hash,
    create,
    get_by_id,
    patch_draft_metadata,
    transition,
)
from src.shared.config import get_settings


@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    conn = engine.connect()
    txn = conn.begin()
    sess = Session(bind=conn, expire_on_commit=False)
    # Track directive ids so we can hard-clean after the test even though
    # transition() commits its own work.
    created_ids: list[str] = []
    sess._test_created_ids = created_ids  # type: ignore[attr-defined]
    try:
        yield sess
    finally:
        sess.close()
        # Bypass append-only triggers using the alembic.downgrading escape.
        if created_ids:
            with engine.begin() as cleanup:
                cleanup.execute(
                    text("SELECT set_config('alembic.downgrading','true', true)")
                )
                cleanup.execute(
                    text(
                        "DELETE FROM change_directive_state_transitions "
                        "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": created_ids},
                )
                cleanup.execute(
                    text(
                        "DELETE FROM change_directives "
                        "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": created_ids},
                )
        try:
            txn.rollback()
        except Exception:  # noqa: BLE001
            pass
        conn.close()


def _track(session: Session, directive: dict) -> None:
    session._test_created_ids.append(str(directive["directive_id"]))  # type: ignore[attr-defined]


def _oa_request(**overrides) -> ChangeDirectiveCreateRequest:
    base = {
        "tier": "Operational_Adjustment",
        "title": "Roll out new vendor onboarding form",
        "description": "Adopt the v2 onboarding form by end of quarter.",
        "affected_segments": ["operations"],
    }
    base.update(overrides)
    return ChangeDirectiveCreateRequest(**base)


def test_create_sets_status_draft(session) -> None:
    authored_by = uuid4()
    directive = create(session, _oa_request(), authored_by)
    _track(session, directive)
    session.commit()

    assert directive["status"] == DirectiveStatus.DRAFT.value
    assert directive["tier"] == "Operational_Adjustment"
    assert str(directive["authored_by"]) == str(authored_by)


def test_transition_is_sole_status_writer_with_hash_chain(session) -> None:
    authored_by = uuid4()
    directive = create(session, _oa_request(), authored_by)
    _track(session, directive)
    session.commit()
    directive_id = directive["directive_id"]

    updated = transition(
        session,
        directive_id,
        DirectiveStatus.ACTIVE,
        authored_by,
        reason="approved at standup",
    )
    assert updated["status"] == DirectiveStatus.ACTIVE.value

    # Hash chain row was written and links to no prior transition.
    rows = session.execute(
        text(
            "SELECT from_state, to_state, hash_chain, prev_transition_hash "
            "FROM change_directive_state_transitions "
            "WHERE directive_id = :id ORDER BY transitioned_at"
        ),
        {"id": str(directive_id)},
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["from_state"] == "draft"
    assert rows[0]["to_state"] == "active"
    assert rows[0]["prev_transition_hash"] is None
    assert isinstance(rows[0]["hash_chain"], str)
    assert len(rows[0]["hash_chain"]) == 64

    # Second transition links the chain forward.
    transition(
        session,
        directive_id,
        DirectiveStatus.REALIZED,
        authored_by,
        reason="evidence met",
    )
    rows2 = session.execute(
        text(
            "SELECT from_state, to_state, hash_chain, prev_transition_hash "
            "FROM change_directive_state_transitions "
            "WHERE directive_id = :id ORDER BY transitioned_at"
        ),
        {"id": str(directive_id)},
    ).mappings().all()
    assert len(rows2) == 2
    assert rows2[1]["prev_transition_hash"] == rows2[0]["hash_chain"]
    assert rows2[1]["from_state"] == "active"
    assert rows2[1]["to_state"] == "realized"


def test_transition_rejects_illegal_state(session) -> None:
    authored_by = uuid4()
    directive = create(session, _oa_request(), authored_by)
    _track(session, directive)
    session.commit()

    # DRAFT -> REALIZED is not allowed.
    with pytest.raises(HTTPException) as excinfo:
        transition(
            session,
            directive["directive_id"],
            DirectiveStatus.REALIZED,
            authored_by,
            reason=None,
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["error"] == "illegal_transition"


def test_transition_to_superseded_requires_target(session) -> None:
    authored_by = uuid4()
    directive = create(session, _oa_request(), authored_by)
    _track(session, directive)
    session.commit()
    directive_id = directive["directive_id"]

    transition(
        session,
        directive_id,
        DirectiveStatus.ACTIVE,
        authored_by,
        reason="ratified",
    )
    # ACTIVE -> SUPERSEDED requires a target directive id.
    with pytest.raises(HTTPException) as excinfo:
        transition(
            session,
            directive_id,
            DirectiveStatus.SUPERSEDED,
            authored_by,
            reason=None,
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["error"] == "supersession_requires_target_directive_id"


def test_supersession_dual_writes_pointer(session) -> None:
    authored_by = uuid4()
    a = create(session, _oa_request(title="original"), authored_by)
    _track(session, a)
    session.commit()
    transition(session, a["directive_id"], DirectiveStatus.ACTIVE, authored_by, "go")

    b = create(session, _oa_request(title="replacement"), authored_by)
    _track(session, b)
    session.commit()

    transition(
        session,
        a["directive_id"],
        DirectiveStatus.SUPERSEDED,
        authored_by,
        reason="superseded by replacement",
        superseded_by_directive_id=b["directive_id"],
    )
    refreshed = get_by_id(session, a["directive_id"])
    assert refreshed["status"] == DirectiveStatus.SUPERSEDED.value
    assert str(refreshed["superseded_by_directive_id"]) == str(b["directive_id"])


def test_patch_draft_metadata_refuses_non_draft(session) -> None:
    authored_by = uuid4()
    directive = create(session, _oa_request(), authored_by)
    _track(session, directive)
    session.commit()
    transition(
        session,
        directive["directive_id"],
        DirectiveStatus.ACTIVE,
        authored_by,
        reason="ratified",
    )
    with pytest.raises(HTTPException) as excinfo:
        patch_draft_metadata(
            session,
            directive["directive_id"],
            ChangeDirectivePatchBody(title="renamed"),
            authored_by,
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["error"] == "patch_only_allowed_in_draft"


def test_patch_draft_metadata_never_writes_status(session) -> None:
    authored_by = uuid4()
    directive = create(session, _oa_request(), authored_by)
    _track(session, directive)
    session.commit()

    patched = patch_draft_metadata(
        session,
        directive["directive_id"],
        ChangeDirectivePatchBody(
            title="updated title",
            description="updated description",
            affected_segments=["finance"],
        ),
        authored_by,
    )
    assert patched["status"] == DirectiveStatus.DRAFT.value
    assert patched["title"] == "updated title"
    assert patched["affected_segments"] == ["finance"]
    # visibility unchanged
    assert patched["visibility"] == "permission_matrix_default"


def test_compute_transition_hash_is_deterministic_and_links() -> None:
    payload = {"a": 1, "b": "two"}
    h1 = compute_transition_hash(payload, None)
    h1b = compute_transition_hash(payload, None)
    assert h1 == h1b
    assert len(h1) == 64

    h2 = compute_transition_hash({"a": 1, "b": "three"}, h1)
    assert h2 != h1
    # Same payload but different prev_hash yields different hash.
    h2_alt = compute_transition_hash({"a": 1, "b": "three"}, "0" * 64)
    assert h2 != h2_alt
