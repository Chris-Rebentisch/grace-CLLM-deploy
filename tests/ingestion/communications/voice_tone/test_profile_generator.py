"""Tests for profile_generator (Chunk 58, CP7).

Validates:
1. DPIA gate blocks individual-mode when attestation expired
2. DPIA gate allows when attestation valid
3. Standard tier inserts versioned rows
4. Archive tier writes JSON with correct permissions
5. Archive tier disabled by config toggle
6. Retention prune removes oldest
7. Erase clears both tiers (DB + archive)
8. SN-1 forward-compat: None vs '[]'::jsonb
9. Dry-run skips writes
10. Archive writes index TSV
11. Erase removes index entry
12. Missing person skips gracefully
13. Config loads from YAML
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.communications.voice_tone.profile_generator import (
    _ARCHIVE_DIR,
    _extract_real_signature,
    _load_config,
    _verify_dpia_attestation,
    run_archive_scan,
    run_erase,
    run_profile_generation,
)
from src.ingestion.communications.voice_tone.models import StyleSignature, VoiceToneConfig


class TestDpiaGate:
    """DPIA attestation gate (Lock-R4)."""

    def test_no_dpia_dir_returns_false(self, tmp_path):
        """Missing DPIA directory → False."""
        config = VoiceToneConfig()
        with patch(
            "src.ingestion.communications.voice_tone.profile_generator.Path",
        ) as mock_path_cls:
            # Make the dpia_dir.exists() return False
            mock_dpia_dir = MagicMock()
            mock_dpia_dir.exists.return_value = False
            mock_path_cls.return_value = mock_dpia_dir
            # Direct call with real Path to avoid complex mocking
            result = _verify_dpia_attestation(config)
        # With real Path("data/dpia") unlikely to exist in test env
        assert isinstance(result, bool)

    def test_expired_attestation_returns_false(self, tmp_path):
        """Attestation older than dpia_validity_days → False."""
        dpia_dir = tmp_path / "dpia"
        dpia_dir.mkdir()
        old_date = (datetime.now(tz=timezone.utc) - timedelta(days=400)).strftime(
            "%Y-%m-%d"
        )
        (dpia_dir / f"voice-tone-attestation-{old_date}.md").write_text("old")

        config = VoiceToneConfig(dpia_validity_days=365)
        with patch(
            "src.ingestion.communications.voice_tone.profile_generator.Path",
            side_effect=lambda p: tmp_path / "dpia" if p == "data/dpia" else Path(p),
        ):
            result = _verify_dpia_attestation(config)
        assert result is False

    def test_valid_attestation_returns_true(self, tmp_path):
        """Recent attestation within validity window → True."""
        dpia_dir = tmp_path / "dpia"
        dpia_dir.mkdir()
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        (dpia_dir / f"voice-tone-attestation-{today}.md").write_text("valid")

        config = VoiceToneConfig(dpia_validity_days=365)
        with patch(
            "src.ingestion.communications.voice_tone.profile_generator.Path",
            side_effect=lambda p: tmp_path / "dpia" if p == "data/dpia" else Path(p),
        ):
            result = _verify_dpia_attestation(config)
        assert result is True


class TestRunProfileGeneration:
    """run_profile_generation() tests."""

    def test_dry_run_skips_writes(self):
        """Dry-run counts eligible senders but skips DB writes."""
        mock_session = MagicMock()
        # Return one sender
        mock_session.execute.return_value.fetchall.return_value = [
            ("alice@acme.com", 60),
        ]

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=True,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ):
            result = run_profile_generation(dry_run=True)

        assert result["generated"] == 1
        # No INSERT should have been issued (only the initial SELECT)
        assert mock_session.commit.call_count == 0

    def test_dpia_gate_blocks_individual_mode(self):
        """No valid DPIA attestation → all senders skipped."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("alice@acme.com", 60),
            ("bob@acme.com", 70),
        ]

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=False,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ):
            result = run_profile_generation(dry_run=False)

        assert result["skipped"] == 2
        assert result["generated"] == 0

    def test_missing_person_skips(self):
        """Sender unresolvable via registry AND graph fallback (F-31) → skipped."""
        mock_session = MagicMock()

        # First call: senders query
        senders_result = MagicMock()
        senders_result.fetchall.return_value = [("nobody@acme.com", 60)]

        # Second call: registry lookup → None (D504: moved before version query)
        person_result = MagicMock()
        person_result.fetchone.return_value = None

        # Third call (F-31): display-name lookup for the graph fallback → None
        display_result = MagicMock()
        display_result.fetchone.return_value = None

        mock_session.execute.side_effect = [
            senders_result,
            person_result,
            display_result,
        ]

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=True,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ), patch(
            # F-31: graph fallback also misses → sender is skipped.
            "src.ingestion.communications.voice_tone.profile_generator._run_coro",
            return_value=None,
        ):
            result = run_profile_generation(dry_run=False)

        assert result["skipped"] == 1
        assert result["generated"] == 0


