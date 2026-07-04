"""Pydantic v2 models for the remote support session domain (Chunk 45, D372).

SupportSession maps to the ``support_sessions`` Postgres table.
SupportSessionCreate is the admin-facing creation request with
``expires_in_seconds`` cap (86400) and floor (3600).
SupportSessionResponse is the outbound shape that **never** returns
``token_hash`` (hash-at-rest contract).
TranscriptEntry and TranscriptResponse model the audit-export transcript
(D374 content-hash-only contract).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SupportSession(BaseModel):
    """Full database row for a support session.

    Internal model — not returned directly in API responses.
    ``token_hash`` is present here but excluded from the response model.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    granted_by_user_id: str
    granted_to_email: str
    granted_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    scope_tags: dict[str, Any] = Field(default_factory=lambda: {"all": True})
    created_via: str = "api"
    token_hash: str
    last_used_at: datetime | None = None


class SupportSessionCreate(BaseModel):
    """Admin-facing creation request for a support session (D372).

    ``expires_in_seconds`` is capped at 86400 (24h) and floored at 3600
    (1h). ``scope_tags`` is stored but not enforced in v1 (deferred to
    Chunk 46+).
    """

    model_config = ConfigDict(extra="forbid")

    granted_to_email: str = Field(
        min_length=1,
        description="Email address of the support operator receiving the token.",
    )
    expires_in_seconds: int = Field(
        default=14400,
        ge=3600,
        le=86400,
        description=(
            "Session duration in seconds. Floor 3600 (1h), cap 86400 (24h). "
            "Default 14400 (4h)."
        ),
    )
    scope_tags: dict[str, Any] = Field(
        default_factory=lambda: {"all": True},
        description="Scope tags for the session. Stored but not enforced in v1.",
    )
    created_via: str = Field(
        default="api",
        description="Creation channel: 'api' or 'cli'.",
    )


class SupportSessionResponse(BaseModel):
    """Outbound response model for a support session (D372).

    **Never** includes ``token_hash`` — the plaintext token is returned
    exactly once at issuance via a separate field.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    granted_by_user_id: str
    granted_to_email: str
    granted_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    scope_tags: dict[str, Any] = Field(default_factory=lambda: {"all": True})
    created_via: str = "api"
    last_used_at: datetime | None = None


class SupportSessionIssuanceResponse(BaseModel):
    """One-time issuance response that includes the plaintext token.

    After this response is returned, the plaintext token is never
    recoverable from the server.
    """

    model_config = ConfigDict(extra="forbid")

    session: SupportSessionResponse
    token: str = Field(
        description="Plaintext support token. Displayed exactly once.",
    )


class TranscriptEntry(BaseModel):
    """Single audit-trail entry in the support session transcript (D374).

    Content-hash-only: ``content_hash`` is SHA-256 of the response body;
    the actual body is **never** included.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    path: str
    method: str
    status_code: int
    content_hash: str = Field(
        description="SHA-256 hex of the response body (D374 content-hash-only).",
    )
    latency_ms: float | None = None
    graph_scope: str | None = None

    # Three-identity-layer per entry (D374).
    end_user: str | None = None
    agent_id: str | None = None
    agent_display_name: str | None = None
    support_operator_email: str | None = None

    refused: bool = Field(
        default=False,
        description="True if this entry was a 403 from a blocked route.",
    )


class TranscriptSummary(BaseModel):
    """Summary statistics for a support session transcript."""

    model_config = ConfigDict(extra="forbid")

    total_requests: int = Field(ge=0)
    distinct_routes: int = Field(ge=0)
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None


class TranscriptResponse(BaseModel):
    """Full transcript for a support session (D374).

    Contains per-request entries (content-hash-only), refused-route
    attempts, and summary statistics.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    entries: list[TranscriptEntry] = Field(default_factory=list)
    summary: TranscriptSummary


class SupportStatusResponse(BaseModel):
    """Public status response for ``GET /api/support/status`` (D372).

    When no active session exists: ``active=False``, all nullable fields
    are explicit ``null`` (never absent).
    """

    model_config = ConfigDict(extra="forbid")

    active: bool
    email: str | None = None
    expires_at: datetime | None = None
