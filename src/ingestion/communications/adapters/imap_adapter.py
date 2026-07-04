"""IMAP live adapter — async IMAP via aioimaplib with UID/UIDVALIDITY tracking.

Chunk 57, CP6, D424. Registered as ``@register_adapter("imap")``.
Credential precedence: (1) os.environ[config.app_password_env] if set;
(2) inline config.password. Neither -> AdapterAuthError(error_class="auth_invalid").
"""

from __future__ import annotations

import email
import email.utils
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

import aioimaplib
import structlog

from src.ingestion.adapter_base import (
    AdapterAuthError,
    AdapterResult,
    AdapterTransientError,
    EmailAdapter,
    header_str,
)
from src.ingestion.adapter_registry import register_adapter
from src.ingestion.models import (
    AttachmentRef,
    CommunicationEvent,
    ImapSourceConfig,
    IngestionCheckpoint,
    Recipient,
    SourceConfig,
)

logger = structlog.get_logger()


def _parse_imap_recipients(msg: email.message.Message, header: str, role: str) -> list[Recipient]:
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


def _parse_imap_date(msg: email.message.Message) -> datetime | None:
    """Parse the Date header into a UTC datetime."""
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


def _get_imap_body(msg: email.message.Message) -> tuple[str | None, str | None]:
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


def _extract_imap_attachments(msg: email.message.Message) -> list[AttachmentRef]:
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


