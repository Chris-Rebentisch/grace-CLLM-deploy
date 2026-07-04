"""Role resolver — sole Voice & Tone module importing ``src.graph.arcade_client`` (Chunk 58, CP6, Lock-R3).

D356 capture-the-why: Lock-R3 isolation — this module is the SOLE Voice & Tone
module that touches ArcadeDB. Tier 1 role category requires
``(Person)-[:HAS_ROLE]->(Role)`` graph pattern verified at
``src/permissions/evidence_collector.py:140``. All other Voice & Tone modules
use Postgres only (Lock-R2).

Authorization: Lock-R3 (chunk-58-divergence.md).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import structlog

from src.ingestion.communications.voice_tone.models import Band, D422_CATEGORIES, VoiceToneConfig

logger = structlog.get_logger()

# Tier 1 Cypher — parameterized, no f-string injection (D430 discipline)
_TIER1_CYPHER = """
MATCH (p:Person {grace_id: $grace_id})-[:HAS_ROLE]->(r:Role)
RETURN r.name AS role_name LIMIT 5
"""


async def resolve_role(
    canonical_grace_id: UUID,
    config: VoiceToneConfig,
) -> tuple[str | None, Band]:
    """Resolve recipient category via ArcadeDB ``HAS_ROLE`` Cypher (Lock-R3).

    Returns:
        ``(matched_category, "high")`` on Cypher match + map hit;
        ``(None, "medium")`` on Cypher match without map hit;
        ``(None, "low")`` on zero rows;
        ``(None, "low")`` + structlog error on Cypher throw (graceful degradation, R7).
    """
    try:
        # D538: route through get_arcade_client() so ARCADE_DATABASE is honored
        # (bare ArcadeClient() hardcodes database="grace", ignoring the setting).
        from src.graph.arcade_client import get_arcade_client

        client = get_arcade_client()
        result = await _execute_cypher(client, str(canonical_grace_id))
    except Exception as exc:
        # R7: ArcadeDB unreachable — graceful degradation
        logger.error(
            "voice_tone_tier1_cypher_failure",
            grace_id=str(canonical_grace_id),
            error=str(exc),
        )
        return None, "low"

    if not result:
        return None, "low"

    # Map role names against config
    for role_name in result:
        category = _match_role_to_category(role_name, config)
        if category:
            return category, "high"

    # Cypher match but no map hit
    return None, "medium"


def _match_role_to_category(
    role_name: str, config: VoiceToneConfig
) -> str | None:
    """Case-insensitive substring match against role_to_category_map."""
    role_lower = role_name.lower()
    for pattern, category in config.role_to_category_map.items():
        if pattern.lower() in role_lower:
            if category in D422_CATEGORIES:
                return category
    return None


async def _execute_cypher(client: object, grace_id: str) -> list[str]:
    """Execute the Tier 1 Cypher query and return role names."""
    # F-55 (validation run): ArcadeClient has no `.query()` method — it is
    # async and exposes `execute_cypher(query, params=...)`, which raises the
    # opencypher language internally. The old `.query(...)` call (wrapped in
    # to_thread as if the client were sync) raised AttributeError on every
    # Tier 1 role resolution, which the caller's except swallowed to (None,
    # "low"). Call the real async method directly.
    response = await client.execute_cypher(  # type: ignore[union-attr]
        _TIER1_CYPHER,
        params={"grace_id": grace_id},
    )

    rows = response.get("result", []) if isinstance(response, dict) else (response or [])
    if not rows:
        return []

    return [
        row.get("role_name", "") for row in rows if row.get("role_name")
    ]


# ---------------------------------------------------------------------------
# F-31 — sender→Person resolution via the graph (Lock-R3: this module is the
# only Voice & Tone module permitted to touch ArcadeDB, so the graph-fallback
# sender resolution lives here, not in profile_generator).
# ---------------------------------------------------------------------------

_SENDER_LOOKUP_CYPHER = """
MATCH (p:Person)
WHERE $needle IN p.aliases OR p.name = $needle
RETURN p.grace_id AS gid LIMIT 2
"""


async def resolve_sender_person(
    sender_email: str,
    display_name: str | None = None,
) -> str | None:
    """Resolve an email sender to a Person vertex ``grace_id`` (F-31).

    Voice profiling previously required an ``entity_resolution_registry`` row
    keyed ``canonical_name=<email>`` — a registry only connector/federation
    code populates — so voice was silently dead on any deployment without
    connectors. This mirrors how triage tier-2 and the corroboration scorer
    already resolve senders: match the email (then the display name) against
    ``Person.name`` / ``Person.aliases`` in the graph. Privacy posture: the
    graph is only READ — no email address is ever persisted onto a Person
    vertex by this path.

    Ambiguity guard: two or more matches -> None (never attach a style
    profile to the wrong person); logged for diagnostics.
    """
    try:
        from src.graph.arcade_client import get_arcade_client

        client = get_arcade_client()
    except Exception as exc:  # noqa: BLE001 — graceful degradation (R7)
        logger.error("voice_tone_sender_resolution_client_failure", error=str(exc))
        return None

    needles = [sender_email]
    if display_name and display_name.strip():
        needles.append(display_name.strip())

    for needle in needles:
        try:
            response = await client.execute_cypher(
                _SENDER_LOOKUP_CYPHER, params={"needle": needle}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "voice_tone_sender_resolution_cypher_failure",
                needle_kind="email" if needle == sender_email else "display_name",
                error=str(exc),
            )
            continue

        rows = (
            response.get("result", [])
            if isinstance(response, dict)
            else (response or [])
        )
        gids = [row.get("gid") or row.get("grace_id") for row in rows]
        gids = [g for g in gids if g]
        if len(gids) > 1:
            logger.warning(
                "voice_tone_sender_resolution_ambiguous",
                sender_email=sender_email,
                match_count=len(gids),
            )
            return None
        if gids:
            return str(gids[0])

    return None
