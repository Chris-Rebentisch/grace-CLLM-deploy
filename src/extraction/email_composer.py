"""Email compose contract for the extraction bridge (D509).

Strips quoted history and signatures from email bodies and prepends a
structured sender anchor — the input adapter for the extraction bridge.

D509 — compose contract for email→extraction bridge.
D356 capture-the-why: invariant = Lock-R2/R3 import discipline; carve-out =
standalone regex signature strip (does NOT import signature_extractor);
authorization = D509.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from mailparser_reply import EmailReplyParser

# ---------------------------------------------------------------------------
# Regex patterns for fallback quote stripping
# ---------------------------------------------------------------------------

_QUOTE_LINE_RE = re.compile(r"^>", re.MULTILINE)
_ON_WROTE_RE = re.compile(r"^On .+ wrote:\s*$", re.MULTILINE)
_ORIGINAL_MSG_RE = re.compile(r"^---\s*Original Message\s*---\s*$", re.MULTILINE)

# ---------------------------------------------------------------------------
# Regex patterns for signature stripping (standalone — NOT importing
# src.ingestion.communications.voice_tone.signature_extractor per Lock-R2/R3)
# ---------------------------------------------------------------------------

_SIGNATURE_DELIMITERS = re.compile(
    r"^(?:-- ?$|--$|_{3,}|Sent from my )", re.MULTILINE
)


@dataclass
class CommunicationEventRow:
    """Typed row from communication_events (DB-backed columns only).

    Uses only columns that exist on communication_events.
    No sender_title or sender_organization (those columns do not exist).
    """

    message_id: str
    sender_display_name: str | None
    sender_email: str
    subject: str | None
    sent_at: datetime | None
    body_plain: str | None


def strip_quoted_history(body_plain: str) -> str:
    """Strip quoted email history from a plain-text email body.

    Primary: uses mail-parser-reply's EmailReplyParser.
    Fallback: regex-based stripping when library returns empty or raises.
    """
    if not body_plain or not body_plain.strip():
        return body_plain or ""

    # Primary path: mail-parser-reply library
    try:
        parser = EmailReplyParser(languages=["en"])
        result = parser.parse_reply(text=body_plain)
        if result and result.strip():
            return result.strip()
    except Exception:
        pass

    # Fallback: regex marker strip
    return _regex_strip_quotes(body_plain)


def _regex_strip_quotes(text: str) -> str:
    """Regex-based quote stripping fallback."""
    lines = text.split("\n")
    output_lines: list[str] = []
    in_quote_block = False

    for line in lines:
        # Detect "On ... wrote:" marker — drop everything after
        if _ON_WROTE_RE.match(line):
            break
        # Detect "--- Original Message ---" marker
        if _ORIGINAL_MSG_RE.match(line):
            break
        # Skip quoted lines (starting with >)
        if _QUOTE_LINE_RE.match(line):
            in_quote_block = True
            continue
        # If we were in a quote block and hit a non-quote line, stop skipping
        if in_quote_block:
            # Continuation of quoted region — keep skipping
            # (heuristic: blank line after quotes is still part of quote)
            if not line.strip():
                continue
            in_quote_block = False
        output_lines.append(line)

    return "\n".join(output_lines).strip()


def _strip_signature(text: str) -> str:
    """Strip email signature using common delimiters (standalone regex)."""
    match = _SIGNATURE_DELIMITERS.search(text)
    if match:
        return text[: match.start()].strip()
    return text.strip()


def compose_extraction_document(event: CommunicationEventRow) -> str:
    """Compose an extraction-ready document from a communication event.

    Steps:
    1. Strip quoted history from body_plain.
    2. Strip signature via standalone regex.
    3. Prepend structured sender anchor (DB-backed fields only).

    Returns the composed text ready for extract_document().
    """
    body = event.body_plain or ""

    # Step 1: Strip quoted history
    visible_body = strip_quoted_history(body)

    # Step 2: Strip signature
    visible_body = _strip_signature(visible_body)

    # Step 3: Build sender anchor (omit missing fields)
    anchor_lines: list[str] = []

    if event.sender_display_name:
        anchor_lines.append(f"From: {event.sender_display_name} <{event.sender_email}>")
        # F-024 / ISS-0016: extraction over the bare "From:" header still
        # produced first-name fragment entities ("Diane") when the body was
        # signed with a first name. Spell the sender identity out as a
        # declarative sentence so the extractor binds first-person statements
        # and first-name sign-offs to the full canonical sender name.
        anchor_lines.append(
            f"Sender: This email was written by {event.sender_display_name} "
            f"({event.sender_email}). Any first-person statements or "
            f"first-name sign-offs in the body refer to "
            f"{event.sender_display_name}."
        )
    else:
        anchor_lines.append(f"From: {event.sender_email}")

    if event.sent_at:
        anchor_lines.append(f"Date: {event.sent_at.isoformat()}")

    if event.subject:
        anchor_lines.append(f"Subject: {event.subject}")

    anchor_lines.append("---")

    anchor = "\n".join(anchor_lines)
    return f"{anchor}\n{visible_body}"
