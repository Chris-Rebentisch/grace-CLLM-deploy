"""Session close routes — Chunk 27 (D193 hard lock, D202 ownership).

Owns:
  - POST /api/regeneration/close-summary
  - POST /api/regeneration/close-confirm

D193 Hard Lock. This module imports primitives from src.regeneration
unchanged:
  - :class:`src.regeneration.response_synthesizer.ResponseSynthesizer`
  - :class:`src.regeneration.regeneration_config.RegenSettings`
  - :class:`src.regeneration.regeneration_models.AssembledPrompt`

It does NOT modify any Chunk 23 file. The close-summary prompt template
lives as a module-level constant in THIS file (D193 + D202). Do not
move it into src/regeneration/* under any circumstance.

Close-confirm session storage is in-memory for v1 (spec §17.4). A
future chunk will persist sessions to a dedicated table.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.regeneration.regeneration_config import RegenSettings
from src.regeneration.regeneration_models import (
    AssembledPrompt,
    ClaimSpan,
)
from src.regeneration.response_synthesizer import ResponseSynthesizer

logger = structlog.get_logger()

router = APIRouter(prefix="/api/regeneration", tags=["regeneration:session"])


# =========================================================================
# D193/D202 prompt-template lock. This constant MUST live inside
# src/api/session_routes.py. Moving it to src/regeneration/* violates
# D193. Moving it to a new src/session_summary/* module violates D202.
# =========================================================================
CLOSE_SUMMARY_PROMPT_TEMPLATE: str = (
    "You are GrACE. The user has completed a knowledge-graph session.\n"
    "Summarize the conversation below as a first-person narrative from the\n"
    "assistant's perspective. Be faithful to what was actually discussed.\n"
    "Acknowledge gaps or unresolved items explicitly. Do not invent facts.\n"
    "Keep the tone reflective, not salesy. Plain prose, no bullet lists.\n"
    "Output only the narrative; no preamble."
)


# ----- Request / response models (mirrors frontend/lib/api/types.ts) -----


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    claim_spans: list[ClaimSpan] | None = None
    sent_at: datetime


class SessionSummary(BaseModel):
    narrative: str
    ontology_changes: list[dict[str, Any]] = Field(default_factory=list)
    cqs_flipped_state: list[dict[str, Any]] = Field(default_factory=list)
    decisions_recorded: list[dict[str, Any]] = Field(default_factory=list)
    deferred_items: list[dict[str, Any]] = Field(default_factory=list)
    certainty_band_shifts: list[dict[str, Any]] = Field(default_factory=list)


class CloseSummaryRequest(BaseModel):
    session_id: UUID
    phase_state: Literal["close"]
    messages: list[ChatMessage]
    phase_durations_ms: dict[str, int] = Field(default_factory=dict)


class CloseSummaryResponse(BaseModel):
    session_id: UUID
    summary: SessionSummary
    request_id: UUID


class CloseConfirmRequest(BaseModel):
    session_id: UUID
    final_summary: SessionSummary
    summary_edited: bool
    summary_rejected: bool


class CloseConfirmResponse(BaseModel):
    session_id: UUID
    session_status: Literal["closed"]
    recorded_at: datetime


# ----- In-memory session record store (v1, per spec §17.4) -----


class _SessionRecord(BaseModel):
    session_id: UUID
    final_summary: SessionSummary
    summary_edited: bool
    summary_rejected: bool
    recorded_at: datetime


_session_records_lock = threading.Lock()
_session_records: dict[UUID, _SessionRecord] = {}


def _conversation_to_prompt_context(messages: list[ChatMessage]) -> str:
    lines: list[str] = ["<conversation>"]
    for i, m in enumerate(messages, start=1):
        label = "User" if m.role == "user" else "Assistant"
        lines.append(f"[{i}] {label}: {m.content.strip()}")
    lines.append("</conversation>")
    return "\n".join(lines)


def _phase_duration_summary(durations: dict[str, int]) -> str:
    if not durations:
        return ""
    parts = [
        f"{k}={ms // 1000}s"
        for k, ms in durations.items()
        if isinstance(ms, int) and ms > 0
    ]
    return f"Phase durations: {', '.join(parts)}." if parts else ""


async def _generate_close_summary_narrative(
    request: CloseSummaryRequest,
) -> str:
    """Invoke the Chunk 23 ResponseSynthesizer primitive with the close
    summary template. The primitive is untouched; only the assembled
    prompt is novel.
    """
    settings = RegenSettings()
    synthesizer = ResponseSynthesizer(settings)

    context = _conversation_to_prompt_context(request.messages)
    duration_note = _phase_duration_summary(request.phase_durations_ms)
    user_query = (
        "Write a narrative summary of the conversation above."
        f" {duration_note}".strip()
    )

    context_chars = len(context)
    query_chars = len(user_query)
    chars_per_token = max(1, settings.chars_per_token)

    assembled = AssembledPrompt(
        system_prompt=CLOSE_SUMMARY_PROMPT_TEMPLATE,
        context=context,
        user_query=user_query,
        system_token_estimate=len(CLOSE_SUMMARY_PROMPT_TEMPLATE)
        // chars_per_token,
        context_token_estimate=context_chars // chars_per_token,
        query_token_estimate=query_chars // chars_per_token,
        total_token_estimate=(
            len(CLOSE_SUMMARY_PROMPT_TEMPLATE)
            + context_chars
            + query_chars
        )
        // chars_per_token,
        context_truncated=False,
        truncation_details=None,
        phase_style_applied="close",
    )

    response = await synthesizer.synthesize(assembled, overrides=None)
    return (response.text or "").strip()


@router.post(
    "/close-summary",
    response_model=CloseSummaryResponse,
    status_code=status.HTTP_200_OK,
)
async def post_close_summary(req: CloseSummaryRequest) -> CloseSummaryResponse:
    if req.phase_state != "close":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_type": "invalid_phase_state",
                "message": "close-summary requires phase_state='close'",
            },
        )
    logger.info(
        "session.close_summary_requested",
        session_id=str(req.session_id),
        message_count=len(req.messages),
    )
    narrative = await _generate_close_summary_narrative(req)
    return CloseSummaryResponse(
        session_id=req.session_id,
        request_id=uuid4(),
        summary=SessionSummary(narrative=narrative),
    )


@router.post(
    "/close-confirm",
    response_model=CloseConfirmResponse,
    status_code=status.HTTP_200_OK,
)
async def post_close_confirm(req: CloseConfirmRequest) -> CloseConfirmResponse:
    recorded_at = datetime.now(timezone.utc)
    with _session_records_lock:
        if req.session_id in _session_records:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_type": "session_already_closed",
                    "session_id": str(req.session_id),
                },
            )
        _session_records[req.session_id] = _SessionRecord(
            session_id=req.session_id,
            final_summary=req.final_summary,
            summary_edited=req.summary_edited,
            summary_rejected=req.summary_rejected,
            recorded_at=recorded_at,
        )
    logger.info(
        "session.close_confirmed",
        session_id=str(req.session_id),
        summary_edited=req.summary_edited,
        summary_rejected=req.summary_rejected,
    )
    return CloseConfirmResponse(
        session_id=req.session_id,
        session_status="closed",
        recorded_at=recorded_at,
    )


def _reset_session_records_for_tests() -> None:
    with _session_records_lock:
        _session_records.clear()
