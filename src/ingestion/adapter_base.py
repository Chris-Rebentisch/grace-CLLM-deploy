"""EmailAdapter ABC — five-method lifecycle contract for ingestion adapters.

Mirrors ``src/connectors/base.py`` __init_subclass__ guard pattern.
Chunk 55, D419. Chunk 57 adds ``AdapterError`` hierarchy (5 subclasses).
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from src.ingestion.models import CommunicationEvent, IngestionCheckpoint, SourceConfig


def header_str(value: object) -> str | None:
    """Normalize a stdlib email header value to plain ``str``.

    F-23 (validation run, 2026-07-01): with the compat32 policy,
    ``msg.get("Subject")`` returns an ``email.header.Header`` object (not a
    ``str``) whenever the raw header contains non-ASCII or RFC 2047 encoded
    words. ``CommunicationEvent.subject`` and the ``raw_headers`` JSONB column
    both require plain strings, so any real-world subject with an em-dash,
    accent, or emoji crashed the whole pull. This shared helper (originally
    ``_header_str`` in eml_adapter.py) is imported by the sibling stdlib-based
    adapters (mbox/imap/gmail) so they all decode headers uniformly.
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
                enc = charset if charset and charset != "unknown-8bit" else "utf-8"
                parts.append(chunk.decode(enc, errors="replace"))
            else:
                parts.append(chunk)
        return "".join(parts)
    except Exception:  # noqa: BLE001 — never let one bad header kill the pull
        return str(value)


# ---------------------------------------------------------------------------
# AdapterError hierarchy (Chunk 57, CP5)
# ---------------------------------------------------------------------------


class AdapterError(Exception):
    """Base adapter exception — carries ``error_class`` for structured dispatch."""

    def __init__(self, message: str = "", *, error_class: str = "unknown") -> None:
        super().__init__(message)
        self.error_class = error_class


class AdapterAuthError(AdapterError):
    """Authentication failure — OAuth refresh or invalid credentials."""

    def __init__(
        self,
        message: str = "",
        *,
        error_class: Literal["oauth_refresh_failed", "auth_invalid"] = "auth_invalid",
    ) -> None:
        super().__init__(message, error_class=error_class)


class AdapterRateLimitError(AdapterError):
    """Provider rate limit (HTTP 429)."""

    def __init__(self, message: str = "", *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message, error_class="rate_limited")
        self.retry_after_seconds = retry_after_seconds


class AdapterCursorExpiredError(AdapterError):
    """Cursor/token expired — adapter must full-resync."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message, error_class="cursor_expired")


class AdapterTransientError(AdapterError):
    """Transient network or parse failure — retryable."""

    def __init__(
        self,
        message: str = "",
        *,
        error_class: Literal["connection_error", "parse_error"] = "connection_error",
    ) -> None:
        super().__init__(message, error_class=error_class)


class AdapterFatalError(AdapterError):
    """Unrecoverable adapter failure."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message, error_class="unknown")


# ---------------------------------------------------------------------------
# AdapterResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class AdapterResult:
    """Single-message parse result from an adapter."""

    event: CommunicationEvent
    warnings: list[str] = field(default_factory=list)
    checkpoint_value: str | None = None


class EmailAdapter(abc.ABC):
    """Abstract base class defining the five-method adapter contract (D419).

    Every concrete adapter must:
    1. Set ``source_type`` as a class attribute.
    2. Implement all five abstract methods.
    """

    source_type: str

    def __init__(self, config: SourceConfig | None = None) -> None:
        self.config = config

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "source_type", None):
            raise TypeError(
                f"EmailAdapter subclass {cls.__name__} must define a "
                f"'source_type' class attribute."
            )

    @abc.abstractmethod
    async def connect(self, source_config: SourceConfig) -> None:
        """Open the source for reading."""
        ...

    @abc.abstractmethod
    async def list_messages(self, *, limit: int | None = None) -> AsyncIterator[str]:
        """Yield message identifiers from the source."""
        ...  # pragma: no cover

    @abc.abstractmethod
    async def parse_message(self, message_id: str) -> AdapterResult:
        """Parse a single message and return an AdapterResult."""
        ...

    @abc.abstractmethod
    def checkpoint(self) -> IngestionCheckpoint:
        """Return the current adapter checkpoint for resumable ingestion."""
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        """Release any resources held by the adapter."""
        ...
