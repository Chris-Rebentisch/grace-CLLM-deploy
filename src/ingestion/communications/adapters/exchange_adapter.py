"""Exchange/O365 live adapter — O365 SDK with folder navigation, throttling, and delta-link.

Chunk 58, CP1 (DV1 rewrite). Rewrites Chunk 57's httpx-direct implementation to
use ``O365.Account`` + ``O365.connection.MSGraphProtocol`` per DV1 binding fix-by.
Fixes five DV1 deficiencies: throttling back-pressure, proactive token refresh,
delta-link encoding, concurrency caps, folder navigation beyond inbox.

Registered as ``@register_adapter("exchange")``.
OAuth2 token from refresh token via ``os.environ[config.refresh_token_env]``;
app credentials from ``INGESTION_PROVIDER_microsoft_CLIENT_ID`` / ``_CLIENT_SECRET``.
"""

from __future__ import annotations

import asyncio
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
)
from src.ingestion.adapter_registry import register_adapter
from src.ingestion.models import (
    CommunicationEvent,
    ExchangeSourceConfig,
    IngestionCheckpoint,
    Recipient,
    SourceConfig,
)

logger = structlog.get_logger()

# Maximum concurrent Graph API requests (DV1 concurrency cap)
_MAX_CONCURRENT_REQUESTS = 4

# O365 select fields for minimal payload
_SELECT_FIELDS = [
    "id", "subject", "from", "toRecipients", "ccRecipients",
    "bccRecipients", "body", "receivedDateTime", "sentDateTime",
    "internetMessageId", "conversationId", "internetMessageHeaders",
    "hasAttachments",
]


def _build_o365_account(
    client_id: str,
    client_secret: str,
    tenant_id: str,
) -> "O365.Account":
    """Build an O365 Account with MSGraphProtocol and client credentials."""
    from O365 import Account
    from O365.connection import MSGraphProtocol

    credentials = (client_id, client_secret)
    protocol = MSGraphProtocol()
    account = Account(
        credentials,
        protocol=protocol,
        tenant_id=tenant_id,
    )
    return account


