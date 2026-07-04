"""D548 guard — the graph writer must give every new vertex a name, falling back
to claim.subject_name when properties_json lacks "name".

Regression for Finding #15 (surfaced live by the bounded-heat apply-gate): the
vertex `name` property is written solely from EntityCreate.properties["name"]
(entity_ops.insert_entity), and canonical dedup keys on it too. But when the LLM
returns domain fields flat or sparse (weak models), `entity.properties` is empty,
so `claim.properties_json` is null even though `claim.subject_name` carries the
canonical name (ExtractedEntity.name is required). Without the fallback the vertex
persisted nameless → undedupable, uncorroboratable, unqueryable-by-name (the email
value-prop). The strong production model masks this by nesting properties + name.

Heat-free: embed_texts and all graph I/O are mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.extraction.claim_models import Claim, ClaimStatus
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractionBatch
from src.extraction.graph_writer import write_batch
from src.graph.entity_models import EntityCreateResponse


def _config() -> ExtractionSettings:
    return ExtractionSettings(
        extraction_base_url="http://localhost:11434",
        database_url="postgresql://localhost/test",
    )


def _nameless_props_claim(subject_name: str, properties_json) -> Claim:
    """A claim whose properties_json lacks 'name' — the Finding #15 shape."""
    return Claim(
        entity_type="Legal_Entity",
        subject_name=subject_name,
        subject_type="Legal_Entity",
        predicate="entity",
        properties_json=properties_json,
        status=ClaimStatus.AUTO_ACCEPTED,
        confidence=0.85,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        source_document_id="email:<deal-001@acme-fo.example>",
        resolved_entity_grace_id=None,  # forces the new-entity branch
    )


SCHEMA = {
    "entity_types": {"Legal_Entity": {"properties": {"name": {"data_type": "string"}}}},
    "relationships": {},
}


async def _run_and_capture_entitycreate(properties_json):
    claim = _nameless_props_claim("Acme Family Trust", properties_json)
    batch = ExtractionBatch(
        document_id="email:<deal-001@acme-fo.example>",
        claims=[claim], entities=[], relationships=[],
        claims_accepted=1, claims_quarantined=0,
    )
    with (
        patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}),
        patch("src.extraction.graph_writer.embed_texts", new_callable=AsyncMock, return_value=[[0.0] * 768]),
        patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert,
        patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"),
        patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1),
        patch("src.extraction.graph_writer.update_event_status_after_write"),
    ):
        mock_insert.return_value = EntityCreateResponse(
            grace_id="new-gid", rid="#1:0", entity_type="Legal_Entity",
            created=True, canonical_match=False,
        )
        await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _config())
    mock_insert.assert_called_once()
    # insert_entity(client, entity_create, embedding=...) — second positional arg
    call = mock_insert.call_args
    entity_create = call.args[1] if len(call.args) > 1 else call.kwargs["entity"]
    return entity_create


@pytest.mark.asyncio
async def test_empty_properties_json_still_names_vertex():
    """The exact Finding #15 shape: weak-model flat output -> properties == {}."""
    ec = await _run_and_capture_entitycreate({})
    assert ec.properties.get("name") == "Acme Family Trust"


@pytest.mark.asyncio
async def test_sparse_properties_without_name_get_name_injected():
    """Has a domain prop but no 'name' -> name injected, existing prop preserved."""
    ec = await _run_and_capture_entitycreate({"entity_form": "Trust"})
    assert ec.properties.get("name") == "Acme Family Trust"
    assert ec.properties.get("entity_form") == "Trust"


@pytest.mark.asyncio
async def test_existing_name_in_properties_is_not_overwritten():
    """If properties already carries a name, the fallback must not clobber it."""
    ec = await _run_and_capture_entitycreate({"name": "Acme Family Trust LLC"})
    assert ec.properties.get("name") == "Acme Family Trust LLC"
