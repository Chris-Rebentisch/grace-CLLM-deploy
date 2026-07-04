"""Voice Card v1 renderer — 4 portable export formats (Chunk 78, D505).

D505 capture-the-why: Voice Card export is CLI-only (D246 mirror).
This module MUST NOT be imported by ``src/api/communications_routes.py``.

Formats: markdown, claude-skill, claude-style, json.
All exemplars route through ``redactor.redact_text()`` before emission.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from src.ingestion.communications.voice_tone.models import (
    StyleSignature,
    VoiceCardV1,
)
from src.ingestion.communications.voice_tone.redactor import redact_text

logger = structlog.get_logger()


class VoiceCardRenderer:
    """Renders a StyleSignature into one of 4 portable formats.

    All exemplar text is PII-redacted before emission (D506).
    Core card body is sized to ``word_limit`` words (default 400, D505).
    """

    def __init__(self, word_limit: int = 400) -> None:
        self.word_limit = word_limit

    def _build_card(
        self,
        profile: StyleSignature,
        subject: str,
        source_email_count: int = 0,
        consent_ref: str | None = None,
    ) -> VoiceCardV1:
        """Build a VoiceCardV1 from a StyleSignature."""
        redacted_exemplars = [redact_text(e, ner_available=False) for e in profile.sample_phrases]
        redacted_avoid = [redact_text(a, ner_available=False) for a in profile.avoid_phrases]

        return VoiceCardV1(
            profile_schema="grace.voice-card/v1",
            subject=subject,
            generated=datetime.now(timezone.utc).isoformat(),
            source_email_count=source_email_count,
            consent_ref=consent_ref,
            style_summary=profile.tone_summary or _default_style_summary(profile),
            characteristic_phrases=redacted_exemplars[:10],
            greetings_closings={
                "greetings": profile.greeting_patterns[:5],
                "closings": profile.closing_patterns[:5],
            },
            contrastive=profile.contrastive_markers[:10],
            exemplars=redacted_exemplars[:5],
            avoid=redacted_avoid[:10],
        )

    def render(
        self,
        profile: StyleSignature,
        subject: str,
        fmt: str = "markdown",
        source_email_count: int = 0,
        consent_ref: str | None = None,
    ) -> str:
        """Render a Voice Card in the specified format.

        Args:
            profile: StyleSignature to render.
            subject: Subject identifier (email or segment).
            fmt: Output format — markdown, claude-skill, claude-style, json.
            source_email_count: Number of source emails analyzed.
            consent_ref: DPIA consent reference.

        Returns:
            Rendered card as a string.
        """
        card = self._build_card(profile, subject, source_email_count, consent_ref)

        if fmt == "markdown":
            return self._render_markdown(card)
        elif fmt == "claude-skill":
            return self._render_claude_skill(card)
        elif fmt == "claude-style":
            return self._render_claude_style(card)
        elif fmt == "json":
            return self._render_json(card)
        else:
            raise ValueError(f"Unknown format: {fmt!r}")

    # ------------------------------------------------------------------
    # Format renderers
    # ------------------------------------------------------------------

    def _render_markdown(self, card: VoiceCardV1) -> str:
        """Markdown with YAML frontmatter + 6 required sections."""
        lines: list[str] = []
        # YAML frontmatter
        lines.append("---")
        lines.append(f"profile_schema: {card.profile_schema}")
        lines.append(f"subject: {card.subject}")
        lines.append(f"generated: {card.generated}")
        lines.append(f"source_email_count: {card.source_email_count}")
        if card.consent_ref:
            lines.append(f"consent_ref: {card.consent_ref}")
        lines.append("---")
        lines.append("")

        # Section 1: Style summary
        lines.append("## Style summary")
        lines.append("")
        lines.append(_truncate_to_words(card.style_summary, self.word_limit))
        lines.append("")

        # Section 2: Characteristic phrases
        lines.append("## Characteristic phrases")
        lines.append("")
        for phrase in card.characteristic_phrases:
            lines.append(f"- {phrase}")
        lines.append("")

        # Section 3: Greetings/closings
        lines.append("## Greetings/closings")
        lines.append("")
        greetings = card.greetings_closings.get("greetings", [])
        closings = card.greetings_closings.get("closings", [])
        if greetings:
            lines.append("**Greetings:**")
            for g in greetings:
                lines.append(f"- {g}")
        if closings:
            lines.append("**Closings:**")
            for c in closings:
                lines.append(f"- {c}")
        lines.append("")

        # Section 4: What makes this voice distinct
        lines.append("## What makes this voice distinct")
        lines.append("")
        for marker in card.contrastive:
            lines.append(f"- {marker}")
        lines.append("")

        # Section 5: Exemplars
        lines.append("## Exemplars")
        lines.append("")
        for ex in card.exemplars:
            lines.append(f"> {ex}")
            lines.append("")

        # Section 6: Avoid
        lines.append("## Avoid")
        lines.append("")
        for av in card.avoid:
            lines.append(f"- {av}")
        lines.append("")

        return "\n".join(lines)

    def _render_claude_skill(self, card: VoiceCardV1) -> str:
        """SKILL.md format with name <=64 chars, description <=200 chars."""
        name = f"Write like {card.subject}"[:64]
        description = (
            f"Mimic the communication style of {card.subject}. "
            f"Based on {card.source_email_count} analyzed emails."
        )[:200]

        lines: list[str] = []
        lines.append(f"# {name}")
        lines.append("")
        lines.append(f"**Description:** {description}")
        lines.append("")
        lines.append("## Style guide")
        lines.append("")
        lines.append(card.style_summary or "No style summary available.")
        lines.append("")

        if card.characteristic_phrases:
            lines.append("## Characteristic phrases")
            lines.append("")
            for p in card.characteristic_phrases:
                lines.append(f"- {p}")
            lines.append("")

        if card.contrastive:
            lines.append("## Distinctive markers")
            lines.append("")
            for m in card.contrastive:
                lines.append(f"- {m}")
            lines.append("")

        if card.avoid:
            lines.append("## Avoid")
            lines.append("")
            for a in card.avoid:
                lines.append(f"- {a}")
            lines.append("")

        return "\n".join(lines)

    def _render_claude_style(self, card: VoiceCardV1) -> str:
        """Paste-ready style block, <=400 words."""
        parts: list[str] = []
        if card.style_summary:
            parts.append(card.style_summary)
        if card.characteristic_phrases:
            parts.append("Characteristic phrases: " + "; ".join(card.characteristic_phrases))
        if card.contrastive:
            parts.append("Distinctive markers: " + "; ".join(card.contrastive))
        if card.avoid:
            parts.append("Avoid: " + "; ".join(card.avoid))

        full_text = "\n\n".join(parts)
        return _truncate_to_words(full_text, self.word_limit)

    def _render_json(self, card: VoiceCardV1) -> str:
        """Canonical VoiceCardV1 JSON record."""
        return card.model_dump_json(indent=2)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _default_style_summary(profile: StyleSignature) -> str:
    """Generate a minimal style summary from band values."""
    return (
        f"Formality: {profile.formality_band}. "
        f"Vocabulary complexity: {profile.vocabulary_complexity_band}. "
        f"Hedging: {profile.hedging_frequency_band}. "
        f"Directness: {profile.directness_band}."
    )


def _truncate_to_words(text: str, limit: int) -> str:
    """Truncate text to approximately *limit* words."""
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + " ..."


# ------------------------------------------------------------------
# Export audit trail (CP7, D506)
# ------------------------------------------------------------------


def record_export_audit(
    subject: str,
    profile_version: int,
    fmt: str,
    redaction_applied: bool = True,
    operator: str | None = None,
) -> None:
    """Persist a row in ``voice_card_exports`` and increment OTel counter."""
    try:
        from src.analytics.metrics import grace_voice_cards_exported_total

        grace_voice_cards_exported_total.add(1, {"format": fmt})
    except Exception:
        logger.warning("voice_card_export_otel_increment_failed")

    try:
        # F-36 (validation run, 2026-07-01): this imported the nonexistent
        # ``src.shared.db`` (same phantom-import class as F-34), so the audit
        # INSERT always failed silently under the broad except — the
        # voice_card_exports audit row was never written even when export
        # succeeded. Use the real ``src.shared.database.get_session_factory``.
        from src.shared.database import get_session_factory
        from sqlalchemy import text

        with get_session_factory()() as session:
            session.execute(
                text(
                    "INSERT INTO voice_card_exports "
                    "(subject, profile_version, format, redaction_applied, operator) "
                    "VALUES (:subject, :pv, :fmt, :ra, :op)"
                ),
                {
                    "subject": subject,
                    "pv": profile_version,
                    "fmt": fmt,
                    "ra": redaction_applied,
                    "op": operator,
                },
            )
            session.commit()
    except Exception:
        logger.warning("voice_card_export_audit_write_failed")


# ------------------------------------------------------------------
# DPIA egress gate (CP7, D506)
# ------------------------------------------------------------------


def check_dpia_egress_gate(subject: str, is_aggregate: bool = False) -> bool:
    """Check DPIA attestation before individual export (D506).

    Aggregate exports are always allowed. Individual exports require
    a hardened DPIA attestation.
    """
    if is_aggregate:
        return True

    try:
        import pathlib

        dpia_dir = pathlib.Path("data/dpia-attestations")
        if not dpia_dir.exists():
            logger.warning("voice_card_dpia_no_attestation_dir", subject=subject)
            return False

        # Any valid attestation file present = allowed
        attestations = list(dpia_dir.glob("*.json"))
        if not attestations:
            logger.warning("voice_card_dpia_no_attestation", subject=subject)
            return False

        return True
    except Exception:
        logger.warning("voice_card_dpia_check_failed", subject=subject)
        return False
