"""Tier 1 noise filter — seven rules in fixed first-match-wins precedence (Chunk 56, D429).

Pure Python — no LLM, no network, no DB.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ingestion.communications.triage.config import Tier1Config
    from src.ingestion.models import CommunicationEvent


# ---------------------------------------------------------------------------
# HTML stripping helper
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """stdlib HTMLParser subclass that collects text nodes."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def _strip_html(html: str) -> str:
    """Strip HTML tags and return plain text."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Individual rule functions
# ---------------------------------------------------------------------------

def check_duplicate_message_id(event: CommunicationEvent, seen_ids: set[str]) -> str | None:
    """Return outcome label if message_id already seen."""
    if event.message_id and event.message_id in seen_ids:
        return "filtered_t1_duplicate_message_id"
    return None


def check_auto_reply(event: CommunicationEvent) -> str | None:
    """RFC 3834 Auto-Submitted header check."""
    if event.raw_headers:
        auto_submitted = event.raw_headers.get("Auto-Submitted") or event.raw_headers.get("auto-submitted")
        if auto_submitted and auto_submitted.lower() != "no":
            return "filtered_t1_auto_reply"
    return None


def check_newsletter(event: CommunicationEvent) -> str | None:
    """RFC 8058 List-Unsubscribe header presence."""
    if event.raw_headers:
        if event.raw_headers.get("List-Unsubscribe") or event.raw_headers.get("list-unsubscribe"):
            return "filtered_t1_newsletter"
    return None


def check_calendar_invite(event: CommunicationEvent) -> str | None:
    """Check for text/calendar MIME attachments OR a text/calendar message body.

    D539: previously only attachments were inspected, so an inline iMIP invite
    (``Content-Type: text/calendar`` on the message itself, the common Outlook/Google
    REQUEST shape) slipped through to later tiers once raw_headers was captured. Mirror
    check_bounce and also inspect the message Content-Type header.
    """
    for att in event.attachments:
        if att.mime_type and "text/calendar" in att.mime_type.lower():
            return "filtered_t1_calendar_invite"
    if event.raw_headers:
        content_type = (
            event.raw_headers.get("Content-Type")
            or event.raw_headers.get("content-type")
            or ""
        )
        if "text/calendar" in content_type.lower():
            return "filtered_t1_calendar_invite"
    return None


def check_bounce(event: CommunicationEvent) -> str | None:
    """Check for multipart/report bounce indicators."""
    if event.raw_headers:
        content_type = event.raw_headers.get("Content-Type") or event.raw_headers.get("content-type") or ""
        if "multipart/report" in content_type.lower():
            return "filtered_t1_bounce"
    return None


def check_system_notification(event: CommunicationEvent, patterns: list[str]) -> str | None:
    """Sender matches configurable system-notification patterns."""
    sender = (event.sender_email or "").lower()
    for pattern in patterns:
        if pattern.lower() in sender:
            return "filtered_t1_system_notification"
    return None


def check_empty_body(event: CommunicationEvent, min_chars: int) -> str | None:
    """Body is empty or below threshold after HTML stripping."""
    text = event.body_plain or ""
    if not text and event.body_html:
        text = _strip_html(event.body_html)
    elif event.body_html:
        # Also check stripped HTML in case body_plain is whitespace
        stripped = _strip_html(event.body_html)
        if len(stripped) < len(text):
            text = stripped
    if len(text.strip()) < min_chars:
        return "filtered_t1_empty_body"
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Map from rule name → callable. The callable signature is determined
# at dispatch time; some rules need extra args.
_RULE_DISPATCH: dict[str, str] = {
    "duplicate_message_id": "duplicate_message_id",
    "auto_reply": "auto_reply",
    "newsletter": "newsletter",
    "calendar_invite": "calendar_invite",
    "bounce": "bounce",
    "system_notification": "system_notification",
    "empty_body": "empty_body",
}


def run_tier1(
    event: CommunicationEvent,
    config: Tier1Config,
    *,
    seen_ids: set[str],
) -> str | None:
    """Evaluate Tier 1 rules in config-specified precedence order.

    Returns the first matching outcome label, or None if no rule fires.
    """
    for rule_name in config.rule_order:
        rule_cfg = getattr(config, rule_name, None)
        if rule_cfg is not None and not rule_cfg.enabled:
            continue

        if rule_name == "duplicate_message_id":
            result = check_duplicate_message_id(event, seen_ids)
        elif rule_name == "auto_reply":
            result = check_auto_reply(event)
        elif rule_name == "newsletter":
            result = check_newsletter(event)
        elif rule_name == "calendar_invite":
            result = check_calendar_invite(event)
        elif rule_name == "bounce":
            result = check_bounce(event)
        elif rule_name == "system_notification":
            patterns = config.system_notification.patterns if hasattr(config, "system_notification") else []
            result = check_system_notification(event, patterns)
        elif rule_name == "empty_body":
            min_chars = config.empty_body.min_chars_after_html_strip if hasattr(config, "empty_body") else 20
            result = check_empty_body(event, min_chars)
        else:
            continue

        if result is not None:
            return result

    return None
