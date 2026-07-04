"""Outlook .msg file adapter — wraps python-oxmsg (MIT, D418).

Chunk 55, D419. Registered unconditionally via ``@register_adapter("msg")``
so that ``get_adapter("msg", config)`` never raises KeyError. When
``python-oxmsg`` is not installed, ``connect()`` raises RuntimeError with
actionable install instructions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from src.ingestion.adapter_base import AdapterResult, EmailAdapter
from src.ingestion.adapter_registry import register_adapter
from src.ingestion.models import (
    AttachmentRef,
    CommunicationEvent,
    IngestionCheckpoint,
    MsgSourceConfig,
    Recipient,
    SourceConfig,
)

try:
    import oxmsg

    _OXMSG_AVAILABLE = True
except ImportError:
    _OXMSG_AVAILABLE = False


@register_adapter("msg")
class MsgAdapter(EmailAdapter):
    """Adapter for Outlook .msg files via python-oxmsg."""

    source_type = "msg"

    def __init__(self, config: SourceConfig | None = None) -> None:
        super().__init__(config)
        self._files: list[Path] = []
        self._index: int = 0
        self._source_id = uuid4()

    async def connect(self, source_config: SourceConfig) -> None:
        if not _OXMSG_AVAILABLE:
            raise RuntimeError(
                "python-oxmsg not installed — run "
                "pip install --break-system-packages python-oxmsg"
            )
        assert isinstance(source_config, MsgSourceConfig)
        self.config = source_config
        directory = Path(source_config.directory_path)
        if not directory.is_dir():
            raise FileNotFoundError(f"MSG directory not found: {directory}")
        self._files = sorted(directory.glob("*.msg"))

    async def list_messages(self, *, limit: int | None = None) -> AsyncIterator[str]:
        files = self._files
        if limit is not None:
            files = files[:limit]
        for f in files:
            yield str(f)

    async def parse_message(self, message_id: str) -> AdapterResult:
        if not _OXMSG_AVAILABLE:
            raise RuntimeError(
                "python-oxmsg not installed — run "
                "pip install --break-system-packages python-oxmsg"
            )

        path = Path(message_id)
        msg = oxmsg.Message(path)
        warnings: list[str] = []

        # Recipients
        recipients: list[Recipient] = []
        for r in getattr(msg, "recipients", []):
            addr = getattr(r, "email_address", None) or getattr(r, "smtp_address", None)
            if addr:
                role = "to"
                rtype = getattr(r, "recipient_type", None)
                if rtype and "cc" in str(rtype).lower():
                    role = "cc"
                elif rtype and "bcc" in str(rtype).lower():
                    role = "bcc"
                recipients.append(
                    Recipient(
                        email=addr,
                        display_name=getattr(r, "display_name", None),
                        role=role,
                    )
                )

        # Attachments
        attachments: list[AttachmentRef] = []
        for att in getattr(msg, "attachments", []):
            filename = getattr(att, "filename", None) or getattr(att, "display_name", "unnamed")
            data = getattr(att, "data", b"") or b""
            mime = getattr(att, "mime_type", "application/octet-stream")
            attachments.append(
                AttachmentRef(filename=filename, mime_type=mime, size_bytes=len(data))
            )

        sender = getattr(msg, "sender_email", None) or getattr(msg, "sender_smtp_address", "unknown@unknown")
        sender_name = getattr(msg, "sender_name", None)
        msg_id_val = getattr(msg, "message_id", None) or f"<generated-{uuid4()}@grace>"
        subject = getattr(msg, "subject", None)
        body = getattr(msg, "body", None)
        sent_at = getattr(msg, "sent_date", None)

        self._index += 1

        event = CommunicationEvent(
            source_id=self._source_id,
            message_id=msg_id_val,
            subject=subject,
            sender_email=sender,
            sender_display_name=sender_name,
            recipients=recipients,
            sent_at=sent_at,
            body_plain=body,
            attachments=attachments,
            # D539 follow-up: capture raw_headers so header-based Tier 1 detectors work
            # for .msg sources. oxmsg exposes the RFC transport headers as a {name:value}
            # dict via `message_headers` (may be empty for some Outlook messages). Coerce
            # to a real dict-or-None so a non-dict value never reaches the Pydantic field.
            raw_headers=(
                _mh if isinstance(_mh := getattr(msg, "message_headers", None), dict) else None
            ),
            source_type="msg",
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