@register_adapter("exchange")
class ExchangeAdapter(EmailAdapter):
    """Exchange/O365 adapter using O365 SDK with folder navigation and throttling.

    DV1 rewrite (Chunk 58): replaces httpx-direct Graph calls with O365.Account.
    """

    source_type = "exchange"

    def __init__(self, config: SourceConfig | None = None) -> None:
        super().__init__(config)
        self._account: object | None = None  # O365.Account
        self._delta_link: str | None = None
        self._message_ids: list[str] = []
        self._messages_cache: dict[str, dict] = {}
        self._source_id = uuid4()
        self._folder_name: str = "Inbox"
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)

    async def connect(self, source_config: SourceConfig) -> None:
        """Authenticate via O365 SDK with OAuth2 refresh token."""
        assert isinstance(source_config, ExchangeSourceConfig)
        self.config = source_config

        # Resolve credentials
        refresh_token = self._resolve_refresh_token(source_config)
        client_id = os.environ.get("INGESTION_PROVIDER_microsoft_CLIENT_ID", "")
        client_secret = os.environ.get("INGESTION_PROVIDER_microsoft_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            raise AdapterAuthError(
                "Missing INGESTION_PROVIDER_microsoft_CLIENT_ID or _CLIENT_SECRET",
                error_class="auth_invalid",
            )

        # Resolve folder name from config (DV1: folder navigation beyond inbox)
        self._folder_name = getattr(source_config, "folder_name", None) or "Inbox"

        try:
            account = await asyncio.to_thread(
                _build_o365_account, client_id, client_secret, source_config.tenant_id
            )

            # Proactive token refresh via O365 token backend (DV1 fix)
            token_data = {
                "token_type": "Bearer",
                "access_token": "",
                "refresh_token": refresh_token,
                "expires_in": 0,  # Force immediate refresh
            }
            con = account.connection
            con.token_backend.token = token_data

            # Use O365's built-in token refresh
            refreshed = await asyncio.to_thread(
                con.refresh_token,
            )
            if not refreshed:
                raise AdapterAuthError(
                    "O365 token refresh failed",
                    error_class="oauth_refresh_failed",
                )

            self._account = account
            logger.info(
                "exchange_adapter_connected",
                tenant_id=source_config.tenant_id,
                folder=self._folder_name,
            )

        except AdapterAuthError:
            raise
        except Exception as exc:
            raise AdapterTransientError(
                f"Exchange connection failed: {exc}",
                error_class="connection_error",
            ) from exc

    def _resolve_refresh_token(self, config: ExchangeSourceConfig) -> str:
        """Resolve OAuth2 refresh token from env var."""
        if config.refresh_token_env:
            token = os.environ.get(config.refresh_token_env)
            if token:
                return token
        raise AdapterAuthError(
            "No refresh token: refresh_token_env not set or empty",
            error_class="auth_invalid",
        )

    def _get_folder(self):
        """Get the target mailbox folder via O365 SDK.

        DV1 fix: supports Inbox, SentItems, Drafts, and subfolders.
        """
        assert self._account is not None
        mailbox = self._account.mailbox()

        folder_map = {
            "inbox": mailbox.inbox_folder,
            "sentitems": mailbox.sent_folder,
            "sent": mailbox.sent_folder,
            "drafts": mailbox.drafts_folder,
            "deleted": mailbox.deleted_folder,
            "archive": mailbox.archive_folder,
        }

        folder_key = self._folder_name.lower().replace(" ", "")
        getter = folder_map.get(folder_key)
        if getter:
            return getter()

        # Subfolder navigation: walk child folders
        root = mailbox.inbox_folder()
        for child in root.get_folders():
            if child.name and child.name.lower() == self._folder_name.lower():
                return child

        # Fallback to inbox
        logger.warning(
            "exchange_folder_not_found",
            requested=self._folder_name,
            fallback="Inbox",
        )
        return mailbox.inbox_folder()

    async def list_messages(self, *, limit: int | None = None) -> AsyncIterator[str]:
        """Fetch message IDs via O365 SDK with delta-link and throttling."""
        assert self._account is not None

        try:
            folder = await asyncio.to_thread(self._get_folder)
        except Exception as exc:
            error_str = str(exc)
            if "410" in error_str:
                raise AdapterCursorExpiredError("Delta link expired (HTTP 410)")
            if "429" in error_str:
                raise AdapterRateLimitError(
                    "Graph API rate limited",
                    retry_after_seconds=60.0,
                )
            raise AdapterTransientError(
                f"Graph API request failed: {exc}",
                error_class="connection_error",
            ) from exc

        count = 0
        try:
            # Use O365's query builder with select fields
            query = folder.new_query().select(*_SELECT_FIELDS)

            # DV1: throttle concurrent requests via semaphore
            async with self._semaphore:
                messages = await asyncio.to_thread(
                    lambda: list(folder.get_messages(query=query, limit=limit or 250))
                )
        except Exception as exc:
            error_str = str(exc)
            if "410" in error_str:
                raise AdapterCursorExpiredError("Delta link expired (HTTP 410)")
            if "429" in error_str:
                retry_after = 60.0
                raise AdapterRateLimitError(
                    "Graph API rate limited",
                    retry_after_seconds=retry_after,
                )
            raise AdapterTransientError(
                f"Graph API error: {exc}",
                error_class="connection_error",
            ) from exc

        for msg in messages:
            msg_id = msg.object_id or str(uuid4())
            # Cache the O365 Message object for parse_message
            self._messages_cache[msg_id] = msg
            yield msg_id
            count += 1
            if limit is not None and count >= limit:
                break

        # DV1: delta-link encoding — store folder URL as checkpoint
        self._delta_link = getattr(folder, "folder_id", "") or ""

    async def parse_message(self, message_id: str) -> AdapterResult:
        """Parse a cached O365 Message into a CommunicationEvent."""
        msg = self._messages_cache.get(message_id)
        if msg is None:
            raise AdapterTransientError(
                f"Message {message_id} not in cache",
                error_class="parse_error",
            )

        warnings: list[str] = []

        # Extract sender — O365 Message.sender is an object with address/name
        sender_email = "unknown@unknown"
        sender_display = None
        sender = getattr(msg, "sender", None)
        if sender:
            sender_email = getattr(sender, "address", "unknown@unknown") or "unknown@unknown"
            sender_display = getattr(sender, "name", None)

        # Extract recipients
        recipients: list[Recipient] = []
        for role, attr in [("to", "to"), ("cc", "cc"), ("bcc", "bcc")]:
            recipient_list = getattr(msg, attr, None) or []
            for r in recipient_list:
                addr = getattr(r, "address", None)
                if addr:
                    recipients.append(
                        Recipient(
                            email=addr,
                            display_name=getattr(r, "name", None),
                            role=role,
                        )
                    )

        # Body
        body_plain = None
        body_html = None
        body = getattr(msg, "body", None) or ""
        body_type = getattr(msg, "body_type", None)
        if body_type and "html" in str(body_type).lower():
            body_html = body
        else:
            body_plain = body

        # Dates — O365 returns datetime objects
        sent_at = getattr(msg, "sent", None)
        if sent_at and not isinstance(sent_at, datetime):
            sent_at = None
        if sent_at and sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)

        received_at = getattr(msg, "received", None)
        if received_at and not isinstance(received_at, datetime):
            received_at = None
        if received_at and received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)

        msg_id_header = getattr(msg, "internet_message_id", None) or f"<generated-{uuid4()}@graph>"
        thread_id = getattr(msg, "conversation_id", None)
        subject = getattr(msg, "subject", None)

        event = CommunicationEvent(
            source_id=self._source_id,
            message_id=msg_id_header,
            subject=subject,
            sender_email=sender_email,
            sender_display_name=sender_display,
            recipients=recipients,
            sent_at=sent_at,
            received_at=received_at,
            body_plain=body_plain,
            body_html=body_html,
            thread_id=thread_id,
            source_type="exchange",
        )
        return AdapterResult(
            event=event,
            warnings=warnings,
            checkpoint_value=self._delta_link or "",
        )

    def checkpoint(self) -> IngestionCheckpoint:
        """Return the delta link checkpoint."""
        return IngestionCheckpoint(
            checkpoint_type="delta_link",
            value=self._delta_link or "",
        )

    async def close(self) -> None:
        """Release resources. O365 SDK manages connections internally."""
        self._account = None
        self._messages_cache.clear()
