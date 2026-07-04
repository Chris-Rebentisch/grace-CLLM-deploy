"""F-0032(c) / ISS-0036: claim-level supersession for ER-merged vertices.

When an earlier claim ($18,500) and a later corrected claim ($15,800) resolve
into ONE merged vertex, there is no earlier/later vertex pair — vertex-level
``superseded_by`` is unobservable by design. The claim table is the system of
record: the earlier claim flips to ``status='superseded'`` and the superseding
claim records ``supersedes_claim_id`` (same vocabulary as the Chunk 30
Edit-and-Accept flow in ``src/api/claim_routes.py``).

Pure unit tests — mock SQLAlchemy session, no DB, no services.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.ingestion.communications.supersession import apply_claim_level_supersession

MEMBERS = [
    {"message_id": "<m0@ex.com>", "thread_position": 0,
     "sent_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
    {"message_id": "<m1@ex.com>", "thread_position": 1,
     "sent_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
]


def _claim(claim_id, doc, grace_id, props, status="auto_accepted", supersedes=None, created=None):
    return SimpleNamespace(
        claim_id=claim_id,
        source_document_id=doc,
        resolved_entity_grace_id=grace_id,
        properties_json=props,
        status=status,
        supersedes_claim_id=supersedes,
        created_at=created,
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Records executed statements; serves SELECT rows, swallows UPDATEs."""

    def __init__(self, select_rows):
        self._select_rows = select_rows
        self.updates: list[tuple[str, dict]] = []
        self.committed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.lstrip().upper().startswith("SELECT"):
            return _FakeResult(self._select_rows)
        self.updates.append((sql, params or {}))
        return _FakeResult([])

    def commit(self):
        self.committed = True


def test_merged_vertex_contradiction_writes_claim_level_supersession():
    """The run's Bid case: $18,500 (earlier) vs $15,800 (later) merged into
    one vertex → earlier claim superseded, later claim gets the pointer."""
    rows = [
        _claim("claim-early", "email:<m0@ex.com>", "bid-merged", {"amount": "18500"}),
        _claim("claim-late", "email:<m1@ex.com>", "bid-merged", {"amount": "15800"}),
    ]
    session = _FakeSession(rows)

    result = apply_claim_level_supersession(session, "<m0@ex.com>", MEMBERS)

    assert result["claims_superseded"] == 1
    assert result["pointer_writes"] == 1
    assert session.committed

    status_updates = [u for u in session.updates if "status = 'superseded'" in u[0]]
    pointer_updates = [u for u in session.updates if "supersedes_claim_id" in u[0]]
    assert len(status_updates) == 1
    assert status_updates[0][1]["claim_id"] == "claim-early"
    assert len(pointer_updates) == 1
    assert pointer_updates[0][1] == {"earlier_id": "claim-early", "final_id": "claim-late"}
    # Pointer write must never clobber an existing lineage pointer.
    assert "supersedes_claim_id IS NULL" in pointer_updates[0][0]


def test_distinct_vertices_are_left_to_vertex_level_pass():
    """Claims resolved to DIFFERENT grace_ids are the vertex-pair case —
    claim-level supersession must not touch them."""
    rows = [
        _claim("c1", "email:<m0@ex.com>", "bid-1", {"amount": "18500"}),
        _claim("c2", "email:<m1@ex.com>", "bid-2", {"amount": "15800"}),
    ]
    session = _FakeSession(rows)

    result = apply_claim_level_supersession(session, "<m0@ex.com>", MEMBERS)

    assert result["claims_superseded"] == 0
    assert session.updates == []
    assert not session.committed


def test_same_message_claims_have_no_ordering_and_are_skipped():
    """Two claims from the SAME message cannot be ordered earlier/later —
    out of scope, no writes."""
    rows = [
        _claim("c1", "email:<m0@ex.com>", "bid-merged", {"amount": "18500"}),
        _claim("c2", "email:<m0@ex.com>", "bid-merged", {"amount": "15800"}),
    ]
    session = _FakeSession(rows)

    result = apply_claim_level_supersession(session, "<m0@ex.com>", MEMBERS)

    assert result["claims_superseded"] == 0
    assert session.updates == []


def test_refinement_between_merged_claims_does_not_supersede():
    """Refinement (later value contains earlier) is not a contradiction."""
    rows = [
        _claim("c1", "email:<m0@ex.com>", "org-merged", {"address": "12 Main St"}),
        _claim("c2", "email:<m1@ex.com>", "org-merged", {"address": "12 Main St, Springfield"}),
    ]
    session = _FakeSession(rows)

    result = apply_claim_level_supersession(session, "<m0@ex.com>", MEMBERS)

    assert result["claims_superseded"] == 0
    assert session.updates == []


def test_dry_run_computes_but_never_writes():
    rows = [
        _claim("claim-early", "email:<m0@ex.com>", "bid-merged", {"amount": "18500"}),
        _claim("claim-late", "email:<m1@ex.com>", "bid-merged", {"amount": "15800"}),
    ]
    session = _FakeSession(rows)

    result = apply_claim_level_supersession(session, "<m0@ex.com>", MEMBERS, dry_run=True)

    assert result["claims_superseded"] == 1
    assert result["pointer_writes"] == 0
    assert session.updates == []
    assert not session.committed


def test_empty_members_and_no_rows_are_noops():
    session = _FakeSession([])
    assert apply_claim_level_supersession(session, "<t>", [])["claims_superseded"] == 0
    assert apply_claim_level_supersession(session, "<t>", MEMBERS)["claims_superseded"] == 0
    assert session.updates == []
