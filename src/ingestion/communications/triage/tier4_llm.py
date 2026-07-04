"""Tier 4 LLM binary relevance classifier (Chunk 57, OQ-1 body-only).

Few-shot binary relevance filter via ``src/shared/llm_provider.get_provider()``
+ ``LLMProvider.generate(prompt, json_mode=True)``. Body text only (OQ-1),
amended by F-021 / ISS-0004: the prompt now also carries the subject line and
a reply marker (headers already in hand at triage time — no new DB queries)
so short replies inside organizationally-relevant threads are not false-dropped.

Two outcomes:
- ``relevant=true`` → returns ``None`` (pass-through; orchestrator writes ``passed_to_extraction``).
- ``relevant=false`` → returns ``"filtered_t4_not_organizationally_relevant"``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.ingestion.communications.triage.config import TriageConfig
    from src.ingestion.models import CommunicationEvent
    from src.shared.llm_provider import LLMProvider

logger = structlog.get_logger()

# Load few-shot fixtures at module init (sorted by filename)
_FIXTURE_DIR = Path(__file__).parent / "tier4_fixtures"
_FEW_SHOT_EXAMPLES: list[dict] = []


def _load_fixtures() -> list[dict]:
    """Load and sort few-shot fixture files."""
    if not _FIXTURE_DIR.exists():
        return []
    fixtures = []
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        with open(path) as f:
            fixtures.append(json.load(f))
    return fixtures


# Eager load on first import
_FEW_SHOT_EXAMPLES = _load_fixtures()


def _build_prompt(
    body_text: str,
    *,
    subject: str | None = None,
    is_reply: bool = False,
) -> str:
    """Build the few-shot prompt for Tier 4 classification.

    F-021 / ISS-0004: optionally includes the thread subject and a reply
    marker so brief replies within organizationally-relevant threads are
    classified in thread context instead of in isolation.
    """
    lines = [
        "You are a binary relevance classifier. Given an email body, determine whether "
        "it is organizationally relevant (contains information about business relationships, "
        "regulatory matters, organizational structure, contracts, policies, or operational decisions) "
        "or not relevant (personal messages, automated notifications, marketing, spam).",
        "",
        # F-021 / ISS-0004: haiku false-dropped 3/24 true-pass emails (short
        # replies like bid details and gains estimates) as "not organizationally
        # relevant", consistently across retriage cycles — a prompt gap, not
        # sampling noise. Missing feature was THREAD CONTEXT; this instruction
        # plus the subject/reply lines below supply it.
        "A short reply or follow-up within a thread whose subject/topic is "
        "organizationally relevant IS relevant — do not drop brief messages for "
        "brevity or informal tone alone. When uncertain about a reply in a "
        "known-relevant thread, keep it.",
        "",
        "Respond with JSON only: {\"relevant\": true/false, \"rationale_band\": \"high\"/\"medium\"/\"low\"}",
        "",
        "Examples:",
    ]

    for ex in _FEW_SHOT_EXAMPLES:
        lines.append(f"User: {ex['body_text']}")
        lines.append(
            f"Assistant: {{\"relevant\": {json.dumps(ex['expected_relevant'])}, "
            f"\"rationale_band\": \"{ex['expected_rationale_band']}\"}}"
        )
        lines.append("")

    # F-021 / ISS-0004: thread-context lines for the email under classification.
    # Subject + reply marker only — both already on the event at triage time.
    user_lines: list[str] = []
    if subject:
        user_lines.append(f"Thread subject: {subject}")
    if is_reply:
        user_lines.append(
            "Note: this email is a reply within an existing thread — judge its "
            "relevance in the context of the thread subject above."
        )
    user_lines.append(body_text)
    lines.append("User: " + "\n".join(user_lines))
    lines.append("Assistant:")
    return "\n".join(lines)


# Accumulated cost tracking (per-process, reset on restart)
_accumulated_cost_usd: float = 0.0


async def run_tier4(
    event: CommunicationEvent,
    provider: LLMProvider,
    config: TriageConfig,
) -> str | None:
    """Run Tier 4 LLM binary relevance classification.

    Returns:
        ``None`` — event is relevant (pass-through to extraction).
        ``"filtered_t4_not_organizationally_relevant"`` — event filtered.
    """
    global _accumulated_cost_usd

    body_text = event.body_plain or ""
    if not body_text.strip():
        # Empty body → pass-through (safe default)
        return None

    # D442 cost enforcement (Chunk 61, CP7).
    # Invariant: D246 cost gate — authorization D442 / research Subject 9.
    # Carve-out: flipped from warn-only (Chunk 57) to enforcing.
    # When budget exceeded, assign filtered_t4_budget_exceeded and do NOT call LLM.
    tier4_config = getattr(config, "tier4", None)
    cost_budget = getattr(tier4_config, "cost_budget_usd_per_run", 1.0) if tier4_config else 1.0
    if _accumulated_cost_usd > cost_budget:
        logger.warning(
            "tier4_cost_budget_exceeded",
            accumulated_cost_usd=_accumulated_cost_usd,
            budget_usd=cost_budget,
            event_id=str(event.event_id),
            enforcing=True,
        )
        return "filtered_t4_budget_exceeded"

    # F-021 / ISS-0004: assemble thread context from fields already in hand on
    # the event (subject / in_reply_to / references / thread_id) — no new DB
    # queries. The parent message's triage outcome WOULD require a lookup by
    # in_reply_to message_id, so it is deliberately not fetched here.
    subject = getattr(event, "subject", None)
    is_reply = bool(
        getattr(event, "in_reply_to", None)
        or getattr(event, "references", None)
        or getattr(event, "thread_id", None)
        or (subject or "").strip().lower().startswith("re:")
    )

    prompt = _build_prompt(body_text, subject=subject, is_reply=is_reply)

    try:
        # D543 capture-the-why: the LLMProvider interface is
        # generate(system_prompt, user_prompt, ...) -> LLMResponse, but this (and the
        # other ingestion LLM stages) was written against the old generate(prompt)->str
        # signature, so the call raised "missing user_prompt" and the model was never
        # actually invoked (the email passed through by default). Pass the built prompt
        # as user_prompt and read response.text.
        response = await provider.generate(
            system_prompt="", user_prompt=prompt, json_mode=True
        )

        # Parse JSON response
        try:
            parsed = json.loads(response.text)
        except (json.JSONDecodeError, TypeError, AttributeError):
            logger.warning(
                "tier4_json_parse_failure",
                event_id=str(event.event_id),
                raw_response=str(response)[:200],
            )
            return None  # Safe default: pass-through

        relevant = parsed.get("relevant", True)
        rationale_band = parsed.get("rationale_band", "medium")

        logger.info(
            "tier4_classification",
            event_id=str(event.event_id),
            relevant=relevant,
            rationale_band=rationale_band,
        )

        if relevant:
            return None
        else:
            return "filtered_t4_not_organizationally_relevant"

    except Exception as exc:
        logger.warning(
            "tier4_llm_error",
            event_id=str(event.event_id),
            error=str(exc),
        )
        return None  # Safe default: pass-through on error
