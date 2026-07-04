"""EML file adapter — parses .eml files from a directory via stdlib email.

Chunk 55, D419. Registered as ``@register_adapter("eml")``.
"""

from __future__ import annotations

import email
import email.utils
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.ingestion.adapter_base import AdapterResult, EmailAdapter
from src.ingestion.adapter_registry import register_adapter
from src.ingestion.models import (
    AttachmentRef,
    CommunicationEvent,
    EmlSourceConfig,
    IngestionCheckpoint,
    Recipient,
    SourceConfig,
)


def _header_str(value: object) -> str | None:
    """Normalize a header value to plain ``str``.

    Constraint this encodes: with the stdlib compat32 policy,
    ``msg.get("Subject")`` returns an ``email.header.Header`` object (not a
    ``str``) whenever the raw header contains non-ASCII or RFC 2047 encoded
    words — and ``CommunicationEvent.subject`` / the ``raw_headers`` JSONB
    column both require plain strings. Any real-world subject with an
    em-dash, accent, or emoji otherwise crashes the whole pull
    (validation-run finding F-23, 2026-07-01).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        from email.header import decode_header

        parts: list[str] = []
        for chunk, charset in decode_header(str(value)):
            if isinstance(chunk, bytes):
                # Raw non-ASCII header bytes surface as charset None /
                # "unknown-8bit"; real-world mail is overwhelmingly UTF-8,
                # so try that before falling back to replacement chars.
                enc = charset if charset and charset != "unknown-8bit" else "utf-8"
                parts.append(chunk.decode(enc, errors="replace"))
            else:
                parts.append(chunk)
        return "".join(parts)
    except Exception:  # noqa: BLE001 — never let one bad header kill the pull
        return str(value)


def _parse_recipients_from_msg(msg: email.message.Message, header: str, role: str) -> list[Recipient]:
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


def _parse_date_from_msg(msg: email.message.Message) -> datetime | None:
    date_str = msg.get("Date")
    if not date_str:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None


@register_adapter("eml")
class EmlAdapter(EmailAdapter):
    """Adapter for .eml files in a directory."""

    source_type = "eml"

    def __init__(self, config: SourceConfig | None = None) -> None:
        super().__init__(config)
        self._files: list[Path] = []
        self._index: int = 0
        self._source_id = uuid4()

    async def connect(self, source_config: SourceConfig) -> None:
        assert isinstance(source_config, EmlSourceConfig)
        self.config = source_config
        directory = Path(source_config.directory_path)
        if not directory.is_dir():
            raise FileNotFoundError(f"EML directory not found: {directory}")
        self._files = sorted(directory.glob("*.eml"))

    async def list_messages(self, *, limit: int | None = None) -> AsyncIterator[str]:
        files = self._files
        if limit is not None:
            files = files[:limit]
        for f in files:
            yield str(f)

    async def parse_message(self, message_id: str) -> AdapterResult:
        path = Path(message_id)
        raw = path.read_bytes()
        msg = email.message_from_bytes(raw)
        warnings: list[str] = []

        recipients = (
            _parse_recipients_from_msg(msg, "To", "to")
            + _parse_recipients_from_msg(msg, "Cc", "cc")
            + _parse_recipients_from_msg(msg, "Bcc", "bcc")
        )

        from_header = msg.get("From", "")
        sender_pairs = email.utils.getaddresses([from_header])
        sender_display = None
        sender_email = "unknown@unknown"
        if sender_pairs:
            name, addr = sender_pairs[0]
            sender_email = addr or "unknown@unknown"
            sender_display = name if name else None

        # Body extraction
        plain = None
        html_body = None
        attachments: list[AttachmentRef] = []
        if msg.is_multipart():
            for part in msg.walk():
                cd = part.get("Content-Disposition", "")
                if "attachment" in cd:
                    filename = part.get_filename() or "unnamed"
                    payload = part.get_payload(decode=True)
                    attachments.append(
                        AttachmentRef(
                            filename=filename,
                            mime_type=part.get_content_type(),
                            size_bytes=len(payload) if payload else 0,
                        )
                    )
                    continue
                ct = part.get_content_type()
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
                plain = payload.decode("utf-8", errors="replace")

        msg_id = msg.get("Message-ID", f"<generated-{uuid4()}@grace>")
        sent_at = _parse_date_from_msg(msg)
        in_reply_to = msg.get("In-Reply-To")
        refs_header = msg.get("References", "")
        references = refs_header.split() if refs_header.strip() else []

        self._index += 1

        event = CommunicationEvent(
            source_id=self._source_id,
            message_id=msg_id,
            subject=_header_str(msg.get("Subject")),
            sender_email=sender_email,
            sender_display_name=sender_display,
            recipients=recipients,
            sent_at=sent_at,
            body_plain=plain,
            body_html=html_body,
            attachments=attachments,
            in_reply_to=in_reply_to,
            references=references,
            # D539 capture-the-why: the adapter previously omitted raw_headers, so
            # raw_headers_json persisted null and the header-based Tier 1 detectors
            # (auto-reply via Auto-Submitted, calendar via Content-Type, newsletter
            # via List-Unsubscribe) were dead for eml email — only sender-pattern
            # rules fired. Capture the parsed headers so those detectors work.
            raw_headers={k: _header_str(v) for k, v in msg.items()},
            source_type="eml",
        )
        return AdapterResult(
            event=event,
            warnings=warnings,
            checkpoint_value=str(self._index),
        )

    def checkpoint(self) -> IngestionCheckpoint:
        return IngestionCheckpoint(checkpoint_type="file_offset", value=str(self._index))

    async def close(self) -> None:
        self._files = []