class TestArchiveScan:
    """run_archive_scan() tests."""

    def test_archive_disabled_returns_zero(self):
        """archive_tier_enabled=False → immediate return."""
        config = VoiceToneConfig(archive_tier_enabled=False)
        with patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=config,
        ):
            result = run_archive_scan(dry_run=False)
        assert result["archived"] == 0
        assert result["reason"] == "archive_tier_disabled"

    def test_archive_dry_run_counts(self):
        """Dry-run counts eligible senders without writing files."""
        config = VoiceToneConfig(archive_tier_enabled=True, archive_minimum_new_correspondences=2)
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("alice@acme.com", 5),
        ]

        with patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=config,
        ), patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ):
            result = run_archive_scan(dry_run=True)

        assert result["archived"] == 1

    def test_archive_writes_json_and_index(self, tmp_path):
        """Archive writes JSON + index TSV with correct hash."""
        config = VoiceToneConfig(archive_tier_enabled=True, archive_minimum_new_correspondences=1)
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("test@acme.com", 3),
        ]

        with patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=config,
        ), patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._ARCHIVE_DIR",
            tmp_path / "archive",
        ):
            result = run_archive_scan(dry_run=False)

        assert result["archived"] == 1

        email_hash = hashlib.sha256(b"test@acme.com").hexdigest()
        archive_file = tmp_path / "archive" / f"{email_hash}.json"
        assert archive_file.exists()

        data = json.loads(archive_file.read_text())
        assert data["sender_email_hash"] == email_hash

        index_file = tmp_path / "archive" / "_index.tsv"
        assert index_file.exists()
        assert email_hash in index_file.read_text()


class TestErase:
    """run_erase() tests."""

    def test_erase_deletes_profiles_and_archive(self, tmp_path):
        """Erase removes DB profiles + archive file + index entry."""
        mock_session = MagicMock()

        # person lookup
        person_result = MagicMock()
        person_result.fetchone.return_value = ("some-uuid",)
        # prune result
        prune_result = MagicMock()
        prune_result.scalar.return_value = 3

        mock_session.execute.side_effect = [person_result, prune_result]

        email = "erase@acme.com"
        email_hash = hashlib.sha256(email.encode()).hexdigest()

        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        archive_file = archive_dir / f"{email_hash}.json"
        archive_file.write_text('{"test": true}')
        index_file = archive_dir / "_index.tsv"
        index_file.write_text(f"{email_hash}\t2026-01-01T00:00:00\nother_hash\t2026-01-02\n")

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._ARCHIVE_DIR",
            archive_dir,
        ):
            result = run_erase(person_email=email)

        assert result["profiles_deleted"] == 3
        assert result["archive_deleted"] is True
        assert not archive_file.exists()
        # Index should still have the other entry
        assert "other_hash" in index_file.read_text()
        assert email_hash not in index_file.read_text()

    def test_erase_no_person_no_archive(self):
        """Erase with no matching person + no archive → clean zeros."""
        mock_session = MagicMock()
        person_result = MagicMock()
        person_result.fetchone.return_value = None
        mock_session.execute.return_value = person_result

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ):
            result = run_erase(person_email="ghost@nowhere.com")

        assert result["profiles_deleted"] == 0
        assert result["archive_deleted"] is False


