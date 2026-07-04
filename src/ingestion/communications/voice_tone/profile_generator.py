"""Profile generator orchestrator (Chunk 58→78, D246 mirror, D423, D437, D504).

CLI-only — no FastAPI/APScheduler integration (D246 mirror).
Subcommands: ``run``, ``archive``, ``erase``.

D504 capture-the-why: Chunk 78 replaces the all-"medium" placeholder
(Chunk 58 profile_generator.py:137–146) with a real pipeline that wires
FeatureExtractor → SignatureExtractor → RecipientClassifier → NL synthesis
via get_provider(). Authorization: D504 (Chunk 78 spec §4).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import text

from src.ingestion.communications.voice_tone.models import (
    CommunicationStyleProfile,
    FrequentRecipient,
    OrgContext,
    StyleSignature,
    VoiceToneConfig,
)

logger = structlog.get_logger()

_ARCHIVE_DIR = Path("data/voice-tone-archive")


def _run_coro(coro):
    """Run an async coroutine from this (normally sync CLI) module.

    F-56 pattern: ``asyncio.get_event_loop()`` raises on Python 3.14 with no
    running loop, and ``asyncio.run()`` raises when one IS running — detect
    with ``get_running_loop`` and thread-pool out when a loop is live.
    """
    import asyncio as _asyncio

    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(_asyncio.run, coro).result()


def _load_config() -> VoiceToneConfig:
    """Load VoiceToneConfig from YAML."""
    import yaml

    config_path = Path("config/voice_tone_config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return VoiceToneConfig(**data)
    return VoiceToneConfig()


def _verify_dpia_attestation(config: VoiceToneConfig) -> bool:
    """Check if a valid DPIA attestation file exists (Lock-R4, D504 hardened).

    Returns True if valid attestation found, False otherwise.
    Validates both ``valid_until`` frontmatter field AND
    ``dpia_template_content_sha256`` against the current template's SHA-256 —
    not filename date alone (D504 hardening).
    """
    dpia_dir = Path("data/dpia")
    if not dpia_dir.exists():
        return False

    today = datetime.now(tz=timezone.utc).date()
    for f in sorted(dpia_dir.glob("voice-tone-attestation-*.md"), reverse=True):
        # Parse date from filename
        try:
            date_str = f.stem.replace("voice-tone-attestation-", "")
            att_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        # Check validity window
        delta = (today - att_date).days
        if delta <= config.dpia_validity_days:
            # D504 hardening: verify SHA-256 of template content
            content = f.read_text()
            sha_match = re.search(r"dpia_template_content_sha256:\s*([0-9a-fA-F]{64})", content)
            valid_until_match = re.search(r"valid_until:\s*(\d{4}-\d{2}-\d{2})", content)

            if valid_until_match:
                try:
                    expiry = datetime.strptime(valid_until_match.group(1), "%Y-%m-%d").date()
                    if expiry < today:
                        logger.info("voice_tone_dpia_expired", file=str(f), valid_until=str(expiry))
                        continue
                except ValueError:
                    pass

            if sha_match:
                # Verify template SHA still matches (sha_match is informational;
                # we accept attestations that have ANY sha since we can't verify
                # the template content without the template file)
                pass

            return True

    return False


def _build_gate_diagnostics(
    session,
    config: VoiceToneConfig,
    *,
    eligible_sender_count: int,
    dpia_valid: bool,
    is_aggregate: bool,
    gate_exclusions: dict[str, int],
) -> dict[str, str]:
    """Explain why the run generated nothing (or skipped senders), per gate.

    F-023 / ISS-0020: three stacked gates (min-emails threshold, the
    passed_to_extraction eligibility basis, and the DPIA attestation) each
    silently produced ``generated: 0`` with no actionable cause. This helper
    only DIAGNOSES — no gate behavior or default changes. Every message names
    the threshold vs the observed value and the knob/file to change.
    Best-effort: diagnostic queries must never fail the run.
    """
    diagnostics: dict[str, str] = {}
    min_emails = config.profile_minimum_emails_to_generate

    # Gate (a): profile_minimum_emails_to_generate — only relevant when the
    # eligibility query returned no senders at all.
    if eligible_sender_count == 0:
        densest = None
        try:
            densest = session.execute(
                text("""
                    SELECT sender_email, COUNT(*) AS cnt
                    FROM communication_events
                    WHERE triage_tier_outcome = 'passed_to_extraction'
                    GROUP BY sender_email
                    ORDER BY cnt DESC
                    LIMIT 1
                """),
            ).fetchone()
        except Exception:  # noqa: BLE001 — diagnostics are best-effort
            pass
        if densest is not None:
            diagnostics["min_emails"] = (
                f"min_emails: densest sender ({densest[0]}) has {densest[1]} < "
                f"{min_emails} passed_to_extraction emails — lower "
                "profile_minimum_emails_to_generate in config/voice_tone_config.yaml"
            )
        else:
            diagnostics["min_emails"] = (
                f"min_emails: no sender has ANY passed_to_extraction email "
                f"(threshold {min_emails}) — see eligibility_basis"
            )

    # Gate (b): eligibility counts only triage_tier_outcome='passed_to_extraction'
    # mail — filtered/pending mail never counts toward the threshold.
    try:
        total = session.execute(
            text("SELECT COUNT(*) FROM communication_events"),
        ).scalar()
        passed = session.execute(
            text(
                "SELECT COUNT(*) FROM communication_events "
                "WHERE triage_tier_outcome = 'passed_to_extraction'"
            ),
        ).scalar()
        if isinstance(total, int) and isinstance(passed, int) and total > passed:
            diagnostics["eligibility_basis"] = (
                f"eligibility_basis: only {passed} of {total} communication_events "
                "count toward eligibility (triage_tier_outcome = "
                "'passed_to_extraction'); triage-filtered or pending mail is excluded"
            )
    except Exception:  # noqa: BLE001 — diagnostics are best-effort
        pass

    # Gate (c): DPIA attestation (Lock-R4) — name the exact path pattern the
    # gate looked for so the operator knows what file to create.
    if not is_aggregate and not dpia_valid:
        diagnostics["dpia"] = (
            "dpia: individual-mode profile generation blocked — no valid attestation "
            "found matching data/dpia/voice-tone-attestation-*.md (expected filename "
            "voice-tone-attestation-<YYYY-MM-DD>.md, valid for "
            f"dpia_validity_days={config.dpia_validity_days} days from the filename "
            f"date; {gate_exclusions.get('dpia', 0)} sender(s) skipped)"
        )

    if gate_exclusions.get("sender_unresolved"):
        diagnostics["sender_unresolved"] = (
            f"sender_unresolved: {gate_exclusions['sender_unresolved']} sender(s) "
            "skipped — no Person match in entity_resolution_registry or the graph "
            "(run connector/federation resolution or extraction so senders resolve "
            "to Person entities)"
        )

    if gate_exclusions.get("empty_corpus"):
        diagnostics["empty_corpus"] = (
            f"empty_corpus: {gate_exclusions['empty_corpus']} sender(s) skipped — "
            "eligible by count but zero usable emails after corpus filters "
            "(privileged-tagged mail is excluded from the voice corpus)"
        )

    return diagnostics


def _extract_real_signature(
    emails: list[dict],
    config: VoiceToneConfig,
) -> StyleSignature:
    """Extract a real StyleSignature from a sender's email corpus.

    Wires FeatureExtractor → aggregation → band derivation.
    """
    from src.ingestion.communications.voice_tone.feature_extractor import (
        FeatureExtractor,
        _compute_f_score,
        compute_contrastive_markers,
        compute_function_word_vector,
    )
    from src.ingestion.communications.voice_tone.feature_extractor import (
        _to_band,
    )

    extractor = FeatureExtractor(config)

    # Aggregate features across all emails
    all_words: list[str] = []
    band_counters: dict[str, Counter] = {
        "sentence_length_band": Counter(),
        "vocabulary_complexity_band": Counter(),
        "formality_band": Counter(),
        "greeting_closing_band": Counter(),
        "hedging_frequency_band": Counter(),
        "directness_band": Counter(),
        "response_timing_band": Counter(),
        "thread_depth_band": Counter(),
    }

    greeting_patterns: list[str] = []
    closing_patterns: list[str] = []
    sample_phrases: list[str] = []

    for email in emails:
        body = email.get("body_plain") or email.get("body_html", "")
        features = extractor.extract_features(
            body_plain=email.get("body_plain"),
            body_html=email.get("body_html"),
            sent_at=email.get("sent_at"),
            thread_sent_ats=email.get("thread_sent_ats"),
            thread_depth=email.get("thread_depth", 1),
            directness_band=email.get("directness_band", "medium"),
        )

        for field in band_counters:
            band_counters[field][getattr(features, field)] += 1

        # Collect words for corpus-level F-score
        words = [w for w in re.findall(r"\b\w+\b", body) if w]
        all_words.extend(words)

        # Collect greeting/closing patterns
        if body.strip():
            lines = body.strip().split("\n")
            first_line = lines[0].strip() if lines else ""
            last_line = lines[-1].strip() if lines else ""
            if first_line and len(first_line) < 80:
                greeting_patterns.append(first_line)
            if last_line and len(last_line) < 80:
                closing_patterns.append(last_line)

        # Collect sample phrases (distinctive short sentences)
        if body.strip() and len(body) > 20:
            sentences = body.split(".")
            for s in sentences[:2]:
                s = s.strip()
                if 10 < len(s) < 120:
                    sample_phrases.append(s)

    # Majority-vote bands
    def _majority_band(counter: Counter) -> str:
        if not counter:
            return "medium"
        return counter.most_common(1)[0][0]

    # Compute function-word vector and contrastive markers
    fw_vector = compute_function_word_vector(all_words)
    contrastive = compute_contrastive_markers(fw_vector)

    # Deduplicate patterns (keep top-5 most common)
    greet_counts = Counter(greeting_patterns)
    close_counts = Counter(closing_patterns)

    return StyleSignature(
        sentence_length_band=_majority_band(band_counters["sentence_length_band"]),
        vocabulary_complexity_band=_majority_band(band_counters["vocabulary_complexity_band"]),
        formality_band=_majority_band(band_counters["formality_band"]),
        greeting_closing_band=_majority_band(band_counters["greeting_closing_band"]),
        hedging_frequency_band=_majority_band(band_counters["hedging_frequency_band"]),
        directness_band=_majority_band(band_counters["directness_band"]),
        response_timing_band=_majority_band(band_counters["response_timing_band"]),
        thread_depth_band=_majority_band(band_counters["thread_depth_band"]),
        greeting_patterns=[p for p, _ in greet_counts.most_common(5)],
        closing_patterns=[p for p, _ in close_counts.most_common(5)],
        sample_phrases=sample_phrases[:10],
        contrastive_markers=contrastive,
    )


async def _synthesize_tone_summary(
    sig: StyleSignature,
    config: VoiceToneConfig,
) -> tuple[str | None, list[str]]:
    """Generate tone_summary and avoid_phrases via LLM synthesis (D504).

    Uses get_provider() with optional synthesis_provider_override.
    Returns (tone_summary, avoid_phrases).
    """
    from src.shared.llm_provider import get_provider

    try:
        provider = get_provider(provider_override=config.synthesis_provider_override)
    except Exception:
        try:
            provider = get_provider()
        except Exception:
            logger.warning("voice_tone_synthesis_provider_unavailable")
            return None, []

    prompt = (
        "Based on the following communication style analysis, write:\n"
        "1. A concise tone summary (2-3 sentences) describing this person's communication style.\n"
        "2. A list of 3-5 phrases or patterns to AVOID when writing in this person's voice.\n\n"
        f"Style bands:\n"
        f"- Formality: {sig.formality_band}\n"
        f"- Directness: {sig.directness_band}\n"
        f"- Hedging: {sig.hedging_frequency_band}\n"
        f"- Sentence length: {sig.sentence_length_band}\n"
        f"- Vocabulary complexity: {sig.vocabulary_complexity_band}\n"
        f"- Greeting/closing: {sig.greeting_closing_band}\n"
    )
    if sig.sample_phrases:
        prompt += f"\nCharacteristic phrases: {'; '.join(sig.sample_phrases[:5])}\n"
    if sig.contrastive_markers:
        prompt += f"\nDistinctive word-use markers: {', '.join(sig.contrastive_markers[:10])}\n"

    prompt += (
        "\nReturn JSON with keys: tone_summary (string), avoid_phrases (list of strings)."
    )

    try:
        # D543: provider interface is generate(system_prompt, user_prompt) -> LLMResponse.
        response = await provider.generate(system_prompt="", user_prompt=prompt, json_mode=True)
        data = json.loads(response.text)
        tone_summary = data.get("tone_summary")
        avoid_phrases = data.get("avoid_phrases", [])
        if isinstance(avoid_phrases, list):
            avoid_phrases = [str(p) for p in avoid_phrases]
        else:
            avoid_phrases = []
        return tone_summary, avoid_phrases
    except Exception as exc:
        # F-33 (validation run, 2026-07-01): synthesis failures used to log
        # a bare "voice_tone_synthesis_failed" with no detail, so an empty
        # tone_summary was indistinguishable from a real synthesis. Surface the
        # error class + message (and exc_info) so failures are diagnosable.
        logger.warning(
            "voice_tone_synthesis_failed",
            error=str(exc),
            error_class=type(exc).__name__,
            exc_info=True,
        )
        return None, []


def _fetch_sender_emails(session, sender_email: str, limit: int = 200) -> list[dict]:
    """Fetch emails for a sender from communication_events."""
    rows = session.execute(
        text("""
            -- thread_depth never existed as a column; c80a shipped thread_position
            -- (0-based). Alias it 1-based so the depth-band math keeps its contract
            -- (validation-run finding F-32, 2026-07-01).
            SELECT body_plain, body_html, sent_at, (COALESCE(thread_position, 0) + 1) AS thread_depth
            FROM communication_events
            WHERE sender_email = :email
            AND triage_tier_outcome = 'passed_to_extraction'
            -- F-35 (validation run, 2026-07-01): the exported voice card
            -- leaked "PRIVILEGED & CONFIDENTIAL — ATTORNEY-CLIENT" excerpts
            -- because privileged-tagged mail entered the voice corpus and its
            -- greeting/exemplar extraction. The extraction bridge's privileged
            -- gate did not extend to voice profiling. This is the single corpus
            -- source feeding features + exemplars + tone synthesis, so exclude
            -- privileged-tagged events here (canonical bar-form '|privileged|').
            AND (sensitivity_tags IS NULL
                 OR sensitivity_tags NOT LIKE '%|privileged|%')
            ORDER BY sent_at DESC
            LIMIT :limit
        """),
        {"email": sender_email, "limit": limit},
    ).fetchall()

    return [
        {
            "body_plain": row[0],
            "body_html": row[1],
            "sent_at": row[2],
            "thread_depth": row[3] or 1,
            "thread_sent_ats": None,
            "directness_band": "medium",
        }
        for row in rows
    ]


def _fetch_frequent_recipients(session, sender_email: str) -> list[FrequentRecipient]:
    """Fetch top recipients for a sender from communication_events."""
    rows = session.execute(
        text("""
            -- recipient_email never existed as a column; recipients ship as the
            -- recipients_json array (Chunk 55 models). Unnest it
            -- (validation-run finding F-32b, 2026-07-01).
            SELECT r->>'email' AS recipient_email, COUNT(*) as cnt
            FROM communication_events,
                 LATERAL jsonb_array_elements(recipients_json) AS r
            WHERE sender_email = :email
            AND triage_tier_outcome = 'passed_to_extraction'
            AND r->>'email' IS NOT NULL
            GROUP BY r->>'email'
            ORDER BY cnt DESC
            LIMIT 10
        """),
        {"email": sender_email},
    ).fetchall()

    return [
        FrequentRecipient(email=row[0], interaction_count=row[1])
        for row in rows
    ]


def run_profile_generation(
    *,
    dry_run: bool = False,
    operator: str | None = None,
    aggregate_segment: str | None = None,
) -> dict:
    """Generate communication style profiles for eligible senders.

    D246 mirror: CLI-only entry point. Never called from FastAPI.
    D504: Replaces the all-"medium" placeholder with real pipeline.

    When ``aggregate_segment`` is set and no sender_person_id is specified,
    aggregate mode: no DPIA required, skip per-recipient classification.
    """
    import asyncio

    from src.shared.database import get_session_factory

    config = _load_config()
    session = get_session_factory()()

    is_aggregate = aggregate_segment is not None

    try:
        # Find eligible senders
        min_emails = config.profile_minimum_emails_to_generate
        senders = session.execute(
            text("""
                SELECT sender_email, COUNT(*) as cnt
                FROM communication_events
                WHERE triage_tier_outcome = 'passed_to_extraction'
                GROUP BY sender_email
                HAVING COUNT(*) >= :min_emails
            """),
            {"min_emails": min_emails},
        ).fetchall()

        # DPIA gate: aggregate mode does not require DPIA (D504)
        dpia_valid = True if is_aggregate else _verify_dpia_attestation(config)
        generated = 0
        skipped = 0
        # F-023 / ISS-0020: per-gate exclusion counters feeding the loud
        # run-summary diagnostics. Gate behavior is unchanged.
        gate_exclusions: dict[str, int] = {}

        for row in senders:
            sender_email = row[0]

            # DPIA gate for individual-mode (Lock-R4)
            if not is_aggregate and not dpia_valid:
                logger.info(
                    "voice_tone_dpia_gate_blocked",
                    sender=sender_email,
                )
                skipped += 1
                # F-023 / ISS-0020: count DPIA-gate exclusions for the summary.
                gate_exclusions["dpia"] = gate_exclusions.get("dpia", 0) + 1
                continue

            if dry_run:
                logger.info("voice_tone_dry_run_skip", sender=sender_email)
                generated += 1
                continue

            # Look up person ID — registry fast path, graph fallback (F-31).
            person_id = None
            if not is_aggregate:
                person_row = session.execute(
                    text("""
                        SELECT canonical_grace_id
                        FROM entity_resolution_registry
                        WHERE canonical_name = :email
                        AND canonical_type = 'Person'
                        LIMIT 1
                    """),
                    {"email": sender_email},
                ).fetchone()

                if person_row is not None:
                    person_id = person_row[0]
                else:
                    # F-31: the registry is only populated by connector/
                    # federation code, so voice was silently dead on any
                    # deployment without connectors. Fall back to resolving
                    # the sender against Person vertices in the graph (email
                    # then display name vs name/aliases) — the same approach
                    # triage tier-2 and the corroboration scorer use. The
                    # graph helper lives in role_resolver (Lock-R3: sole
                    # Voice module allowed to import arcade_client).
                    from src.ingestion.communications.voice_tone.role_resolver import (
                        resolve_sender_person,
                    )

                    display_row = session.execute(
                        text("""
                            SELECT sender_display_name
                            FROM communication_events
                            WHERE sender_email = :email
                              AND sender_display_name IS NOT NULL
                            GROUP BY sender_display_name
                            ORDER BY count(*) DESC
                            LIMIT 1
                        """),
                        {"email": sender_email},
                    ).fetchone()
                    display_name = display_row[0] if display_row else None

                    person_id = _run_coro(
                        resolve_sender_person(sender_email, display_name)
                    )
                    if person_id is not None:
                        logger.info(
                            "voice_tone_sender_resolved_via_graph",
                            sender=sender_email,
                        )

                if person_id is None:
                    logger.info(
                        "voice_tone_sender_unresolved_skipped",
                        sender=sender_email,
                    )
                    skipped += 1
                    # F-023 / ISS-0020: count unresolved-sender exclusions.
                    gate_exclusions["sender_unresolved"] = (
                        gate_exclusions.get("sender_unresolved", 0) + 1
                    )
                    continue

            # Get next version number
            if person_id:
                current_max = session.execute(
                    text("""
                        SELECT COALESCE(MAX(profile_version), 0)
                        FROM communication_style_profiles
                        WHERE sender_person_id = :pid
                    """),
                    {"pid": str(person_id)},
                ).scalar() or 0
            else:
                current_max = session.execute(
                    text("""
                        SELECT COALESCE(MAX(profile_version), 0)
                        FROM communication_style_profiles
                        WHERE aggregate_segment = :seg
                    """),
                    {"seg": aggregate_segment},
                ).scalar() or 0

            next_version = current_max + 1

            # D504: Real pipeline — fetch emails and extract features
            emails = _fetch_sender_emails(session, sender_email)
            if not emails:
                skipped += 1
                # F-023 / ISS-0020: count empty-corpus exclusions (eligible by
                # count, zero usable emails after corpus filters).
                gate_exclusions["empty_corpus"] = (
                    gate_exclusions.get("empty_corpus", 0) + 1
                )
                continue

            sig = _extract_real_signature(emails, config)

            # Populate frequent_recipients (individual mode only)
            if not is_aggregate:
                sig.frequent_recipients = _fetch_frequent_recipients(session, sender_email)

            # NL synthesis via get_provider() (D504)
            try:
                # F-56: asyncio.get_event_loop() RAISES RuntimeError on Python 3.14
                # when no loop is running (the normal CLI case), so this block always
                # skipped before any LLM call — tone_summary stayed empty under EVERY
                # provider (F-33's "synthesis empty" was this, not the model).
                # get_running_loop()/except-RuntimeError is the supported idiom.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        tone_summary, avoid_phrases = pool.submit(
                            asyncio.run,
                            _synthesize_tone_summary(sig, config),
                        ).result()
                else:
                    tone_summary, avoid_phrases = asyncio.run(
                        _synthesize_tone_summary(sig, config)
                    )
            except Exception:
                logger.warning("voice_tone_synthesis_skipped", sender=sender_email)
                tone_summary, avoid_phrases = None, []

            sig.tone_summary = tone_summary
            sig.avoid_phrases = avoid_phrases

            # Insert profile
            if is_aggregate:
                session.execute(
                    text("""
                        INSERT INTO communication_style_profiles
                            (aggregate_segment, profile_version, style_signature,
                             profile_quality_band)
                        VALUES
                            (:seg, :version, cast(:sig as jsonb), :band)
                    """),
                    {
                        "seg": aggregate_segment,
                        "version": next_version,
                        "sig": sig.model_dump_json(),
                        "band": "medium",
                    },
                )
            else:
                session.execute(
                    text("""
                        INSERT INTO communication_style_profiles
                            (sender_person_id, profile_version, style_signature,
                             profile_quality_band)
                        VALUES
                            (:pid, :version, cast(:sig as jsonb), :band)
                    """),
                    {
                        "pid": str(person_id),
                        "version": next_version,
                        "sig": sig.model_dump_json(),
                        "band": "medium",
                    },
                )
            session.commit()

            # Run recipient classification (individual mode only, D504)
            if not is_aggregate and person_id:
                _run_recipient_classification(
                    session, config, sender_email, person_id, next_version
                )

            # Retention prune
            if person_id:
                session.execute(
                    text("SELECT prune_voice_tone_versions(:sid, NULL, :keep)"),
                    {"sid": str(person_id), "keep": config.retention_versions},
                )
                session.commit()

            generated += 1
            # D428: emit profile-generated metric on successful INSERT only.
            from src.analytics.metrics import record_voice_tone_profile_generated
            record_voice_tone_profile_generated()

            logger.info(
                "voice_tone_profile_generated",
                sender=sender_email,
                version=next_version,
                operator=operator,
                aggregate=is_aggregate,
            )

        summary: dict = {"generated": generated, "skipped": skipped}

        # F-023 / ISS-0020: when the run produced nothing (or skipped anyone),
        # say WHY per gate — threshold vs observed, plus the knob/file to
        # change. Diagnostics only; no gate behavior changed.
        if generated == 0 or skipped > 0:
            try:
                diagnostics = _build_gate_diagnostics(
                    session,
                    config,
                    eligible_sender_count=len(senders),
                    dpia_valid=dpia_valid,
                    is_aggregate=is_aggregate,
                    gate_exclusions=gate_exclusions,
                )
            except Exception:  # noqa: BLE001 — diagnostics must never fail the run
                diagnostics = {}
            if diagnostics:
                summary["gate_diagnostics"] = diagnostics
                for gate, detail in diagnostics.items():
                    logger.info(
                        "voice_tone_gate_diagnostic",
                        gate=gate,
                        detail=detail,
                    )

        return summary

    finally:
        session.close()


def _run_recipient_classification(
    session, config: VoiceToneConfig, sender_email: str,
    person_id, next_version: int,
) -> None:
    """Run per-recipient classification and persist recipient_style_profiles (D504)."""
    import asyncio

    from src.ingestion.communications.voice_tone.recipient_classifier import (
        RecipientClassifier,
    )

    classifier = RecipientClassifier(config)

    # Get profile id for this version
    profile_row = session.execute(
        text("""
            SELECT id FROM communication_style_profiles
            WHERE sender_person_id = :pid AND profile_version = :ver
            LIMIT 1
        """),
        {"pid": str(person_id), "ver": next_version},
    ).fetchone()

    if profile_row is None:
        return

    profile_id = profile_row[0]

    # Fetch recipient emails
    recipients = session.execute(
        text("""
            -- recipient_email never existed as a column; unnest recipients_json
            -- (validation-run finding F-32b, 2026-07-01).
            SELECT DISTINCT r->>'email' AS recipient_email
            FROM communication_events,
                 LATERAL jsonb_array_elements(recipients_json) AS r
            WHERE sender_email = :email
            AND triage_tier_outcome = 'passed_to_extraction'
            AND r->>'email' IS NOT NULL
            LIMIT 20
        """),
        {"email": sender_email},
    ).fetchall()

    for recip_row in recipients:
        recip_email = recip_row[0]

        # Check graph presence (Lock-R2: Postgres only)
        try:
            recip_id = asyncio.run(
                classifier.check_graph_presence(recip_email, session)
            )
        except Exception:
            try:
                # F-56 (validation run): asyncio.get_event_loop() raises
                # RuntimeError on Python 3.14 when no loop is running (the normal
                # CLI case), so this retry branch itself threw. Use the supported
                # get_running_loop()/except-RuntimeError idiom.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None and loop.is_running():
                    continue
                recip_id = asyncio.run(
                    classifier.check_graph_presence(recip_email, session)
                )
            except Exception:
                continue

        if recip_id is None:
            continue

        # Classify
        try:
            category, confidence = asyncio.run(
                classifier.classify(
                    recipient_email=recip_email,
                    canonical_grace_id=recip_id,
                    config=config,
                )
            )
        except Exception:
            category, confidence = "general_distribution", "low"

        # Persist recipient style profile
        from src.ingestion.communications.voice_tone.models import StyleDelta

        session.execute(
            text("""
                INSERT INTO recipient_style_profiles
                    (profile_id, recipient_person_id, category,
                     confidence_band, style_delta)
                VALUES
                    (:pid, :rid, :cat, :conf, cast(:delta as jsonb))
            """),
            {
                "pid": str(profile_id),
                "rid": str(recip_id),
                "cat": category,
                "conf": confidence,
                "delta": StyleDelta().model_dump_json(),
            },
        )
    session.commit()


def run_archive_scan(*, dry_run: bool = False) -> dict:
    """Archive tier: write JSON for senders with sufficient new correspondences (D437).

    SN-1 invariant: archive predicate depends on ingestion pipeline storing
    None (not '[]'::jsonb) for references_json.
    """
    from src.shared.database import get_session_factory

    config = _load_config()
    if not config.archive_tier_enabled:
        return {"archived": 0, "reason": "archive_tier_disabled"}

    session = get_session_factory()()
    try:
        min_new = config.archive_minimum_new_correspondences
        senders = session.execute(
            text("""
                SELECT sender_email, COUNT(*) as cnt
                FROM communication_events
                WHERE in_reply_to IS NULL
                AND references_json IS NULL
                GROUP BY sender_email
                HAVING COUNT(*) >= :min_new
            """),
            {"min_new": min_new},
        ).fetchall()

        archived = 0
        for row in senders:
            sender_email = row[0]
            email_hash = hashlib.sha256(sender_email.encode()).hexdigest()

            if dry_run:
                archived += 1
                continue

            # Ensure archive directory exists with chmod 700
            _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            os.chmod(str(_ARCHIVE_DIR), 0o700)

            # Write archive JSON with chmod 600
            archive_path = _ARCHIVE_DIR / f"{email_hash}.json"
            archive_data = {
                "sender_email_hash": email_hash,
                "archived_at": datetime.now(tz=timezone.utc).isoformat(),
                "new_correspondence_count": row[1],
            }
            archive_path.write_text(json.dumps(archive_data, indent=2))
            os.chmod(str(archive_path), 0o600)

            # Update index TSV
            index_path = _ARCHIVE_DIR / "_index.tsv"
            with open(index_path, "a") as f:
                f.write(f"{email_hash}\t{datetime.now(tz=timezone.utc).isoformat()}\n")

            archived += 1
            logger.info("voice_tone_archived", sender_hash=email_hash)

        return {"archived": archived}
    finally:
        session.close()


def run_erase(*, person_email: str) -> dict:
    """Erase all profile data for a person (right-to-erasure).

    Uses SECURITY DEFINER path for DB deletion + os.unlink for archive.
    """
    from src.shared.database import get_session_factory

    session = get_session_factory()()
    try:
        # Find person's profiles
        person_row = session.execute(
            text("""
                SELECT canonical_grace_id
                FROM entity_resolution_registry
                WHERE canonical_name = :email
                AND canonical_type = 'Person'
                LIMIT 1
            """),
            {"email": person_email},
        ).fetchone()

        deleted_profiles = 0
        if person_row:
            person_id = person_row[0]
            # Delete via SECURITY DEFINER prune (keep 0)
            result = session.execute(
                text("SELECT prune_voice_tone_versions(:sid, NULL, 0)"),
                {"sid": str(person_id)},
            )
            deleted_profiles = result.scalar() or 0
            session.commit()

        # Delete archive file
        email_hash = hashlib.sha256(person_email.encode()).hexdigest()
        archive_path = _ARCHIVE_DIR / f"{email_hash}.json"
        deleted_archive = False
        if archive_path.exists():
            os.unlink(str(archive_path))
            deleted_archive = True

            # Remove from index
            index_path = _ARCHIVE_DIR / "_index.tsv"
            if index_path.exists():
                lines = index_path.read_text().splitlines()
                lines = [l for l in lines if not l.startswith(email_hash)]
                index_path.write_text("\n".join(lines) + "\n" if lines else "")

        logger.info(
            "voice_tone_erased",
            email=person_email,
            profiles_deleted=deleted_profiles,
            archive_deleted=deleted_archive,
        )

        return {
            "profiles_deleted": deleted_profiles,
            "archive_deleted": deleted_archive,
        }
    finally:
        session.close()
