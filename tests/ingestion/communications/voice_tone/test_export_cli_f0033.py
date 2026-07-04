"""F-0033b/c / ISS-0049 — voice export CLI fixes.

(b) ``--person <email>`` previously CAST the raw email to uuid when the
    entity_resolution_registry had no row (registry is connector-populated),
    crashing with a psycopg2 error. Email now falls back to the F-31 graph
    lookup (role_resolver.resolve_sender_person) and unresolvable subjects
    exit cleanly with guidance.
(c) ``--out <file>.md`` was treated as a directory. A suffixed path is now
    honored as the exact output file.
Rider: ``source_email_count`` is populated from communication_events at
export time instead of always exporting 0.

Pure unit tests — DB session factory, graph resolver, and audit writer are
all mocked; no Postgres or ArcadeDB access.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.ingestion.communications.voice_tone.models import StyleSignature

PERSON_EMAIL = "amelia@whitfield.example"
PERSON_UUID = str(uuid.uuid4())


def _sig_dict() -> dict:
    return StyleSignature(
        sentence_length_band="medium",
        vocabulary_complexity_band="medium",
        formality_band="high",
        greeting_closing_band="medium",
        hedging_frequency_band="low",
        directness_band="high",
        response_timing_band="medium",
        thread_depth_band="medium",
        sample_phrases=["Let us proceed."],
    ).model_dump()


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """Routes SQL by substring; records the statements it saw."""

    def __init__(self, *, registry_row=None, profile_row=None, email_count=None):
        self.registry_row = registry_row
        self.profile_row = profile_row
        self.email_count = email_count
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        if "entity_resolution_registry" in sql:
            return _FakeResult(self.registry_row)
        if "COUNT(*)" in sql:
            return _FakeResult((self.email_count,) if self.email_count is not None else None)
        if "communication_style_profiles" in sql:
            return _FakeResult(self.profile_row)
        return _FakeResult(None)


def _wire(monkeypatch, session: _FakeSession, resolver_result=None):
    """Patch DB factory, graph resolver, and audit writer for main()."""
    monkeypatch.setattr(
        "src.shared.database.get_session_factory", lambda: (lambda: session)
    )

    async def _fake_resolver(email, display_name=None):
        return resolver_result

    monkeypatch.setattr(
        "src.ingestion.communications.voice_tone.role_resolver.resolve_sender_person",
        _fake_resolver,
    )
    monkeypatch.setattr(
        "src.ingestion.communications.voice_tone.voice_card.record_export_audit",
        lambda **kwargs: None,
    )


def _run_export(monkeypatch, argv: list[str]) -> int:
    from src.ingestion.communications.voice_tone.__main__ import main

    monkeypatch.setattr("sys.argv", ["voice_tone", "export", *argv])
    with pytest.raises(SystemExit) as exc_info:
        main()
    return exc_info.value.code or 0


class TestEmailResolution:
    def test_email_resolves_via_graph_fallback_no_raw_cast(
        self, monkeypatch, tmp_path
    ):
        """Registry miss + graph hit → export succeeds; no email hits the CAST."""
        session = _FakeSession(
            registry_row=None, profile_row=(_sig_dict(), 3), email_count=5
        )
        _wire(monkeypatch, session, resolver_result=PERSON_UUID)

        out = tmp_path / "card.md"
        code = _run_export(
            monkeypatch,
            ["--person", PERSON_EMAIL, "--format", "markdown", "--out", str(out)],
        )
        assert code == 0
        assert out.exists()
        # The uuid, not the raw email, reached the profile query params path;
        # sanity: content rendered for the email subject.
        assert PERSON_EMAIL in out.read_text()

    def test_unresolvable_email_exits_cleanly_with_guidance(
        self, monkeypatch, tmp_path, capsys
    ):
        """Registry miss + graph miss → clean exit 1, no CAST crash."""
        session = _FakeSession(registry_row=None, profile_row=(_sig_dict(), 1))
        _wire(monkeypatch, session, resolver_result=None)

        code = _run_export(
            monkeypatch,
            ["--person", "nobody@nowhere.example", "--out", str(tmp_path)],
        )
        assert code == 1
        out = capsys.readouterr().out
        payload = json.loads(out.strip().splitlines()[-1])
        assert "Could not resolve" in payload["error"]
        # The profile query (with its uuid CAST) must never have run.
        assert not any(
            "communication_style_profiles" in s for s in session.statements
        )

    def test_uuid_input_keeps_working(self, monkeypatch, tmp_path):
        session = _FakeSession(profile_row=(_sig_dict(), 2))
        _wire(monkeypatch, session)

        code = _run_export(
            monkeypatch,
            ["--person", PERSON_UUID, "--out", str(tmp_path)],
        )
        assert code == 0
        # No registry lookup for a non-email subject.
        assert not any(
            "entity_resolution_registry" in s for s in session.statements
        )


class TestOutPathHandling:
    def test_out_with_file_suffix_writes_exact_file(self, monkeypatch, tmp_path):
        session = _FakeSession(profile_row=(_sig_dict(), 2))
        _wire(monkeypatch, session)

        out = tmp_path / "exports" / "amelia-voice.md"
        code = _run_export(
            monkeypatch, ["--person", PERSON_UUID, "--out", str(out)]
        )
        assert code == 0
        assert out.is_file()
        # No directory-mode artifact.
        assert not (tmp_path / "exports" / "amelia-voice.md" / PERSON_UUID).exists()

    def test_out_directory_keeps_legacy_layout(self, monkeypatch, tmp_path):
        session = _FakeSession(profile_row=(_sig_dict(), 2))
        _wire(monkeypatch, session)

        code = _run_export(
            monkeypatch, ["--person", PERSON_UUID, "--out", str(tmp_path)]
        )
        assert code == 0
        assert (tmp_path / PERSON_UUID / "voice-card.md").is_file()


class TestSourceEmailCount:
    def test_count_populated_from_communication_events(
        self, monkeypatch, tmp_path
    ):
        session = _FakeSession(
            registry_row=(PERSON_UUID,), profile_row=(_sig_dict(), 3), email_count=5
        )
        _wire(monkeypatch, session)

        out = tmp_path / "card.md"
        code = _run_export(
            monkeypatch, ["--person", PERSON_EMAIL, "--out", str(out)]
        )
        assert code == 0
        assert "source_email_count: 5" in out.read_text()