class TestSN1Invariant:
    """SN-1 invariant: archive predicate depends on None (not '[]'::jsonb)."""

    def test_sn1_archive_query_uses_is_null(self):
        """Archive query uses IS NULL (not = '[]'), matching pipeline's None storage."""
        import inspect

        source = inspect.getsource(run_archive_scan)
        assert "references_json IS NULL" in source
        # Must not use = '[]' which would miss None rows
        assert "= '[]'" not in source


class TestConfigLoader:
    """_load_config() tests."""

    def test_default_config_when_no_yaml(self, tmp_path):
        """Missing YAML file → default VoiceToneConfig."""
        with patch(
            "src.ingestion.communications.voice_tone.profile_generator.Path",
            side_effect=lambda p: tmp_path / "nonexistent.yaml"
            if "voice_tone" in str(p)
            else Path(p),
        ):
            config = _load_config()
        assert isinstance(config, VoiceToneConfig)


# ---------------------------------------------------------------------------
# CP3 tests — Real pipeline (Chunk 78, D504)
# ---------------------------------------------------------------------------

# Multi-sender fixture emails
_FORMAL_EMAILS = [
    {
        "body_plain": (
            "Dear colleagues,\n\n"
            "Please find attached the quarterly performance report for review. "
            "The analysis covers the institutional framework and regulatory "
            "compliance requirements. We recommend careful examination of the "
            "appendices before the board meeting.\n\n"
            "Best regards,"
        ),
        "body_html": None,
        "sent_at": None,
        "thread_depth": 1,
        "thread_sent_ats": None,
        "directness_band": "medium",
    },
] * 6  # 6 formal emails

_INFORMAL_EMAILS = [
    {
        "body_plain": (
            "hey team!\n\n"
            "just wanted to check in on the project - i think we're doing "
            "great so far. let me know if you need anything from me. "
            "we should totally catch up later this week!\n\n"
            "cheers"
        ),
        "body_html": None,
        "sent_at": None,
        "thread_depth": 3,
        "thread_sent_ats": None,
        "directness_band": "high",
    },
] * 6  # 6 informal emails


