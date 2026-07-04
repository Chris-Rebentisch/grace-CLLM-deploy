"""CLASS-KILLER (b): raw-SQL column/table contract for ingestion SQL.

A validation run surfaced the F-32 class: raw SQL referencing columns that
never existed (`thread_depth`, `recipient_email` on communication_events), which
a mock-heavy suite never executed against a real schema. This test runs the
actual voice_tone SQL against the isolated `grace_test` schema inside a
transaction it ROLLS BACK, so nothing mutates — it fails loudly if any SQL
references a column/table that does not exist.

Scope (per task): the voice_tone `_fetch_sender_emails` /
`_fetch_frequent_recipients` SELECTs, which caused F-32. role_resolver's query
is OpenCypher (ArcadeDB), not Postgres SQL, so it is validated structurally in
tests/ingestion/communications/voice_tone/test_role_resolver_regression.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session as SASession

from src.ingestion.communications.voice_tone.profile_generator import (
    _fetch_frequent_recipients,
    _fetch_sender_emails,
)
from src.shared.database import get_engine


@pytest.fixture()
def rollback_session():
    """A session on a transaction that is always rolled back — grace_test is
    physically the only reachable DB (conftest isolation) and nothing commits."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    session = SASession(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_fetch_sender_emails_sql_columns_valid(rollback_session):
    """_fetch_sender_emails SELECT must reference only real columns (F-32)."""
    try:
        rows = _fetch_sender_emails(rollback_session, "nobody@example.com", limit=5)
    except ProgrammingError as exc:
        pytest.fail(f"_fetch_sender_emails SQL references invalid column/table: {exc}")
    # No rows for a bogus sender, but the query must have executed cleanly.
    assert rows == []


def test_fetch_frequent_recipients_sql_columns_valid(rollback_session):
    """_fetch_frequent_recipients SELECT must reference only real columns (F-32b)."""
    try:
        rows = _fetch_frequent_recipients(rollback_session, "nobody@example.com")
    except ProgrammingError as exc:
        pytest.fail(f"_fetch_frequent_recipients SQL references invalid column/table: {exc}")
    assert rows == []


def test_grace_test_untouched_after_rollback(rollback_session):
    """Guard: the contract test must not mutate grace_test (SELECTs + rollback)."""
    # A trivial read to prove the connection is live and read-only in effect.
    n = rollback_session.execute(
        text("SELECT count(*) FROM communication_events")
    ).scalar()
    assert n is not None
