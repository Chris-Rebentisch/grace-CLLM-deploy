"""Sensitivity tagger CLI — four closed-list tags with thread-level
propagation (Chunk 59, D426/D439/D440).

Tags (v1 closed list — new values advance D426 in a future chunk):
  - ``privileged``
  - ``pii_dense``
  - ``external_boundary``
  - ``privilege_potentially_waived``

CLI entry:
    python -m src.ingestion.communications.sensitivity_tagger run [--dry-run]

D246 mirror: this module MUST NOT import ``fastapi`` or ``apscheduler``.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import structlog
import yaml
from sqlalchemy import text as sa_text

logger = structlog.get_logger("ingestion.sensitivity_tagger")

# ---------------------------------------------------------------------------
# Closed-list vocabulary (D426 — frozen at v1)
# ---------------------------------------------------------------------------
SENSITIVITY_TAGS_V1 = frozenset({
    "privileged",
    "pii_dense",
    "external_boundary",
    "privilege_potentially_waived",
})


# ---------------------------------------------------------------------------
# Bar-form serialization helpers (D349)
# ---------------------------------------------------------------------------

def tags_to_bar_form(tags: list[str]) -> str:
    """Convert a list of tags to alphabetic-sorted bar-form.

    ``[]`` → ``""``
    ``["privileged", "external_boundary"]`` → ``"|external_boundary|privileged|"``

    Raises ``ValueError`` if any tag contains the ``|`` separator.
    """
    for t in tags:
        if "|" in t:
            raise ValueError(f"Tag must not contain '|': {t!r}")
    if not tags:
        return ""
    sorted_tags = sorted(set(tags))
    return "|" + "|".join(sorted_tags) + "|"


def tags_from_bar_form(s: str | None) -> list[str]:
    """Inverse of :func:`tags_to_bar_form`.

    ``None`` or ``""`` → ``[]``
    ``"|external_boundary|privileged|"`` → ``["external_boundary", "privileged"]``
    """
    if not s:
        return []
    return [t for t in s.split("|") if t]


# ---------------------------------------------------------------------------
# PII-density regex patterns (compiled once at module load)
# ---------------------------------------------------------------------------

_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w][\w.-]+\b")
_RE_PHONE = re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b")
_RE_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_RE_DIGIT_RUN = re.compile(r"\b\d{9,}\b")
_RE_PROPER_NAME = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")


# ---------------------------------------------------------------------------
# Tag detectors
# ---------------------------------------------------------------------------

def _load_sensitivity_config() -> dict:
    """Load ``config/sensitivity_rules.yaml``."""
    config_path = Path(__file__).resolve().parents[3] / "config" / "sensitivity_rules.yaml"
    if not config_path.exists():
        logger.warning("sensitivity_tagger_config_missing", path=str(config_path))
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def _load_organization_domains() -> list[str]:
    """Load organization_domains from ``config/voice_tone_config.yaml`` (Lock-R1)."""
    vt_path = Path(__file__).resolve().parents[3] / "config" / "voice_tone_config.yaml"
    if not vt_path.exists():
        return []
    with open(vt_path) as f:
        vt_config = yaml.safe_load(f) or {}
    return vt_config.get("organization_domains", [])


def _norm_amp(s: str) -> str:
    """Canonicalize text for privilege-phrase matching (F-25): lowercase,
    map ``&`` → ``and``, and collapse whitespace so "PRIVILEGED & CONFIDENTIAL"
    and "privileged and confidential" compare equal."""
    return re.sub(r"\s+", " ", (s or "").replace("&", "and")).strip().lower()


def _detect_privileged(
    event_row: dict,
    config: dict,
    recipient_categories: dict[str, str] | None = None,
) -> list[str]:
    """Detect ``privileged`` and ``privilege_potentially_waived`` tags.

    Triggers:
      (a) Any recipient classified as ``legal_counsel`` (via RecipientClassifier).
      (b) Body/subject regex-matches a privilege phrase from config, anchored
          at line start to avoid matching forward-quoted email text (D439).

    Args:
        event_row: Dict with keys from communication_events columns.
        config: Sensitivity rules config dict.
        recipient_categories: Optional pre-computed {email: category} map.

    Returns:
        List of tags to apply (may be empty).
    """
    tags: list[str] = []
    trigger_a = False

    # Trigger (a): legal_counsel recipient
    if recipient_categories:
        has_legal = any(
            cat == "legal_counsel" for cat in recipient_categories.values()
        )
        if has_legal:
            trigger_a = True
            tags.append("privileged")

    # Trigger (b): privilege phrase match (line-start anchored)
    phrases = config.get("privilege_phrases", [])
    if phrases and not trigger_a:
        body = event_row.get("body_plain") or ""
        subject = event_row.get("subject") or ""
        combined_text = subject + "\n" + body

        # F-25 (validation run, 2026-07-01): real subject lines write
        # "PRIVILEGED & CONFIDENTIAL", but the config phrase list uses "and"
        # (or vice-versa), and case-insensitive regex alone didn't bridge the
        # "&" / "and" gap — only 1/3 litigation emails tagged. Normalize both
        # the phrases and the text (case + "&"→"and" + collapsed whitespace)
        # before the line-start-anchored comparison.
        norm_phrases = [_norm_amp(p) for p in phrases]
        norm_phrases = [p for p in norm_phrases if p]
        for line in combined_text.split("\n"):
            nl = _norm_amp(line)
            if any(nl.startswith(np) for np in norm_phrases):
                if "privileged" not in tags:
                    tags.append("privileged")
                break

    # privilege_potentially_waived co-tag
    if trigger_a and recipient_categories:
        non_legal = [
            email for email, cat in recipient_categories.items()
            if cat != "legal_counsel"
        ]
        if non_legal:
            tags.append("privilege_potentially_waived")

    return tags


def _detect_pii_dense(event_row: dict, config: dict) -> list[str]:
    """Detect ``pii_dense`` tag via body-text heuristic (H1 resolved).

    Pure function — no DB join, no session argument.
    """
    body = event_row.get("body_plain")
    if not body:
        body = event_row.get("body_html")
        if body:
            # Strip HTML tags
            body = re.sub(r"<[^>]+>", " ", body)
    if not body:
        return []

    # Count PII-like tokens
    pii_count = (
        len(_RE_EMAIL.findall(body))
        + len(_RE_PHONE.findall(body))
        + len(_RE_SSN.findall(body))
        + len(_RE_DIGIT_RUN.findall(body))
        + len(_RE_PROPER_NAME.findall(body))
    )

    word_count = max(len(body.split()), 1)
    pii_density = pii_count / word_count * 100

    threshold = config.get("pii_density_threshold", 5.0)
    if pii_density >= threshold:
        return ["pii_dense"]
    return []


def _detect_external_boundary(event_row: dict, org_domains: list[str]) -> list[str]:
    """Detect ``external_boundary`` tag.

    Fires when sender or any To/Cc recipient domain is NOT in
    organization_domains. BCC does NOT trigger.
    """
    if not org_domains:
        logger.warning("sensitivity_tagger_no_organization_domains_configured")
        return []

    org_set = {d.lower() for d in org_domains}

    def _domain(email: str) -> str:
        parts = email.rsplit("@", 1)
        return parts[1].lower() if len(parts) == 2 else ""

    # Check sender
    sender_email = event_row.get("sender_email", "")
    if sender_email and _domain(sender_email) not in org_set:
        return ["external_boundary"]

    # Check To/Cc recipients
    recipients = event_row.get("recipients_json") or []
    for recip in recipients:
        if isinstance(recip, dict):
            role = recip.get("role", "")
            email = recip.get("email", "")
            if role in ("to", "cc") and email and _domain(email) not in org_set:
                return ["external_boundary"]

    return []


# ---------------------------------------------------------------------------
# Thread-level propagation (CP5, D440)
# ---------------------------------------------------------------------------

def _propagate_thread_tags(session, thread_id: str | None, message_id: str) -> None:
    """Eager-recompute thread-level sensitivity tag union.

    UPSERT into ``communication_sensitivity_propagation`` keyed on thread_id.
    Orphan threads (NULL thread_id or thread_id == message_id) use message_id.
    """
    effective_tid = thread_id if (thread_id and thread_id != message_id) else message_id

    # Compute union of all tags for this thread
    rows = session.execute(
        sa_text(
            "SELECT sensitivity_tags FROM communication_events "
            "WHERE thread_id = :tid OR (thread_id IS NULL AND message_id = :tid)"
        ),
        {"tid": effective_tid},
    ).fetchall()

    all_tags: set[str] = set()
    for row in rows:
        if row[0]:
            all_tags.update(tags_from_bar_form(row[0]))

    bar = tags_to_bar_form(sorted(all_tags))

    session.execute(
        sa_text(
            "INSERT INTO communication_sensitivity_propagation "
            "(thread_id, propagated_tags, last_recomputed_at) "
            "VALUES (:tid, :tags, NOW()) "
            "ON CONFLICT (thread_id) DO UPDATE SET "
            "propagated_tags = EXCLUDED.propagated_tags, "
            "last_recomputed_at = NOW()"
        ),
        {"tid": effective_tid, "tags": bar},
    )


# ---------------------------------------------------------------------------
# Main tagging cycle
# ---------------------------------------------------------------------------

async def _run_tagger(*, dry_run: bool = False) -> dict:
    """Execute one full sensitivity tagging pass."""
    from src.shared.database import get_session_factory

    Session = get_session_factory()
    session = Session()

    config = _load_sensitivity_config()
    org_domains = _load_organization_domains()

    try:
        # Fetch all events (could be scoped later)
        rows = session.execute(
            sa_text(
                "SELECT id, message_id, sender_email, sender_display_name, "
                "recipients_json, subject, body_plain, body_html, "
                "thread_id, sensitivity_tags "
                "FROM communication_events "
                "ORDER BY id"
            )
        ).fetchall()

        tagged_count = 0
        threads_propagated: set[str] = set()

        for row in rows:
            event_row = {
                "id": row[0],
                "message_id": row[1],
                "sender_email": row[2],
                "sender_display_name": row[3],
                "recipients_json": row[4],
                "subject": row[5],
                "body_plain": row[6],
                "body_html": row[7],
                "thread_id": row[8],
            }

            tags: list[str] = []

            # Privilege detection (trigger (b) only in v1 CLI — trigger (a)
            # requires RecipientClassifier async call which needs graph context;
            # for CLI batch pass we detect via config phrases only)
            tags.extend(_detect_privileged(event_row, config))

            # PII density
            tags.extend(_detect_pii_dense(event_row, config))

            # External boundary
            tags.extend(_detect_external_boundary(event_row, org_domains))

            if tags:
                bar = tags_to_bar_form(tags)
                if not dry_run:
                    session.execute(
                        sa_text(
                            "UPDATE communication_events SET sensitivity_tags = :tags "
                            "WHERE id = :id"
                        ),
                        {"tags": bar, "id": str(row[0])},
                    )

                logger.info(
                    "sensitivity_tagged",
                    event_id=str(row[0]),
                    tags=tags,
                )
                tagged_count += 1

            # Thread propagation
            if not dry_run:
                effective_tid = row[8] if (row[8] and row[8] != row[1]) else row[1]
                if effective_tid not in threads_propagated:
                    _propagate_thread_tags(session, row[8], row[1])
                    threads_propagated.add(effective_tid)

        if not dry_run:
            session.commit()

        logger.info(
            "sensitivity_tagger_complete",
            total_events=len(rows),
            tagged=tagged_count,
            threads_propagated=len(threads_propagated),
        )
        return {
            "total": len(rows),
            "tagged": tagged_count,
            "threads_propagated": len(threads_propagated),
        }

    finally:
        session.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = argparse.ArgumentParser(
        description="Sensitivity tagger — four closed-list tags + thread propagation (D426)"
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Execute one tagging pass")
    run_parser.add_argument("--dry-run", action="store_true", help="No DB writes")

    args = parser.parse_args()
    if args.command != "run":
        parser.print_help()
        sys.exit(1)

    result = asyncio.run(_run_tagger(dry_run=args.dry_run))
    logger.info("sensitivity_tagger_exit", **result)


if __name__ == "__main__":
    main()
