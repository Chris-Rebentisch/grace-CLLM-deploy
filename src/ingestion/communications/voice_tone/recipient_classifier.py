"""Recipient classifier — four-tier abstain-and-defer cascade (Chunk 58, CP6, Lock-R2).

D356 capture-the-why: Lock-R2 design choice — Postgres ``entity_resolution_registry``
is authoritative for canonical-entity identity per D404; ArcadeDB Person vertices
may exist without registry rows. This module MUST NOT import ``src.graph.*``.

Authorization: Lock-R2 (chunk-58-divergence.md).

Four-tier cascade:
  Tier 1 — delegates to ``role_resolver.resolve_role()`` (Lock-R3).
  Tier 1.5 — signature-derived title/org from recent outgoing emails.
  Tier 2 — email-pattern: thread depth, response timing, CC/TO positioning.
  Tier 3 — LLM fallback.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import structlog

from src.ingestion.communications.voice_tone.models import (
    Band,
    D422_CATEGORIES,
    VoiceToneConfig,
)

logger = structlog.get_logger()


class RecipientClassifier:
    """Four-tier abstain-and-defer cascade for recipient classification.

    MUST NOT import ``src.graph.*`` (Lock-R2). Graph-presence gate uses
    Postgres ``entity_resolution_registry`` only.
    """

    def __init__(self, config: VoiceToneConfig) -> None:
        self.config = config

    async def check_graph_presence(
        self, recipient_email: str, session: object
    ) -> UUID | None:
        """Graph-presence gate (Lock-R2): check entity_resolution_registry.

        Zero rows → silently drop the recipient (no row, no warn, no UI surface).
        """
        from sqlalchemy import text

        result = session.execute(  # type: ignore[union-attr]
            text(
                "SELECT canonical_grace_id FROM entity_resolution_registry "
                "WHERE canonical_type = 'Person' "
                "AND canonical_name = :recipient_email"
            ),
            {"recipient_email": recipient_email},
        ).fetchone()

        if result is None:
            return None
        return UUID(str(result[0]))

    async def classify(
        self,
        recipient_email: str,
        canonical_grace_id: UUID,
        config: VoiceToneConfig,
        signature_title: str | None = None,
        signature_org: str | None = None,
        thread_depth: int = 1,
        response_timing_band: Band = "medium",
        cc_position: bool = False,
        representative_bodies: list[str] | None = None,
        sender_person_id: UUID | None = None,
        profile_version: int = 1,
    ) -> tuple[str, Band]:
        """Run four-tier classification cascade.

        Returns (category, confidence_band). Higher tiers win on conflict.
        """
        # Tier 1: Graph role via role_resolver (Lock-R3)
        tier1_result = await self._tier1_role(canonical_grace_id, config)
        if tier1_result[0] is not None:
            return tier1_result

        # Tier 1.5: Signature-derived title
        tier15_result = self._tier15_signature(signature_title, config)
        if tier15_result[0] is not None:
            return tier15_result

        # Tier 2: Email-pattern heuristics
        tier2_result = self._tier2_pattern(
            thread_depth, response_timing_band, cc_position
        )
        if tier2_result[0] is not None:
            return tier2_result

        # Tier 3: LLM fallback
        tier3_result = await self._tier3_llm(
            recipient_email, representative_bodies or [],
            sender_person_id, profile_version,
        )
        return tier3_result

    async def _tier1_role(
        self, canonical_grace_id: UUID, config: VoiceToneConfig
    ) -> tuple[str | None, Band]:
        """Tier 1: Delegate to role_resolver.resolve_role() (Lock-R3)."""
        from src.ingestion.communications.voice_tone.role_resolver import resolve_role

        return await resolve_role(canonical_grace_id, config)

    def _tier15_signature(
        self, title: str | None, config: VoiceToneConfig
    ) -> tuple[str | None, Band]:
        """Tier 1.5: Signature-derived title → category via title_to_category_map."""
        if not title:
            return None, "low"

        title_lower = title.lower()
        for pattern, category in config.title_to_category_map.items():
            if pattern.lower() in title_lower:
                if category in D422_CATEGORIES:
                    return category, "medium"
        return None, "low"

    def _tier2_pattern(
        self,
        thread_depth: int,
        response_timing_band: Band,
        cc_position: bool,
    ) -> tuple[str | None, Band]:
        """Tier 2: Email-pattern heuristics.

        High thread depth + fast response → peer_same_department.
        CC-only positioning → general_distribution.
        """
        if cc_position:
            return "general_distribution", "low"

        if thread_depth >= 5 and response_timing_band == "high":
            return "peer_same_department", "low"

        return None, "low"

    async def _tier3_llm(
        self,
        recipient_email: str,
        representative_bodies: list[str],
        sender_person_id: UUID | None,
        profile_version: int,
    ) -> tuple[str, Band]:
        """Tier 3: LLM fallback with deterministic seed.

        Random N=5 representative emails, deterministic seed keyed on
        (sender_person_id, profile_version).
        """
        import hashlib
        import json
        import random

        # Deterministic seed
        seed_str = f"{sender_person_id}:{profile_version}"
        seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)

        # Sample N=5 representative emails
        sample = rng.sample(
            representative_bodies,
            min(5, len(representative_bodies)),
        ) if representative_bodies else []

        if not sample:
            return "general_distribution", "low"

        try:
            from src.shared.llm_provider import get_provider

            provider = get_provider()
            categories_str = ", ".join(sorted(D422_CATEGORIES))
            prompt = (
                f"Classify the relationship between a sender and recipient "
                f"'{recipient_email}' based on these email excerpts. "
                f"Choose one category from: {categories_str}.\n\n"
            )
            for i, body in enumerate(sample):
                prompt += f"Email {i+1}: {body[:300]}\n\n"
            prompt += (
                "Return JSON with keys: category (one of the listed categories), "
                "confidence (high, medium, or low)."
            )

            # D543: provider interface is generate(system_prompt, user_prompt) -> LLMResponse.
            response = await provider.generate(system_prompt="", user_prompt=prompt, json_mode=True)
            data = json.loads(response.text)
            category = data.get("category", "general_distribution")
            if category in D422_CATEGORIES:
                return category, "low"
        except Exception:
            logger.warning(
                "voice_tone_tier3_llm_fallback_failed",
                recipient=recipient_email,
            )

        return "general_distribution", "low"
