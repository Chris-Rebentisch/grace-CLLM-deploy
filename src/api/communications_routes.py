"""Communications routes (Chunk 58, CP8, §7).

Eight routes under ``/api/communications/``. D246 mirror: route module
MUST NOT import ``src.ingestion.communications.voice_tone.profile_generator``.
Routes read from DB only; CLI writes to DB.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.ingestion.communications.voice_tone.models import (
    D422_CATEGORIES,
    Band,
    DraftGuidancePayload,
    DpiaAttestationRequest,
    StyleSignature,
    VoiceToneConfig,
)
from src.shared.database import get_session_factory

logger = structlog.get_logger(__name__)

communications_router = APIRouter(prefix="/api/communications", tags=["communications"])

_DPIA_TEMPLATE_PATH = Path("docs/_templates/dpia-voice-tone.md")
_DPIA_DIR = Path("data/dpia")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _require_admin_key(request: Request) -> None:
    """Mutating-route admin-key enforcement."""
    admin_key = os.environ.get("GRACE_ADMIN_KEY", "")
    if not admin_key:
        client_host = request.client.host if request.client else None
        if client_host in {"127.0.0.1", "::1", "testclient"}:
            return
        raise HTTPException(status_code=401, detail="admin key required")
    submitted = request.headers.get("X-Admin-Key", "")
    if not submitted or not secrets.compare_digest(admin_key, submitted):
        raise HTTPException(status_code=401, detail="admin key required")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DraftGuidanceRequest(BaseModel):
    """Request body for draft-guidance (read-only POST)."""

    person_id: UUID = Field(description="Sender person ID.")
    recipient_id: UUID | None = Field(default=None, description="Optional recipient person ID.")
    category: str | None = Field(default=None, description="Optional D422 category filter.")


# ---------------------------------------------------------------------------
# Route (list): GET /api/communications/profiles
# ---------------------------------------------------------------------------


@communications_router.get("/profiles")
async def list_profiles(
    cursor: str | None = None,
    limit: int = 25,
):
    """Cursor-paginated list of communication style profiles (band-only, D120/D217).

    No admin-key — read path. Max limit 100. Uses existing
    ``uq_csp_identity_version`` index.
    """
    if limit < 1 or limit > 100:
        limit = min(max(limit, 1), 100)

    session = get_session_factory()()
    try:
        offset = 0
        if cursor:
            try:
                offset = int(cursor)
            except ValueError:
                pass

        rows = session.execute(
            text("""
                SELECT id, sender_person_id, profile_version,
                       style_signature, profile_quality_band, created_at
                FROM communication_style_profiles
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
            """),
            {"lim": limit + 1, "off": offset},
        ).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = str(offset + limit) if has_more else None

        return {
            "items": [
                {
                    "person_id": str(r[1]),
                    "profile_version": r[2],
                    "style_signature": r[3],
                    "profile_quality_band": r[4],
                    "created_at": r[5].isoformat() if r[5] else None,
                }
                for r in rows
            ],
            "next_cursor": next_cursor,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Route (a): GET /api/communications/profiles/{person_id}
# ---------------------------------------------------------------------------


@communications_router.get("/profiles/{person_id}")
async def get_profile(person_id: str):
    """Voice & Tone profile — latest version for a sender (band-only, D120/D217)."""
    session = get_session_factory()()
    try:
        row = session.execute(
            text("""
                SELECT id, sender_person_id, profile_version,
                       style_signature, profile_quality_band, created_at
                FROM communication_style_profiles
                WHERE sender_person_id = :pid
                ORDER BY profile_version DESC
                LIMIT 1
            """),
            {"pid": person_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="no profile found")

        return {
            "person_id": str(row[1]),
            "profile_version": row[2],
            "style_signature": row[3],
            "profile_quality_band": row[4],
            "created_at": row[5].isoformat() if row[5] else None,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Route (b): GET /profiles/{person_id}/for-recipient/{recipient_id}
# ---------------------------------------------------------------------------


@communications_router.get("/profiles/{person_id}/for-recipient/{recipient_id}")
async def get_per_recipient_profile(person_id: str, recipient_id: str):
    """Per-recipient style profile — composed absolute bands from StyleSignature + StyleDelta."""
    session = get_session_factory()()
    try:
        row = session.execute(
            text("""
                SELECT rsp.category, rsp.confidence_band, rsp.style_delta,
                       csp.style_signature, csp.profile_version
                FROM recipient_style_profiles rsp
                JOIN communication_style_profiles csp ON rsp.profile_id = csp.id
                WHERE csp.sender_person_id = :pid
                AND rsp.recipient_person_id = :rid
                ORDER BY csp.profile_version DESC
                LIMIT 1
            """),
            {"pid": person_id, "rid": recipient_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="no profile or recipient found")

        return {
            "person_id": person_id,
            "recipient_id": recipient_id,
            "category": row[0],
            "confidence_band": row[1],
            "style_delta": row[2],
            "style_signature": row[3],
            "profile_version": row[4],
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Route (c): GET /profiles/{person_id}/for-category/{category}
# ---------------------------------------------------------------------------


@communications_router.get("/profiles/{person_id}/for-category/{category}")
async def get_per_category_profile(person_id: str, category: str):
    """Per-category aggregate across recipients."""
    if category not in D422_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {sorted(D422_CATEGORIES)}",
        )

    session = get_session_factory()()
    try:
        rows = session.execute(
            text("""
                SELECT rsp.recipient_person_id, rsp.confidence_band,
                       rsp.style_delta
                FROM recipient_style_profiles rsp
                JOIN communication_style_profiles csp ON rsp.profile_id = csp.id
                WHERE csp.sender_person_id = :pid
                AND rsp.category = :cat
                ORDER BY csp.profile_version DESC
            """),
            {"pid": person_id, "cat": category},
        ).fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail="no profile or empty category")

        return {
            "person_id": person_id,
            "category": category,
            "recipients": [
                {
                    "recipient_person_id": str(r[0]),
                    "confidence_band": r[1],
                    "style_delta": r[2],
                }
                for r in rows
            ],
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Route (d): POST /draft-guidance (read-only POST, READONLY_ROUTES)
# ---------------------------------------------------------------------------


@communications_router.post("/draft-guidance")
async def draft_guidance(body: DraftGuidanceRequest):
    """Draft-guidance — LLM-consumable absolute style payload (no DB write).

    D504: Enriched to return DraftGuidancePayload from persisted StyleSignature
    JSONB fields. Pure DB read — no LLM call in the handler (D504 design invariant).
    Route MUST NOT import profile_generator (D246 guard).
    Route MUST NOT call get_provider() — synthesis outputs are persisted at
    generation time by profile_generator (CP3).
    """
    session = get_session_factory()()
    try:
        # Get latest profile
        profile_row = session.execute(
            text("""
                SELECT style_signature, profile_quality_band, profile_version
                FROM communication_style_profiles
                WHERE sender_person_id = :pid
                ORDER BY profile_version DESC
                LIMIT 1
            """),
            {"pid": str(body.person_id)},
        ).fetchone()

        if profile_row is None:
            raise HTTPException(status_code=404, detail="no profile found")

        # D504: Parse StyleSignature from JSONB to extract enriched fields
        sig_data = profile_row[0]
        if isinstance(sig_data, str):
            import json as _json
            sig_data = _json.loads(sig_data)

        sig = StyleSignature.model_validate(sig_data)

        # Build DraftGuidancePayload from persisted JSONB fields (D504)
        payload = DraftGuidancePayload(
            greeting=sig.greeting_patterns[0] if sig.greeting_patterns else None,
            closing=sig.closing_patterns[0] if sig.closing_patterns else None,
            sample_phrases=sig.sample_phrases,
            avoid_phrases=sig.avoid_phrases,
            tone_summary=sig.tone_summary,
            hedging=sig.hedging_frequency_band,
            directness=sig.directness_band,
        )

        result: dict[str, Any] = {
            "person_id": str(body.person_id),
            "profile_version": profile_row[2],
            "profile_quality_band": profile_row[1],
            "guidance": payload.model_dump(),
        }

        # If recipient_id provided, compose with delta
        if body.recipient_id:
            delta_row = session.execute(
                text("""
                    SELECT rsp.style_delta, rsp.category
                    FROM recipient_style_profiles rsp
                    JOIN communication_style_profiles csp ON rsp.profile_id = csp.id
                    WHERE csp.sender_person_id = :pid
                    AND rsp.recipient_person_id = :rid
                    ORDER BY csp.profile_version DESC
                    LIMIT 1
                """),
                {"pid": str(body.person_id), "rid": str(body.recipient_id)},
            ).fetchone()

            if delta_row:
                result["style_delta"] = delta_row[0]
                result["recipient_category"] = delta_row[1]

        return result
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Route (e): GET /profiles/{person_id}/explanation (admin-key gated)
# ---------------------------------------------------------------------------


@communications_router.get("/profiles/{person_id}/explanation")
async def get_explanation(person_id: str, request: Request):
    """Art 15 explanation — deterministic template, no LLM. Admin-key gated."""
    _require_admin_key(request)

    session = get_session_factory()()
    try:
        row = session.execute(
            text("""
                SELECT id, profile_version, style_signature,
                       profile_quality_band, created_at
                FROM communication_style_profiles
                WHERE sender_person_id = :pid
                ORDER BY profile_version DESC
                LIMIT 1
            """),
            {"pid": person_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="no profile found")

        profile_id = row[0]
        profile_version = row[1]
        style_sig = row[2]
        quality_band = row[3]
        created_at = row[4]

        # Get recipient categories
        cats = session.execute(
            text("""
                SELECT DISTINCT category FROM recipient_style_profiles
                WHERE profile_id = :pid
            """),
            {"pid": str(profile_id)},
        ).fetchall()

        # Get sample email headers (no bodies)
        headers = session.execute(
            text("""
                SELECT sender_email, recipient_email, subject, sent_at
                FROM communication_events
                WHERE triage_tier_outcome = 'passed_to_extraction'
                AND sender_email IN (
                    SELECT canonical_name FROM entity_resolution_registry
                    WHERE canonical_grace_id = :pid AND canonical_type = 'Person'
                )
                ORDER BY sent_at DESC
                LIMIT 10
            """),
            {"pid": person_id},
        ).fetchall()

        # Build feature contributions from style_signature bands
        bands = style_sig if isinstance(style_sig, dict) else {}
        features = []
        for feature_name in [
            "sentence_length", "vocabulary_complexity", "formality",
            "greeting_closing", "hedging_frequency", "directness",
            "response_timing", "thread_depth",
        ]:
            band_key = f"{feature_name}_band"
            band_val = bands.get(band_key, "medium")
            features.append({
                "feature": feature_name,
                "value_band": band_val,
                "drove": [band_key],
            })

        # Deterministic NL summary (no LLM — Art 15)
        nl_summary = (
            f"Profile version {profile_version} generated at "
            f"{created_at.isoformat() if created_at else 'unknown'}. "
            f"Quality band: {quality_band}. "
            f"Based on {len(headers)} sample emails."
        )

        # DPIA reference
        dpia_ref = None
        if _DPIA_DIR.exists():
            atts = sorted(_DPIA_DIR.glob("voice-tone-attestation-*.md"), reverse=True)
            if atts:
                dpia_ref = str(atts[0])

        return {
            "profile_version": profile_version,
            "person_id": person_id,
            "generated_at": created_at.isoformat() if created_at else None,
            "bands": bands,
            "recipient_categories_assigned": [c[0] for c in cats],
            "feature_contributions": features,
            "sample_email_count": len(headers),
            "sample_email_headers": [
                {
                    "sender": h[0],
                    "recipient": h[1],
                    "subject": h[2],
                    "sent_at_iso": h[3].isoformat() if h[3] else None,
                }
                for h in headers
            ],
            "natural_language_summary": nl_summary,
            "dpia_reference": dpia_ref,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Route (f): GET /profiles/aggregate/{segment}
# ---------------------------------------------------------------------------


@communications_router.get("/profiles/aggregate/{segment}")
async def get_aggregate_profile(segment: str):
    """Aggregate profile from department_communication_profiles VIEW."""
    session = get_session_factory()()
    try:
        row = session.execute(
            text("""
                SELECT aggregate_segment, avg_sentence_length_band,
                       avg_formality_band, avg_directness_band,
                       profile_count
                FROM department_communication_profiles
                WHERE aggregate_segment = :seg
            """),
            {"seg": segment},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="no aggregate profile for segment")

        return {
            "aggregate_segment": row[0],
            "avg_sentence_length_band": row[1],
            "avg_formality_band": row[2],
            "avg_directness_band": row[3],
            "profile_count": row[4],
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Route (g): GET /dpia/status
# ---------------------------------------------------------------------------


@communications_router.get("/dpia/status")
async def get_dpia_status():
    """DPIA attestation status (read path, no auth)."""
    config = _load_voice_tone_config()

    if not _DPIA_DIR.exists():
        return {
            "attestation_active": False,
            "valid_until": None,
            "signed_by": None,
        }

    today = datetime.now(tz=timezone.utc).date()
    for f in sorted(_DPIA_DIR.glob("voice-tone-attestation-*.md"), reverse=True):
        try:
            date_str = f.stem.replace("voice-tone-attestation-", "")
            att_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        delta = (today - att_date).days
        if delta <= config.dpia_validity_days:
            valid_until = att_date + timedelta(days=config.dpia_validity_days)
            # Try to read signed_by from frontmatter
            signed_by = None
            try:
                content = f.read_text()
                for line in content.splitlines():
                    if line.startswith("signed_by:"):
                        signed_by = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass

            return {
                "attestation_active": True,
                "valid_until": valid_until.isoformat(),
                "signed_by": signed_by,
            }

    return {
        "attestation_active": False,
        "valid_until": None,
        "signed_by": None,
    }


# ---------------------------------------------------------------------------
# Route (h): POST /dpia/attestation (mutating, admin-key gated, Lock-R4)
# ---------------------------------------------------------------------------


@communications_router.post("/dpia/attestation", status_code=201)
async def submit_dpia_attestation(body: DpiaAttestationRequest, request: Request):
    """Submit DPIA attestation (Lock-R4). Admin-key gated."""
    _require_admin_key(request)

    config = _load_voice_tone_config()
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    target_path = _DPIA_DIR / f"voice-tone-attestation-{today}.md"

    # 409 if already exists for today
    if target_path.exists():
        raise HTTPException(status_code=409, detail="attestation already exists for today")

    # Compute live SHA-256 of template
    if not _DPIA_TEMPLATE_PATH.exists():
        raise HTTPException(
            status_code=422,
            detail="DPIA template not found at docs/_templates/dpia-voice-tone.md",
        )

    template_content = _DPIA_TEMPLATE_PATH.read_text()
    live_sha = hashlib.sha256(template_content.encode()).hexdigest()

    if live_sha != body.dpia_template_content_sha256:
        raise HTTPException(
            status_code=409,
            detail="template changed; reload and re-sign",
        )

    # Write attestation file
    os.makedirs(str(_DPIA_DIR), mode=0o700, exist_ok=True)

    valid_until = (
        datetime.strptime(today, "%Y-%m-%d").date()
        + timedelta(days=config.dpia_validity_days)
    )

    frontmatter = (
        f"---\n"
        f"signed_by: {body.signed_by}\n"
        f"signed_role: {body.signed_role}\n"
        f"signed_at: {body.signed_at_iso.isoformat()}\n"
        f"valid_until: {valid_until.isoformat()}\n"
        f"template_sha256: {body.dpia_template_content_sha256}\n"
        f"---\n\n"
        f"DPIA attestation for Voice & Tone individual-mode profiling.\n"
    )

    target_path.write_text(frontmatter)
    os.chmod(str(target_path), 0o600)

    logger.info(
        "voice_tone_dpia_attested",
        signed_by=body.signed_by,
        path=str(target_path),
    )

    return {
        "path": str(target_path),
        "valid_until": valid_until.isoformat(),
    }


# ---------------------------------------------------------------------------
# Config loader (read-only, no profile_generator import)
# ---------------------------------------------------------------------------


def _load_voice_tone_config() -> VoiceToneConfig:
    """Load VoiceToneConfig from YAML (D246 mirror: no profile_generator import)."""
    import yaml

    config_path = Path("config/voice_tone_config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return VoiceToneConfig(**data)
    return VoiceToneConfig()