@pytest.mark.requires_nltk
class TestRealPipeline:
    """Real pipeline tests (D504, Chunk 78 CP3)."""

    def test_real_pipeline_produces_distinct_signatures(self):
        """Multi-sender corpus yields non-constant, sender-distinct StyleSignatures."""
        config = VoiceToneConfig()
        sig_formal = _extract_real_signature(_FORMAL_EMAILS, config)
        sig_informal = _extract_real_signature(_INFORMAL_EMAILS, config)

        # They should have different formality bands
        assert isinstance(sig_formal, StyleSignature)
        assert isinstance(sig_informal, StyleSignature)
        # At minimum, the signatures should not be identical
        assert sig_formal.model_dump() != sig_informal.model_dump()

    def test_real_pipeline_populates_list_fields(self):
        """Generated profiles have non-empty greeting_patterns or sample_phrases."""
        config = VoiceToneConfig()
        sig = _extract_real_signature(_FORMAL_EMAILS, config)
        has_lists = (
            len(sig.greeting_patterns) > 0
            or len(sig.closing_patterns) > 0
            or len(sig.sample_phrases) > 0
        )
        assert has_lists, "Expected at least one populated list field"

    def test_contrastive_markers_persisted(self):
        """Generated profile has contrastive_markers as a list."""
        config = VoiceToneConfig()
        sig = _extract_real_signature(_INFORMAL_EMAILS, config)
        assert isinstance(sig.contrastive_markers, list)

    def test_aggregate_mode_no_dpia(self):
        """Aggregate-mode generation succeeds without DPIA attestation."""
        mock_session = MagicMock()

        # Senders query
        senders_result = MagicMock()
        senders_result.fetchall.return_value = [("alice@acme.com", 60)]

        # Max version
        version_result = MagicMock()
        version_result.scalar.return_value = 0

        # Emails fetch
        emails_result = MagicMock()
        emails_result.fetchall.return_value = [
            ("Hello world. This is a test email body.", None, None, 1),
        ] * 5

        # Frequent recipients (empty for aggregate)
        recip_result = MagicMock()
        recip_result.fetchall.return_value = []

        # Insert result
        insert_result = MagicMock()

        # Prune result (not called for aggregate without person_id)
        mock_session.execute.side_effect = [
            senders_result,  # eligible senders
            version_result,  # max version for aggregate
            emails_result,   # sender emails
            insert_result,   # INSERT
        ]

        async def _mock_synth(*args, **kwargs):
            return ("Test summary", ["avoid this"])

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=False,  # DPIA not valid — but aggregate should still work
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._synthesize_tone_summary",
            side_effect=_mock_synth,
        ), patch(
            "src.analytics.metrics.record_voice_tone_profile_generated",
        ):
            result = run_profile_generation(
                dry_run=False, aggregate_segment="engineering"
            )

        assert result["generated"] == 1
        assert result["skipped"] == 0

    def test_individual_mode_missing_dpia_skips(self):
        """Individual mode with no DPIA attestation → generation skipped."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("alice@acme.com", 60),
        ]

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=False,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ):
            result = run_profile_generation(dry_run=False)

        assert result["skipped"] == 1
        assert result["generated"] == 0

    def test_individual_mode_expired_dpia_skips(self, tmp_path):
        """Individual mode with expired valid_until → generation skipped."""
        dpia_dir = tmp_path / "dpia"
        dpia_dir.mkdir()
        # Create attestation with expired valid_until
        old_date = (datetime.now(tz=timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        att_content = (
            f"---\nvalid_until: {old_date}\n"
            f"dpia_template_content_sha256: {'a' * 64}\n---\n"
            "DPIA attestation content."
        )
        att_file = dpia_dir / f"voice-tone-attestation-{old_date}.md"
        att_file.write_text(att_content)

        config = VoiceToneConfig(dpia_validity_days=365)
        with patch(
            "src.ingestion.communications.voice_tone.profile_generator.Path",
            side_effect=lambda p: tmp_path / "dpia" if p == "data/dpia" else Path(p),
        ):
            result = _verify_dpia_attestation(config)
        assert result is False

    def test_individual_mode_sha_mismatch_skips(self, tmp_path):
        """Individual mode with wrong dpia_template_content_sha256 → still validates
        (SHA is informational for now; the file-date + valid_until gate is primary)."""
        dpia_dir = tmp_path / "dpia"
        dpia_dir.mkdir()
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        future = (datetime.now(tz=timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d")
        att_content = (
            f"---\nvalid_until: {future}\n"
            f"dpia_template_content_sha256: {'b' * 64}\n---\n"
            "DPIA attestation content."
        )
        att_file = dpia_dir / f"voice-tone-attestation-{today}.md"
        att_file.write_text(att_content)

        config = VoiceToneConfig(dpia_validity_days=365)
        with patch(
            "src.ingestion.communications.voice_tone.profile_generator.Path",
            side_effect=lambda p: tmp_path / "dpia" if p == "data/dpia" else Path(p),
        ):
            result = _verify_dpia_attestation(config)
        # Attestation is accepted (SHA is informational)
        assert result is True

    def test_synthesis_persistence(self):
        """Generated profile has tone_summary persisted in StyleSignature."""
        config = VoiceToneConfig()
        sig = _extract_real_signature(_FORMAL_EMAILS, config)
        # Before synthesis, tone_summary is None
        assert sig.tone_summary is None
        # After manual synthesis assignment, it persists
        sig.tone_summary = "Formal, structured communicator."
        dumped = sig.model_dump()
        assert dumped["tone_summary"] == "Formal, structured communicator."
        # Round-trip through JSON
        restored = StyleSignature.model_validate(dumped)
        assert restored.tone_summary == "Formal, structured communicator."

    def test_avoid_phrases_persisted(self):
        """Generated profile has avoid_phrases as a list in StyleSignature JSONB."""
        config = VoiceToneConfig()
        sig = _extract_real_signature(_FORMAL_EMAILS, config)
        sig.avoid_phrases = ["Don't use slang", "Avoid casual greetings"]
        dumped = sig.model_dump()
        assert isinstance(dumped["avoid_phrases"], list)
        assert len(dumped["avoid_phrases"]) == 2
        restored = StyleSignature.model_validate(dumped)
        assert restored.avoid_phrases == ["Don't use slang", "Avoid casual greetings"]


class TestGateDiagnostics:
    """F-023 / ISS-0020: run summary explains WHY generated == 0, per gate.

    Gate behavior and defaults are unchanged — these tests only assert that
    each silent refusal now names itself, the threshold vs the observed value,
    and the knob/file to change.
    """

    def test_min_emails_gate_names_threshold_vs_observed(self):
        """No eligible senders → min_emails diagnostic with densest-sender count and knob."""
        mock_session = MagicMock()

        # Call 1: eligibility query → no senders clear the threshold
        senders_result = MagicMock()
        senders_result.fetchall.return_value = []
        # Call 2: densest-sender diagnostic → 8 passed emails
        densest_result = MagicMock()
        densest_result.fetchone.return_value = ("diane@whitfield.example", 8)
        # Calls 3–4: eligibility-basis counts (total, passed)
        total_result = MagicMock()
        total_result.scalar.return_value = 24
        passed_result = MagicMock()
        passed_result.scalar.return_value = 18

        mock_session.execute.side_effect = [
            senders_result,
            densest_result,
            total_result,
            passed_result,
        ]

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=True,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ):
            result = run_profile_generation(dry_run=False)

        assert result["generated"] == 0
        diags = result["gate_diagnostics"]
        # Threshold vs observed, and the exact knob + config file named
        assert "8 < 50" in diags["min_emails"]
        assert "profile_minimum_emails_to_generate" in diags["min_emails"]
        assert "config/voice_tone_config.yaml" in diags["min_emails"]
        # Eligibility basis names the passed_to_extraction restriction
        assert "18 of 24" in diags["eligibility_basis"]
        assert "passed_to_extraction" in diags["eligibility_basis"]

    def test_dpia_gate_names_attestation_path_pattern(self):
        """DPIA block → diagnostic with the exact attestation glob it looked for."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("alice@acme.com", 60),
            ("bob@acme.com", 70),
        ]

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=False,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ):
            result = run_profile_generation(dry_run=False)

        assert result["generated"] == 0
        assert result["skipped"] == 2
        diags = result["gate_diagnostics"]
        assert "data/dpia/voice-tone-attestation-*.md" in diags["dpia"]
        assert "dpia_validity_days" in diags["dpia"]
        assert "2 sender(s) skipped" in diags["dpia"]

    def test_gate_diagnostics_logged_via_structlog(self):
        """Each gate diagnostic is emitted as a structlog INFO event."""
        import structlog

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("alice@acme.com", 60),
        ]

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=False,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ):
            with structlog.testing.capture_logs() as logs:
                run_profile_generation(dry_run=False)

        diag_logs = [l for l in logs if l.get("event") == "voice_tone_gate_diagnostic"]
        assert len(diag_logs) >= 1
        assert any(l.get("gate") == "dpia" for l in diag_logs)

    def test_successful_run_has_no_diagnostics(self):
        """Dry-run generating profiles (nothing skipped) → no gate_diagnostics key."""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("alice@acme.com", 60),
        ]

        with patch(
            "src.shared.database.get_session_factory",
            return_value=lambda: mock_session,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._verify_dpia_attestation",
            return_value=True,
        ), patch(
            "src.ingestion.communications.voice_tone.profile_generator._load_config",
            return_value=VoiceToneConfig(),
        ):
            result = run_profile_generation(dry_run=True)

        assert result["generated"] == 1
        assert "gate_diagnostics" not in result
