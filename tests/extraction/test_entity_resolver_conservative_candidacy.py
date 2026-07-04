"""Tests for conservative ER candidacy additions (2026-07-03 fix round).

Covers:
- F-0041 / ISS-0034: cross-type name-collision visibility flag.
- F-0025b / ISS-0035: OCR suffix-variant names reach Tier-3 candidacy.
- F-0032d / ISS-0035: single-token Person fragments route to adjudication
  when exactly ONE existing Person's first/last token matches.

Pure unit tests — arcade client / registry / embeddings all mocked. No
services, no DB.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.extraction.entity_registry import EntityRegistry
from src.extraction.entity_resolver import (
    DisambiguationResult,
    EntityResolver,
)
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractedEntity
from src.graph.arcade_client import ArcadeClient, ArcadeConfig


def _make_entity(name="Acme Corp", entity_type="Legal_Entity", **props):
    return ExtractedEntity(name=name, entity_type=entity_type, properties=props)


def _mock_arcade_client():
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock(return_value={"result": []})
    client.execute_sql = AsyncMock(return_value={"result": []})
    return client


def _make_resolver(arcade_client=None, config=None, instructor_client=None):
    return EntityResolver(
        arcade_client=arcade_client or _mock_arcade_client(),
        config=config or ExtractionSettings(),
        ollama_base_url="http://localhost:11434",
        instructor_client=instructor_client,
    )


def _yes_instructor(reasoning="Same entity"):
    mock = AsyncMock()
    mock.resolve = AsyncMock(
        return_value=DisambiguationResult(decision="YES", reasoning=reasoning)
    )
    return mock


def _no_instructor(reasoning="Different entities"):
    mock = AsyncMock()
    mock.resolve = AsyncMock(
        return_value=DisambiguationResult(decision="NO", reasoning=reasoning)
    )
    return mock


# ---------------------------------------------------------------------------
# F-0041 / ISS-0034 — cross-type name collision
# ---------------------------------------------------------------------------


def _sql_router(collision_rows, ann_neighbors=None):
    """execute_sql side_effect distinguishing the ANN query from the
    cross-type collision scan."""

    async def _route(sql, *args, **kwargs):
        if "vectorNeighbors" in sql:
            return {"result": [{"neighbors": ann_neighbors or []}]}
        return {"result": collision_rows}

    return _route


@pytest.mark.asyncio
async def test_cross_type_collision_flagged_and_logged():
    """Same normalized name under a DIFFERENT type -> entity still minted,
    but the result carries the cross_type_name_collision flag (ISS-0034)."""
    client = _mock_arcade_client()
    client.execute_sql = AsyncMock(
        side_effect=_sql_router(
            collision_rows=[
                {
                    "entity_type": "Legal_Entity",
                    "grace_id": "uuid-le-1",
                    "name": "Fairview County Water District",
                }
            ]
        )
    )

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Fairview County Water District", entity_type="Vendor")
        result = await resolver.resolve_entity(entity, registry)

    # Entity creation is NOT blocked — it is minted new...
    assert result.is_new is True
    assert result.resolution_tier == "new"
    # ...but the collision is visible on the resolution-log row
    assert result.resolution_note == "cross_type_name_collision"
    assert result.candidates_json is not None
    flagged = [
        c for c in result.candidates_json
        if c.get("flag") == "cross_type_name_collision"
    ]
    assert len(flagged) == 1
    assert flagged[0]["grace_id"] == "uuid-le-1"
    assert flagged[0]["entity_type"] == "Legal_Entity"


@pytest.mark.asyncio
async def test_cross_type_different_normalized_name_untouched():
    """A row whose normalized name differs is NOT a collision."""
    client = _mock_arcade_client()
    client.execute_sql = AsyncMock(
        side_effect=_sql_router(
            collision_rows=[
                {
                    "entity_type": "Legal_Entity",
                    "grace_id": "uuid-le-2",
                    "name": "Fairview County Water Authority",
                }
            ]
        )
    )

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Fairview County Water District", entity_type="Vendor")
        result = await resolver.resolve_entity(entity, registry)

    assert result.is_new is True
    assert result.resolution_note is None


@pytest.mark.asyncio
async def test_cross_type_same_type_row_untouched():
    """A same-name row of the SAME type is the existing per-type path's
    business — the cross-type flag must not fire."""
    client = _mock_arcade_client()
    client.execute_sql = AsyncMock(
        side_effect=_sql_router(
            collision_rows=[
                {
                    "entity_type": "Vendor",
                    "grace_id": "uuid-v-1",
                    "name": "Fairview County Water District",
                }
            ]
        )
    )

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Fairview County Water District", entity_type="Vendor")
        result = await resolver.resolve_entity(entity, registry)

    assert result.is_new is True
    assert result.resolution_note is None


@pytest.mark.asyncio
async def test_cross_type_check_skipped_for_matched_entities():
    """Tier-1 matches (is_new=False) never trigger the cross-type scan —
    the existing same-type path is untouched."""
    client = _mock_arcade_client()

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value="uuid-existing",
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Fairview County Water District", entity_type="Vendor")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "exact"
    assert result.is_new is False
    assert result.resolution_note is None
    client.execute_sql.assert_not_called()


@pytest.mark.asyncio
async def test_cross_type_check_failure_never_breaks_resolution():
    """A failing collision scan is log-and-continue — resolution outcome
    is unchanged (visibility check must never break extraction)."""
    client = _mock_arcade_client()

    async def _route(sql, *args, **kwargs):
        if "vectorNeighbors" in sql:
            return {"result": [{"neighbors": []}]}
        raise RuntimeError("arcade down")

    client.execute_sql = AsyncMock(side_effect=_route)

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Fairview County Water District", entity_type="Vendor")
        result = await resolver.resolve_entity(entity, registry)

    assert result.is_new is True
    assert result.resolution_note is None


# ---------------------------------------------------------------------------
# F-0025b / ISS-0035 — OCR suffix-variant candidacy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llo_variant_reaches_tier3_adjudication():
    """'Cedar Grove Residences LLO' finds the stored '...LLC' vertex via
    canonical respelling and is ADJUDICATED (Tier 3), not silently merged
    and not minted blind (F-0025b)."""
    lookups: list[str] = []

    async def _lookup(client, entity_type, name):
        lookups.append(name)
        if name == "Cedar Grove Residences LLC":
            return "uuid-llc-1"
        return None

    instructor = _yes_instructor("LLO is an OCR misread of LLC")

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        side_effect=_lookup,
    ), patch(
        "src.extraction.entity_resolver.append_entity_alias",
        new_callable=AsyncMock,
    ):
        resolver = _make_resolver(instructor_client=instructor)
        registry = EntityRegistry()
        entity = _make_entity("Cedar Grove Residences LLO")
        result = await resolver.resolve_entity(entity, registry)

    # The canonical respelling was looked up
    assert "Cedar Grove Residences LLC" in lookups
    # Adjudicated merge — Tier 3, with the variant provenance note preserved
    instructor.resolve.assert_called_once()
    assert result.resolution_tier == "llm"
    assert result.resolved_grace_id == "uuid-llc-1"
    assert result.is_new is False
    assert result.resolution_note == "suffix_ocr_variant_candidate"


@pytest.mark.asyncio
async def test_llo_variant_llm_says_no_mints_new():
    """Tier-3 NO on a suffix-variant candidate mints a new entity —
    adjudication can decline (conservatism preserved)."""

    async def _lookup(client, entity_type, name):
        return "uuid-llc-1" if name == "Cedar Grove Residences LLC" else None

    instructor = _no_instructor("Genuinely different entities")

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        side_effect=_lookup,
    ):
        resolver = _make_resolver(instructor_client=instructor)
        registry = EntityRegistry()
        entity = _make_entity("Cedar Grove Residences LLO")
        result = await resolver.resolve_entity(entity, registry)

    assert result.resolution_tier == "new"
    assert result.is_new is True
    assert result.resolution_note == "suffix_ocr_variant_candidate"


@pytest.mark.asyncio
async def test_llo_variant_no_client_mints_with_note():
    """Without a Tier-3 client the variant candidate falls back to the
    existing conservative mint-with-note path — never a silent merge."""

    async def _lookup(client, entity_type, name):
        return "uuid-llc-1" if name == "Cedar Grove Residences LLC" else None

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        side_effect=_lookup,
    ):
        resolver = _make_resolver(instructor_client=None)
        registry = EntityRegistry()
        entity = _make_entity("Cedar Grove Residences LLO")
        result = await resolver.resolve_entity(entity, registry)

    assert result.is_new is True
    assert (
        result.resolution_note
        == "suffix_ocr_variant_candidate;llm_disambiguation_failed"
    )


@pytest.mark.asyncio
async def test_canonical_suffix_name_skips_variant_lookup():
    """A canonically-suffixed name has no variants — Tier 2 runs as today."""
    lookups: list[str] = []

    async def _lookup(client, entity_type, name):
        lookups.append(name)
        return None

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        side_effect=_lookup,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver()
        registry = EntityRegistry()
        entity = _make_entity("Cedar Grove Residences LLC")
        result = await resolver.resolve_entity(entity, registry)

    # Only the Tier-1 raw + normalized lookups — no variant respelling
    assert lookups == [
        "Cedar Grove Residences LLC",
        "cedar grove residences",
    ]
    assert result.resolution_tier == "new"


# ---------------------------------------------------------------------------
# F-0032d / ISS-0035 — single-token Person fragments
# ---------------------------------------------------------------------------


def _person_rows(*names):
    return {
        "result": [
            {"grace_id": f"uuid-p-{i}", "name": n} for i, n in enumerate(names)
        ]
    }


@pytest.mark.asyncio
async def test_person_fragment_single_match_routes_to_adjudication():
    """'Theodore' with exactly one existing 'Theodore Blake' -> Tier-3
    adjudication instead of blind minting (F-0032d)."""
    client = _mock_arcade_client()
    client.execute_cypher = AsyncMock(return_value=_person_rows("Theodore Blake"))
    instructor = _yes_instructor("First-name fragment of Theodore Blake")

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.append_entity_alias",
        new_callable=AsyncMock,
    ):
        resolver = _make_resolver(arcade_client=client, instructor_client=instructor)
        registry = EntityRegistry()
        entity = _make_entity("Theodore", entity_type="Person")
        result = await resolver.resolve_entity(entity, registry)

    instructor.resolve.assert_called_once()
    assert result.resolution_tier == "llm"
    assert result.resolved_grace_id == "uuid-p-0"
    assert result.matched_name == "Theodore Blake"
    assert result.is_new is False
    assert result.resolution_note == "person_name_fragment_candidate"


@pytest.mark.asyncio
async def test_person_fragment_multiple_matches_mints_as_today():
    """'Theodore' with TWO matching Persons -> ambiguous, minted as today
    (no guessing among multiple full-name owners)."""
    client = _mock_arcade_client()

    async def _cypher(query, *args, **kwargs):
        if "STARTS WITH" in query:
            return _person_rows("Theodore Blake", "Theodore Ashworth")
        return {"result": []}

    client.execute_cypher = AsyncMock(side_effect=_cypher)
    instructor = _yes_instructor()

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client, instructor_client=instructor)
        registry = EntityRegistry()
        entity = _make_entity("Theodore", entity_type="Person")
        result = await resolver.resolve_entity(entity, registry)

    # Ambiguous fragment must NOT call the adjudicator with a guessed owner
    instructor.resolve.assert_not_called()
    assert result.resolution_tier == "new"
    assert result.is_new is True


@pytest.mark.asyncio
async def test_person_fragment_zero_matches_mints_as_today():
    """'Theodore' with no token-matching Person -> mint as today."""
    client = _mock_arcade_client()
    instructor = _yes_instructor()

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client, instructor_client=instructor)
        registry = EntityRegistry()
        entity = _make_entity("Theodore", entity_type="Person")
        result = await resolver.resolve_entity(entity, registry)

    instructor.resolve.assert_not_called()
    assert result.resolution_tier == "new"
    assert result.is_new is True


@pytest.mark.asyncio
async def test_person_fragment_check_skips_multi_token_names():
    """Full names never enter the fragment path — existing behavior only."""
    client = _mock_arcade_client()

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Theodore Blake", entity_type="Person")
        result = await resolver.resolve_entity(entity, registry)

    # No fragment Cypher query issued (default execute_cypher would have been
    # called with a STARTS WITH query otherwise)
    for call in client.execute_cypher.call_args_list:
        assert "STARTS WITH" not in call.args[0]
    assert result.resolution_tier == "new"


@pytest.mark.asyncio
async def test_person_fragment_check_skips_non_person_types():
    """Single-token non-Person entities never enter the fragment path."""
    client = _mock_arcade_client()

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.extraction.entity_resolver.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ):
        resolver = _make_resolver(arcade_client=client)
        registry = EntityRegistry()
        entity = _make_entity("Acme", entity_type="Legal_Entity")
        result = await resolver.resolve_entity(entity, registry)

    for call in client.execute_cypher.call_args_list:
        assert "STARTS WITH" not in call.args[0]
    assert result.resolution_tier == "new"
