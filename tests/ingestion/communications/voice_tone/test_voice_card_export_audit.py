"""F-36 regression: exporting a Voice Card must write the voice_card_exports
audit row and bump grace_voice_cards_exported_total. Previously record_export_audit
(a) was never called by the export CLI and (b) imported nonexistent src.shared.db
(so the INSERT failed silently even if called)."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from src.ingestion.communications.voice_tone import voice_card


def test_record_export_audit_executes_insert():
    session = MagicMock()

    @contextmanager
    def _factory_cm():
        yield session

    factory = MagicMock(side_effect=lambda: _factory_cm())

    with patch(
        "src.shared.database.get_session_factory", return_value=factory
    ):
        voice_card.record_export_audit(
            subject="edward@example.com",
            profile_version=7,
            fmt="markdown",
            redaction_applied=True,
            operator="op1",
        )

    # The INSERT into voice_card_exports must have executed and committed.
    assert session.execute.called
    sql = str(session.execute.call_args[0][0])
    assert "voice_card_exports" in sql
    params = session.execute.call_args[0][1]
    assert params["subject"] == "edward@example.com"
    assert params["pv"] == 7
    assert params["fmt"] == "markdown"
    assert session.commit.called


def test_record_export_audit_does_not_import_phantom_module():
    import inspect

    src = inspect.getsource(voice_card.record_export_audit)
    assert "src.shared.db import" not in src  # the F-34/F-36 phantom import
    assert "src.shared.database" in src
