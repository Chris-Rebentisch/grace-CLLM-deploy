"""Tests for the federation rules engine (Chunk 51 CP5).

All functions are pure — no I/O, no mocks needed.
"""

from __future__ import annotations

import pytest

from src.federation.models import FederationConfig
from src.federation.rules_engine import (
    filter_properties_for_federation,
    resolve_namespace,
    should_share_layer,
)


@pytest.fixture()
def config() -> FederationConfig:
    return FederationConfig()


# ---------------------------------------------------------------------------
# filter_properties_for_federation
# ---------------------------------------------------------------------------


class TestFilterProperties:

    def test_domain_layer_passes_all(self, config):
        props = {"name": "Acme", "founded": "2020", "secret_flag": True}
        result = filter_properties_for_federation(props, "domain", config)
        assert result == props

    def test_temporal_layer_passes_all(self, config):
        props = {"valid_from": "2024-01-01", "valid_to": None}
        result = filter_properties_for_federation(props, "temporal", config)
        assert result == props

    def test_provenance_layer_passes_surface_only(self, config):
        props = {
            "source_document_id": "doc-123",
            "extraction_date": "2024-06-01",
            "last_updated": "2024-06-15",
            "human_reviewed": True,
            "internal_note": "confidential",
            "extractor_model": "qwen2.5:7b",
        }
        result = filter_properties_for_federation(props, "provenance", config)
        assert result == {
            "source_document_id": "doc-123",
            "extraction_date": "2024-06-01",
            "last_updated": "2024-06-15",
            "human_reviewed": True,
        }

    def test_provenance_layer_missing_properties_graceful(self, config):
        """Only present D403 surface properties are included."""
        props = {"source_document_id": "doc-123", "unrelated": "x"}
        result = filter_properties_for_federation(props, "provenance", config)
        assert result == {"source_document_id": "doc-123"}

    def test_governance_layer_passes_nothing(self, config):
        props = {"reviewer": "admin", "decision": "approved"}
        result = filter_properties_for_federation(props, "governance", config)
        assert result == {}

    def test_unknown_layer_passes_nothing(self, config):
        props = {"foo": "bar"}
        result = filter_properties_for_federation(props, "unknown_layer", config)
        assert result == {}


# ---------------------------------------------------------------------------
# should_share_layer
# ---------------------------------------------------------------------------


class TestShouldShareLayer:

    def test_domain_shared(self, config):
        assert should_share_layer("domain", config) is True

    def test_temporal_shared(self, config):
        assert should_share_layer("temporal", config) is True

    def test_provenance_siloed(self, config):
        assert should_share_layer("provenance", config) is False

    def test_governance_siloed(self, config):
        assert should_share_layer("governance", config) is False

    def test_unknown_layer_siloed(self, config):
        assert should_share_layer("something_else", config) is False


# ---------------------------------------------------------------------------
# resolve_namespace
# ---------------------------------------------------------------------------


class TestResolveNamespace:

    def test_prefix_match(self):
        assert resolve_namespace("Procore_Task", ["Procore"]) == "Procore"

    def test_no_prefix_mother_type(self):
        assert resolve_namespace("Legal_Entity", ["Procore"]) is None

    def test_empty_prefix_list(self):
        assert resolve_namespace("Procore_Task", []) is None

    def test_longest_match_wins(self):
        result = resolve_namespace(
            "ProcoreUS_Task", ["Procore", "ProcoreUS"]
        )
        assert result == "ProcoreUS"

    def test_multiple_candidates_longest_wins(self):
        result = resolve_namespace(
            "Alpha_Beta_Entity", ["Alpha", "Alpha_Beta"]
        )
        assert result == "Alpha_Beta"

    def test_exact_type_name_equals_prefix_no_match(self):
        """Prefix must be followed by '_' to match."""
        assert resolve_namespace("Procore", ["Procore"]) is None
