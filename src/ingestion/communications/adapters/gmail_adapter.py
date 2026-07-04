"""Gmail live adapter — Gmail API v1 with historyId tracking.

Chunk 57, CP8, D424. Registered as ``@register_adapter("gmail")``.
Token from refresh token via ``os.environ[config.refresh_token_env]``;
app credentials from ``INGESTION_PROVIDER_google_CLIENT_ID`` / ``_CLIENT_SECRET``.
"""

from __future__ import annotations

import base64
import email
import email.utils
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

import structlog

from src.ingestion.adapter_base import (
    AdapterAuthError,
    AdapterCursorExpiredError,
    AdapterRateLimitError,
    AdapterResult,
    AdapterTransientError,
    EmailAdapter,
    header_str,
)
from src.ingestion.adapter_registry import register_adapter
from src.ingestion.models import (
    AttachmentRef,
    CommunicationEvent,
    GmailSourceConfig,
    IngestionCheckpoint,
    Recipient,
    SourceConfig,
)

logger = structlog.get_logger()


@register_adapter("gmail")
class GmailAdapter(EmailAdapter):
    """Gmail adapter using Gmail API v1 with historyId-based delta sync."""

    source_type = "gmail"

    def __init__(self, config: SourceConfig | None = None) -> None:
        super().__init__(config)
        self._service = None
        self._history_id: str = ""
        self._message_ids: list[str] = []
        self._source_id = uuid4()

    async def connect(self, source_config: SourceConfig) -> None:
        """Authenticate via OAuth2 refresh token and build Gmail API service."""
        assert isinstance(source_config, GmailSourceConfig)
        self.config = source_config

        refresh_token = self._resolve_refresh_token(source_config)
        client_id = os.environ.get("INGESTION_PROVIDER_google_CLIENT_ID", "")
        client_secret = os.environ.get("INGESTION_PROVIDER_google_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            raise AdapterAuthError(
                "Missing INGESTION_PROVIDER_google_CLIENT_ID or _CLIENT_SECRET",
                error_class="auth_invalid",
            )

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            )
            self._service = build("gmail", "v1", credentials=creds)
        except Exception as exc:
            if "invalid_grant" in str(exc).lower() or "unauthorized" in str(exc).lower():
                raise AdapterAuthError(
                    f"Gmail token refresh failed: {exc}",
                    error_class="oauth_refresh_failed",
                ) from exc
            raise AdapterTransientError(
                f"Gmail connection failed: {exc}",
                error_class="connection_error",
            ) from exc

    def _resolve_refresh_token(self, config: GmailSourceConfig) -> str:
        """Resolve OAuth2 refresh token from env var."""
        if config.refresh_token_env:
            token = os.environ.get(config.refresh_token_env)
            if token:
                return token
        raise AdapterAuthError(
            "No refresh token: refresh_token_env not set or empty",
            error_class="auth_invalid",
        )

    async def list_messages(self, *, limit: int | None = None) -> AsyncIterator[str]:
        """List message IDs via Gmail API, using historyId for delta sync."""
        assert self._service is not None

        try:
            if self._history_id:
                # Delta sync via history.list
                try:
                    history_results = (
                        self._service.users()
                        .history()
                        .list(userId="me", startHistoryId=self._history_id, historyTypes=["messageAdded"])
                        .execute()
                    )
                except Exception as exc:
                    error_str = str(exc)
                    if "404" in error_str or "410" in error_str:
                        # historyId expired — full sync
                        logger.warning("gmail_history_id_expired", history_id=self._history_id)
                        self._history_id = ""
                        raise AdapterCursorExpiredError(
                            "Gmail historyId expired, full resync needed"
                        ) from exc
                    elif "429" in error_str:
                        raise AdapterRateLimitError("Gmail API rate limited") from exc
                    raise

                # Update history ID
                if "historyId" in history_results:
                    self._history_id = str(history_results["historyId"])

                count = 0
                for history_record in history_results.get("history", []):
                    for msg_added in history_record.get("messagesAdded", []):
                        msg_id = msg_added.get("message", {}).get("id")
                        if msg_id:
                            yield msg_id
                            count += 1
                            if limit is not None and count >= limit:
                                return
            else:
                # Full sync — list all messages
                page_token = None
                count = 0
                while True:
                    try:
                        results = (
                            self._service.users()
                            .messages()
                            .list(
                                userId="me",
                                maxResults=min(limit or 100, 100),
                                pageToken=page_token,
                            )
                            .execute()
                        )
                    except Exception as exc:
                        error_str = str(exc)
                        if "429" in error_str:
                            raise AdapterRateLimitError("Gmail API rate limited") from exc
                        raise

                    for msg in results.get("messages", []):
                        yield msg["id"]
                        count += 1
                        if limit is not None and count >= limit:
                            # Capture historyId from profile for next delta
                            try:
                                profile = self._service.users().getProfile(userId="me").execute()
                                self._history_id = str(profile.get("historyId", ""))
                            except Exception:
                                pass
                            return

                    page_token = results.get("nextPageToken")
                    if not page_token:
                        break

                # Get current historyId for next delta sync
                try:
                    profile = self._service.users().getProfile(userId="me").execute()
                    self._history_id = str(profile.get("historyId", ""))
                except Exception:
                    pass

        except (AdapterCursorExpiredError, AdapterRateLimitError):
            raise
        except Exception as exc:
            raise AdapterTransientError(
                f"Gmail list_messages failed: {exc}",
                error_class="connection_error",
            ) from exc

    async def parse_message(self, message_id: str) -> AdapterResult:
        """Fetch and parse a single Gmail message by ID."""
        assert self._service is not None
        warnings: list[str] = []

        try:
            msg_data = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="raw")
                .execute()
            )
        except Exception as exc:
            error_str = str(exc)
            if "429" in error_str:
                raise AdapterRateLimitError("Gmail API rate limited") from exc
            raise AdapterTransientError(
                f"Gmail fetch failed for {message_id}: {exc}",
                error_class="parse_error",
            ) from exc

        # Update historyId if present
        if "historyId" in msg_data:
            self._history_id = str(msg_data["historyId"])

        # Decode raw message
        raw_bytes = base64.urlsafe_b64decode(msg_data.get("raw", ""))
        msg = email.message_from_bytes(raw_bytes)

        # Extract fields
        recipients: list[Recipient] = []
        for header, role in [("To", "to"), ("Cc", "cc"), ("Bcc", "bcc")]:
            raw = msg.get_all(header, [])
            for value in raw:
                for display_name, addr in email.utils.getaddresses([value]):
                    if addr:
                        recipients.append(
                            Recipient(
                                email=addr,
                                display_name=display_name if display_name else None,
                                role=role,
                            )
                        )

        from_header = msg.get("From", "")
        sender_pairs = email.utils.getaddresses([from_header])
        sender_email_addr = "unknown@unknown"
        sender_display = None
        if sender_pairs:
            name, addr = sender_pairs[0]
            sender_email_addr = addr or "unknown@unknown"
            sender_display = name if name else None

        # Body
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

        sent_at = None
        date_str = msg.get("Date")
        if date_str:
            try:
                parsed = email.utils.parsedate_to_datetime(date_str)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                sent_at = parsed
            except (ValueError, TypeError):
                pass

        msg_id_header = msg.get("Message-ID", f"<generated-{uuid4()}@gmail>")
        in_reply_to = msg.get("In-Reply-To")
        references_header = msg.get("References", "")
        references = references_header.split() if references_header.strip() else []
        thread_id = msg_data.get("threadId")

        # Attachments
        attachments: list[AttachmentRef] = []
        if msg.is_multipart():
            for part in msg.walk():
                cd = part.get("Content-Disposition", "")
                if "attachment" in cd:
                    filename = part.get_filename() or "unnamed"
                    mime_type = part.get_content_type()
                    payload_bytes = part.get_payload(decode=True)
                    size = len(payload_bytes) if payload_bytes else 0
                    attachments.append(
                        AttachmentRef(filename=filename, mime_type=mime_type, size_bytes=size)
                    )

        event = CommunicationEvent(
            source_id=self._source_id,
            message_id=msg_id_header,
            # F-23 (validation run): normalize non-ASCII / RFC 2047 headers
            # to plain str so em-dash/accent subjects don't crash the pull. The
            # Gmail adapter parses the raw RFC822 payload with stdlib
            # email.message.Message, so it shares the compat32 Header bug.
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
            thread_id=thread_id,
            source_type="gmail",
        )
        return AdapterResult(
            event=event,
            warnings=warnings,
            checkpoint_value=self._history_id,
        )

    def checkpoint(self) -> IngestionCheckpoint:
        """Return the historyId checkpoint."""
        return IngestionCheckpoint(
            checkpoint_type="history_id",
            value=self._history_id,
        )

    async def close(self) -> None:
        """Release Gmail API service resources."""
        self._service = None
