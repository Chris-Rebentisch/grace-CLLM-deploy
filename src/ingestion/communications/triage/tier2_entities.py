"""Tier 2 entity lookup — ArcadeDB Person/Organization queries (Chunk 56, D430).

Uses ``name`` + ``aliases`` only via parameterized ``execute_cypher``.
Does NOT use ``canonical_name`` or ``email_addresses`` (neither exists on
shipped vertices). No f-string interpolation of user-controlled values.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ingestion.models import CommunicationEvent


def _extract_sender_name(event: CommunicationEvent) -> str:
    """Extract sender display name; fall back to local-part of sender_email."""
    if event.sender_display_name:
        return event.sender_display_name
    email = str(event.sender_email)
    return email.split("@")[0] if "@" in email else email


# C1 defect #3: default is a superset of the shipped D430 pair — Legal_Entity
# covers legal-ontology deployments that lack Person/Organization. Absent labels
# are treated as no-match (see run_tier2), so the extra label never regresses.
_DEFAULT_ENTITY_TYPES = ("Person", "Organization", "Legal_Entity")


def _build_entity_query(label: str) -> str:
    """Build the sender-match query for a graph vertex label. The label comes from
    operator config (trusted), not user input; the sender name stays parameterized
    ($sender_name) so the D430 no-f-string-injection rule still holds for it."""
    return (
        f"MATCH (n:{label}) "
        "WHERE n.name = $sender_name OR $sender_name IN n.aliases "
        "RETURN n.grace_id LIMIT 1"
    )


# Backward-compat constants (D540: the default-type queries). Retained so existing
# callers/tests that reference them keep working after the generalization.
_PERSON_QUERY = _build_entity_query("Person")
_ORG_QUERY = _build_entity_query("Organization")


async def run_tier2(event: CommunicationEvent, arcade_client, config=None) -> str | None:
    """Query ArcadeDB for a sender entity match against the configured vertex types.

    D540: the matched labels come from ``config.entity_types`` (default
    Person/Organization, the shipped D430 behavior). Returns ``None`` if any
    configured type matches the sender (pass to next tier); otherwise
    ``"filtered_t2_no_known_entity"``. A query against a label absent from the
    active graph raises — caught here and treated as no-match (robust to ontology
    mismatch) rather than crashing the tier.
    """
    sender_name = _extract_sender_name(event)
    params = {"sender_name": sender_name}
    entity_types = list(getattr(config, "entity_types", None) or _DEFAULT_ENTITY_TYPES)

    results = await asyncio.gather(
        *[arcade_client.execute_cypher(_build_entity_query(lbl), params=params)
          for lbl in entity_types],
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            continue
        rows = r.get("result", []) if isinstance(r, dict) else r or []
        if rows:
            return None  # Entity found — pass to next tier

    return "filtered_t2_no_known_entity"