@register_adapter("imap")
class ImapAdapter(EmailAdapter):
    """Async IMAP adapter using aioimaplib with UID/UIDVALIDITY tracking."""

    source_type = "imap"

    def __init__(self, config: SourceConfig | None = None) -> None:
        super().__init__(config)
        self._client = None
        self._uidvalidity: int = 0
        self._last_uid: int = 0
        self._source_id = uuid4()
        self._password: str | None = None

    def _resolve_credentials(self, config: ImapSourceConfig) -> str:
        """Resolve password from env var or inline config.

        Precedence: (1) os.environ[config.app_password_env] if set;
        (2) inline config.password. Neither -> AdapterAuthError.
        """
        # Check app_password_env first (cloud IMAP)
        app_env = getattr(config, "app_password_env", None)
        if app_env:
            env_val = os.environ.get(app_env)
            if env_val:
                return env_val

        # Inline password
        if config.password:
            return config.password

        raise AdapterAuthError(
            "No IMAP credentials: neither app_password_env nor password set",
            error_class="auth_invalid",
        )

    async def connect(self, source_config: SourceConfig) -> None:
        """Open IMAP connection and SELECT INBOX to get UIDVALIDITY."""
        assert isinstance(source_config, ImapSourceConfig)
        self.config = source_config
        self._password = self._resolve_credentials(source_config)

        try:
            if source_config.use_ssl:
                self._client = aioimaplib.IMAP4_SSL(
                    host=source_config.host,
                    port=source_config.port,
                )
            else:
                self._client = aioimaplib.IMAP4(
                    host=source_config.host,
                    port=source_config.port,
                )

            await self._client.wait_hello_from_server()
            response = await self._client.login(source_config.username, self._password)
            if response.result != "OK":
                raise AdapterAuthError(
                    f"IMAP login failed: {response.lines}",
                    error_class="auth_invalid",
                )

            # SELECT INBOX to get UIDVALIDITY
            select_resp = await self._client.select("INBOX")
            if select_resp.result != "OK":
                raise AdapterTransientError(
                    f"IMAP SELECT failed: {select_resp.lines}",
                    error_class="connection_error",
                )

            # Parse UIDVALIDITY from response
            for line in select_resp.lines:
                if "UIDVALIDITY" in str(line):
                    # Extract numeric value
                    parts = str(line).split("UIDVALIDITY")
                    if len(parts) > 1:
                        val_str = parts[1].strip().rstrip("]").strip()
                        try:
                            new_uidvalidity = int(val_str)
                        except ValueError:
                            continue
                        if self._uidvalidity != 0 and new_uidvalidity != self._uidvalidity:
                            # UIDVALIDITY changed — full resync
                            logger.warning(
                                "imap_uidvalidity_changed",
                                old=self._uidvalidity,
                                new=new_uidvalidity,
                            )
                            self._last_uid = 0
                        self._uidvalidity = new_uidvalidity
                        break

        except AdapterAuthError:
            raise
        except AdapterTransientError:
            raise
        except Exception as exc:
            raise AdapterTransientError(
                f"IMAP connection failed: {exc}",
                error_class="connection_error",
            ) from exc

    async def list_messages(self, *, limit: int | None = None) -> AsyncIterator[str]:
        """Yield UIDs from INBOX starting after last checkpoint."""
        assert self._client is not None
        # Search for messages with UID > last_uid
        search_criteria = f"UID {self._last_uid + 1}:*"
        response = await self._client.uid_search(search_criteria)
        if response.result != "OK":
            return

        uids = []
        for line in response.lines:
            line_str = str(line).strip()
            if line_str:
                uids.extend(line_str.split())

        # Filter out non-numeric and UIDs <= last_uid
        valid_uids = []
        for uid in uids:
            try:
                uid_int = int(uid)
                if uid_int > self._last_uid:
                    valid_uids.append(uid)
            except ValueError:
                continue

        if limit is not None:
            valid_uids = valid_uids[:limit]

        for uid in valid_uids:
            yield uid

    async def parse_message(self, message_id: str) -> AdapterResult:
        """Fetch and parse a single IMAP message by UID."""
        assert self._client is not None
        warnings: list[str] = []

        response = await self._client.uid("fetch", message_id, "(RFC822)")
        if response.result != "OK":
            raise AdapterTransientError(
                f"IMAP FETCH failed for UID {message_id}",
                error_class="parse_error",
            )

        # Parse the raw email
        raw_bytes = None
        for line in response.lines:
            if isinstance(line, bytes):
                raw_bytes = line
                break
            elif isinstance(line, tuple) and len(line) > 1:
                raw_bytes = line[1] if isinstance(line[1], bytes) else None
                break

        if raw_bytes is None:
            raise AdapterTransientError(
                f"No message body for UID {message_id}",
                error_class="parse_error",
            )

        msg = email.message_from_bytes(raw_bytes)

        recipients = (
            _parse_imap_recipients(msg, "To", "to")
            + _parse_imap_recipients(msg, "Cc", "cc")
            + _parse_imap_recipients(msg, "Bcc", "bcc")
        )

        from_header = msg.get("From", "")
        sender_pairs = email.utils.getaddresses([from_header])
        sender_display = None
        sender_email_addr = "unknown@unknown"
        if sender_pairs:
            name, addr = sender_pairs[0]
            sender_email_addr = addr or "unknown@unknown"
            sender_display = name if name else None

        plain, html_body = _get_imap_body(msg)
        attachments = _extract_imap_attachments(msg)
        sent_at = _parse_imap_date(msg)
        msg_id = msg.get("Message-ID", f"<generated-{uuid4()}@grace>")
        in_reply_to = msg.get("In-Reply-To")
        references_header = msg.get("References", "")
        references = references_header.split() if references_header.strip() else []

        # Track UID
        self._last_uid = int(message_id)

        event = CommunicationEvent(
            source_id=self._source_id,
            message_id=msg_id,
            # F-23 (validation run): normalize non-ASCII / RFC 2047 headers
            # to plain str so em-dash/accent subjects don't crash the pull.
            subject=header_str(msg.get("Subject")),
            sender_email=sender_email_addr,
            sender_display_name=sender_display,
            recipients=recipients,
            sent_at=sent_at,
            body_plain=plain,
            body_html=html_body,
            attachments=attachments,
            in_reply_to=in_reply_to,
            references=references,
            source_type="imap",
        )
        return AdapterResult(
            event=event,
            warnings=warnings,
            checkpoint_value=f"{self._uidvalidity}:{self._last_uid}",
        )

    def checkpoint(self) -> IngestionCheckpoint:
        """Return a UID/UIDVALIDITY compound checkpoint."""
        return IngestionCheckpoint(
            checkpoint_type="uid_validity",
            value=f"{self._uidvalidity}:{self._last_uid}",
        )

    async def close(self) -> None:
        """Close the IMAP connection."""
        if self._client is not None:
            try:
                await self._client.logout()
            except Exception:
                pass
            self._client = None
