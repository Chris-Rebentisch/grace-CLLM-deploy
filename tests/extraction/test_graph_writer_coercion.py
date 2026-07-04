"""Tests for _coerce_props_to_schema strict coercion (F-0018 + F-0027/ISS-0032).

F-0027 / ISS-0032: the original F-0018 datetime coercion used
dateutil.parse(fuzzy=True), which INVENTED plausible-but-wrong dates
("Q1 2026" -> 2026-01-03T00:00 in-graph). The fix is strict parsing
(fuzzy=False + ambiguity double-parse); values that don't fully determine
year/month/day route to `<prop>_raw` and the vertex still writes
(preserving the F-0018 no-data-loss guarantee).

Pure unit tests — no ArcadeDB, no Postgres, no services.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.extraction.claim_models import Claim, ClaimStatus
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.graph_writer import _coerce_props_to_schema, write_batch
from src.extraction.extraction_models import ExtractionBatch
from src.graph.entity_models import EntityCreateResponse


SCHEMA = {
    "entity_types": {
        "Valuation": {
            "properties": {
                "name": {"data_type": "string"},
                "amount": {"data_type": "float"},
                "share_count": {"data_type": "integer"},
                "close_date": {"data_type": "datetime"},
                "effective_date": {"data_type": "date"},
                "is_final": {"data_type": "boolean"},
            }
        }
    },
    "relationships": {},
}


class TestNumericCoercionStillWorks:
    """F-0018 regression guard — the money/int paths must be unchanged."""

    def test_money_string_coerces_to_float(self):
        props = {"name": "Series B", "amount": "$2,500,000"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert out["amount"] == 2500000.0
        assert "amount_raw" not in out

    def test_integer_coerces(self):
        props = {"name": "Series B", "share_count": "1,200"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert out["share_count"] == 1200

    def test_boolean_coerces(self):
        props = {"name": "Series B", "is_final": "Yes"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert out["is_final"] is True

    def test_non_numeric_string_lands_in_raw(self):
        props = {"name": "Series B", "amount": "undisclosed"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert "amount" not in out
        assert out["amount_raw"] == "undisclosed"


class TestStrictDatetimeCoercion:
    """F-0027 / ISS-0032 — strict datetime parsing, no invented dates."""

    def test_unambiguous_long_form_parses(self):
        props = {"name": "Deal", "close_date": "February 20, 2026"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert out["close_date"] == "2026-02-20T00:00:00"
        assert "close_date_raw" not in out

    def test_iso_date_parses(self):
        props = {"name": "Deal", "close_date": "2026-03-31"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert out["close_date"] == "2026-03-31T00:00:00"

    def test_iso_datetime_parses(self):
        props = {"name": "Deal", "close_date": "2026-03-31T14:30:00"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert out["close_date"] == "2026-03-31T14:30:00"

    def test_date_data_type_also_strict(self):
        props = {"name": "Deal", "effective_date": "Q1 2026"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert "effective_date" not in out
        assert out["effective_date_raw"] == "Q1 2026"

    def test_quarter_form_does_not_become_datetime(self):
        """The F-0027 corruption case: 'Q1 2026' must NOT become 2026-01-03."""
        props = {"name": "Deal", "close_date": "Q1 2026"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert "close_date" not in out
        assert out["close_date_raw"] == "Q1 2026"

    def test_bare_year_rejected_as_underdetermined(self):
        """dateutil would fill month/day from `default` — that's invention."""
        props = {"name": "Deal", "close_date": "2026"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert "close_date" not in out
        assert out["close_date_raw"] == "2026"

    def test_bare_month_rejected_as_underdetermined(self):
        props = {"name": "Deal", "close_date": "March"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert "close_date" not in out
        assert out["close_date_raw"] == "March"

    def test_prose_date_rejected(self):
        """fuzzy=True used to extract a date out of prose; fuzzy=False must not."""
        props = {"name": "Deal", "close_date": "early 2026"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert "close_date" not in out
        assert out["close_date_raw"] == "early 2026"


class TestPassthrough:
    def test_undeclared_property_untouched(self):
        props = {"name": "Deal", "mystery_field": "Q1 2026"}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert out["mystery_field"] == "Q1 2026"

    def test_non_string_values_untouched(self):
        props = {"name": "Deal", "amount": 42.0, "close_date": None}
        out = _coerce_props_to_schema(props, "Valuation", SCHEMA)
        assert out["amount"] == 42.0
        assert out["close_date"] is None

    def test_unknown_entity_type_passthrough(self):
        props = {"amount": "$1,000"}
        out = _coerce_props_to_schema(props, "Nonexistent_Type", SCHEMA)
        assert out["amount"] == "$1,000"


@pytest.mark.asyncio
class TestVertexWriteProceeds:
    """The vertex must still write when a datetime value is rejected."""

    async def test_write_batch_writes_vertex_with_raw_fallback(self):
        claim = Claim(
            entity_type="Valuation",
            subject_name="Series B Valuation",
            subject_type="Valuation",
            predicate="entity",
            properties_json={
                "name": "Series B Valuation",
                "amount": "$2,500,000",
                "close_date": "Q1 2026",
            },
            confidence=0.9,
            status=ClaimStatus.AUTO_ACCEPTED,
            extraction_event_id=str(uuid4()),
            source_document_id="doc-1",
        )
        batch = ExtractionBatch(
            document_id="doc-1",
            claims=[claim],
            entities=[],
            relationships=[],
            claims_accepted=1,
            claims_quarantined=0,
        )
        config = ExtractionSettings(
            extraction_base_url="http://localhost:11434",
            database_url="postgresql://localhost/test",
        )

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.embed_texts", new_callable=AsyncMock, return_value=[[0.0] * 8]), \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
             patch("src.extraction.graph_writer.update_event_status_after_write"):
            mock_insert.return_value = EntityCreateResponse(
                grace_id="new-gid", rid="#1:0", entity_type="Valuation",
                created=True, canonical_match=False,
            )
            result = await write_batch(
                batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", config,
            )

        assert result.entities_created == 1
        assert result.entities_failed == 0
        entity_create = mock_insert.call_args.args[1]
        # Numeric coercion still works alongside the strict datetime path
        assert entity_create.properties["amount"] == 2500000.0
        # The loose date did NOT become a datetime — raw fallback instead
        assert "close_date" not in entity_create.properties
        assert entity_create.properties["close_date_raw"] == "Q1 2026"
