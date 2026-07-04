"""Mbox file adapter — parses standard Unix mbox files via stdlib mailbox.

Chunk 55, D419. Registered as ``@register_adapter("mbox")``.
"""

from __future__ import annotations

import email
import email.utils
import mailbox
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.ingestion.adapter_base import AdapterResult, EmailAdapter, header_str
from src.ingestion.adapter_registry import register_adapter
from src.ingestion.models import (
    AttachmentRef,
    CommunicationEvent,
    IngestionCheckpoint,
    MboxSourceConfig,
    Recipient,
    SourceConfig,
)


def _parse_recipients(msg: mailbox.mboxMessage, header: str, role: str) -> list[Recipient]:
    """Extract recipients from a mail header."""
    raw = msg.get_all(header, [])
    results: list[Recipient] = []
    for value in raw:
        for display_name, addr in email.utils.getaddresses([value]):
            if addr:
                results.append(
                    Recipient(
                        email=addr,
                        display_name=display_name if display_name else None,
                        role=role,
                    )
                )
    return results


def _parse_date(msg: mailbox.mboxMessage) -> datetime | None:
    """Parse the Date header into a UTC datetime."""
    date_str = msg.get("Date")
    if not date_str:
        return None
    parsed = email.utils.parsedate_to_datetime(date_str)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _extract_attachments(msg: mailbox.mboxMessage) -> list[AttachmentRef]:
    """Extract attachment references from a multipart message."""
    attachments: list[AttachmentRef] = []
    if msg.is_multipart():
        for part in msg.walk():
            cd = part.get("Content-Disposition", "")
            if "attachment" in cd:
                filename = part.get_filename() or "unnamed"
                mime_type = part.get_content_type()
                payload = part.get_payload(decode=True)
                size = len(payload) if payload else 0
                attachments.append(
                    AttachmentRef(
                        filename=filename,
                        mime_type=mime_type,
                        size_bytes=size,
                    )
                )
    return attachments


def _get_body(msg: mailbox.mboxMessage) -> tuple[str | None, str | None]:
    """Extract plain and HTML bodies."""
    plain = None
    html_body = None
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = part.get("Content-Disposition", "")
            if "attachment" in cd:
                continue
            if ct == "text/plain" and plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    plain = payload.decode("utf-8", errors="replace")
            elif ct == "text/html" and html_body is None:
                payload = part.get_payload(decode=True)
                if payload:
                    html_body = payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            ct = msg.get_content_type()
            text = payload.decode("utf-8", errors="replace")
            if ct == "text/html":
                html_body = text
            else:
                plain = text
    return plain, html_body


@register_adapter("mbox")
class MboxAdapter(EmailAdapter):
    """Adapter for Unix mbox files using stdlib ``mailbox.mbox``."""

    source_type = "mbox"

    def __init__(self, config: SourceConfig | None = None) -> None:
        super().__init__(config)
        self._mbox: mailbox.mbox | None = None
        self._keys: list[str] = []
        self._offset: int = 0
        self._source_id = uuid4()

    async def connect(self, source_config: SourceConfig) -> None:
        """Open the mbox file."""
        assert isinstance(source_config, MboxSourceConfig)
        self.config = source_config
        path = Path(source_config.file_path)
        if not path.exists():
            raise FileNotFoundError(f"Mbox file not found: {path}")
        self._mbox = mailbox.mbox(str(path))
        self._keys = list(self._mbox.keys())

    async def list_messages(self, *, limit: int | None = None) -> AsyncIterator[str]:
        """Yield message keys from the mbox."""
        keys = self._keys
        if limit is not None:
            keys = keys[:limit]
        for key in keys:
            yield str(key)

    async def parse_message(self, message_id: str) -> AdapterResult:
        """Parse a single mbox message by key."""
        assert self._mbox is not None
        msg = self._mbox[int(message_id)]
        warnings: list[str] = []

        recipients = (
            _parse_recipients(msg, "To", "to")
            + _parse_recipients(msg, "Cc", "cc")
            + _parse_recipients(msg, "Bcc", "bcc")
        )

        # Sender
        from_header = msg.get("From", "")
        sender_pairs = email.utils.getaddresses([from_header])
        sender_display = None
        sender_email = "unknown@unknown"
        if sender_pairs:
            name, addr = sender_pairs[0]
            sender_email = addr or "unknown@unknown"
            sender_display = name if name else None

        plain, html_body = _get_body(msg)
        attachments = _extract_attachments(msg)
        sent_at = _parse_date(msg)
        msg_id = msg.get("Message-ID", f"<generated-{uuid4()}@grace>")

        in_reply_to = msg.get("In-Reply-To")
        references_header = msg.get("References", "")
        references = references_header.split() if references_header.strip() else []

        self._offset = int(message_id) + 1

        event = CommunicationEvent(
            source_id=self._source_id,
            message_id=msg_id,
            # F-23 (validation run): normalize non-ASCII / RFC 2047 headers
            # to plain str so em-dash/accent subjects don't crash the pull.
            subject=header_str(msg.get("Subject")),
            sender_email=sender_email,
            sender_display_name=sender_display,
            recipients=recipients,
            sent_at=sent_at,
            body_plain=plain,
            body_html=html_body,
            attachments=attachments,
            in_reply_to=in_reply_to,
            references=references,
            # D539 follow-up: capture raw_headers (was omitted) so header-based Tier 1
            # detectors (auto-reply / newsletter / calendar) work for mbox sources too
            # — and for PST, which delegates parsing to this adapter via PstPreconverter.
            # F-23: normalize each header value to plain str.
            raw_headers={k: header_str(v) for k, v in msg.items()},
            source_type="mbox",
        )
        return AdapterResult(
            event=event,
            warnings=warnings,
            checkpoint_value=str(self._offset),
        )

    def checkpoint(self) -> IngestionCheckpoint:
        """Return a file-offset checkpoint."""
        return IngestionCheckpoint(
            checkpoint_type="file_offset",
            value=str(self._offset),
        )

    async def close(self) -> None:
        """Close the mbox."""
        if self._mbox is not None:
            self._mbox.close()
            self._mbox = None
