"""F-0048 / ISS-0039 — MINE judge context must carry entity property values.

Validation run 2026-07-03: retention scored 7.7% on a document whose
facts WERE in the graph because the seed vertex's 768-float ``_embedding``
blew the TemplateSerializer entity line past the whole char budget, evicting
every entity line (and every property value) from the judge context. These
tests pin the fix: system-plane props excluded, values bounded, context
includes name + domain property values, output stays token-bounded.

Pure unit tests — mocked Arcade client / patched neighborhood fetch only.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.extraction.mine_sampler import (
    _GRAPH_CONTEXT_TOKEN_BUDGET,
    _MAX_PROP_VALUE_CHARS,
    _MAX_PROPS_PER_ENTITY,
    MINESampler,
    neighborhood_to_serializer_inputs,
)

_FAKE_EMBEDDING = [0.0123456789] * 768


def _seed_vertex(**overrides) -> dict:
    vertex = {
        "@type": "Insurance_Policy",
        "@rid": "#12:0",
        "grace_id": "gid-policy-1",
        "name": "Meridian Umbrella Policy",
        "policy_number": "GP-8894-1120",
        "jurisdiction": "BVI",
        "coverage_limit": "5,000,000 USD",
        "_embedding": list(_FAKE_EMBEDDING),
        "_deprecated": False,
        "sensitivity_tags": "|privileged|",
        "extraction_confidence": 0.92,
    }
    vertex.update(overrides)
    return vertex


def _neighborhood() -> dict:
    return {
        "seed": _seed_vertex(),
        "neighbors": [
            {
                "@type": "Legal_Entity",
                "grace_id": "gid-le-1",
                "name": "Meridian Holdings",
                "jurisdiction": "Delaware",
                "_embedding": list(_FAKE_EMBEDDING),
            }
        ],
        "edges": [
            {
                "source_grace_id": "gid-le-1",
                "target_grace_id": "gid-policy-1",
                "relationship_type": "holds_policy",
            }
        ],
    }


# ---------------------------------------------------------------------------
# neighborhood_to_serializer_inputs — property hygiene
# ---------------------------------------------------------------------------


def test_serializer_inputs_include_domain_properties() -> None:
    """Domain property values survive the conversion to RankedResult."""
    results, _ = neighborhood_to_serializer_inputs(_neighborhood())
    seed = results[0]
    assert seed.properties["policy_number"] == "GP-8894-1120"
    assert seed.properties["jurisdiction"] == "BVI"
    assert seed.properties["coverage_limit"] == "5,000,000 USD"


def test_serializer_inputs_exclude_system_plane_properties() -> None:
    """`_embedding` and other system-plane props never reach the judge."""
    results, _ = neighborhood_to_serializer_inputs(_neighborhood())
    for r in results:
        assert "_embedding" not in r.properties
        assert "sensitivity_tags" not in r.properties
        assert "extraction_confidence" not in r.properties
        assert "_deprecated" not in r.properties


def test_oversized_string_property_is_truncated_not_dropped() -> None:
    """A very long string value is bounded, keeping the line budget safe."""
    nb = _neighborhood()
    nb["seed"]["description"] = "x" * 10_000
    results, _ = neighborhood_to_serializer_inputs(nb)
    desc = results[0].properties["description"]
    assert len(desc) <= _MAX_PROP_VALUE_CHARS + 1  # + ellipsis
    assert desc.endswith("…")


def test_oversized_structured_value_under_domain_key_is_dropped() -> None:
    """A stray vector/blob under a non-system key is dropped, not serialized."""
    nb = _neighborhood()
    nb["seed"]["weird_vector"] = [0.5] * 500
    results, _ = neighborhood_to_serializer_inputs(nb)
    assert "weird_vector" not in results[0].properties


def test_per_entity_property_count_is_capped() -> None:
    nb = _neighborhood()
    for i in range(50):
        nb["seed"][f"extra_prop_{i:02d}"] = f"value-{i}"
    results, _ = neighborhood_to_serializer_inputs(nb)
    assert len(results[0].properties) <= _MAX_PROPS_PER_ENTITY


# ---------------------------------------------------------------------------
# MINESampler._build_graph_context — end-to-end (mocked graph)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_context_contains_property_values_not_embedding() -> None:
    """The serialized judge context carries name + domain property values.

    Regression for the exact F-0048 failure: pre-fix, the `_embedding`-bloated
    seed line broke the serializer's entity loop and the context contained
    ONLY edge lines.
    """
    sampler = MINESampler()
    with patch(
        "src.extraction.mine_sampler.fetch_entity_neighborhood",
        new=AsyncMock(return_value=_neighborhood()),
    ):
        context = await sampler._build_graph_context(
            AsyncMock(), ["gid-policy-1"]
        )

    # Entity lines with property values present
    assert 'Insurance_Policy "Meridian Umbrella Policy"' in context
    assert "policy_number=GP-8894-1120" in context
    assert "jurisdiction=BVI" in context
    # Edge line still present
    assert "holds_policy" in context
    # System plane excluded
    assert "_embedding" not in context
    assert "0.0123456789" not in context
    assert "sensitivity_tags" not in context


@pytest.mark.asyncio
async def test_graph_context_is_token_bounded() -> None:
    """Many property-heavy entities cannot blow past the serializer budget."""
    nb = _neighborhood()
    nb["neighbors"] = [
        {
            "@type": "Legal_Entity",
            "grace_id": f"gid-le-{i}",
            "name": f"Entity Number {i}",
            **{f"prop_{j}": "v" * 200 for j in range(15)},
        }
        for i in range(60)
    ]
    sampler = MINESampler()
    with patch(
        "src.extraction.mine_sampler.fetch_entity_neighborhood",
        new=AsyncMock(return_value=nb),
    ):
        context = await sampler._build_graph_context(
            AsyncMock(), ["gid-policy-1"]
        )

    # TemplateSerializer enforces token_budget * 4 chars; allow slack for the
    # final line boundary.
    assert len(context) <= _GRAPH_CONTEXT_TOKEN_BUDGET * 4 + 4096
    # Seed entity (serialized first) keeps its property values under pressure.
    assert "policy_number=GP-8894-1120" in context


@pytest.mark.asyncio
async def test_graph_context_no_seeds_message_unchanged() -> None:
    sampler = MINESampler()
    context = await sampler._build_graph_context(AsyncMock(), [])
    assert context == "No matching entities found in the knowledge graph."
