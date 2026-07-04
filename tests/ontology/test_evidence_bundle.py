"""Tests for EvidenceBundle model and SchemaProposal.evidence typing (CP1, Chunk 47)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.ontology.evidence_bundle import EvidenceBundle, evidence_bundle_from_db
from src.ontology.models import SchemaProposal


def _valid_bundle_kwargs() -> dict:
    return {
        "source_signal_ids": [uuid4()],
        "signal_type": "A",
        "signal_strength": 0.75,
        "affected_entity_types": ["Legal_Entity"],
        "ontology_module": "finance",
    }


class TestEvidenceBundleValidation:
    def test_valid_bundle_with_required_fields(self):
        bundle = EvidenceBundle(**_valid_bundle_kwargs())
        assert bundle.signal_type == "A"
        assert bundle.signal_strength == 0.75
        assert bundle.ontology_module == "finance"

    def test_rejects_missing_source_signal_ids(self):
        kwargs = _valid_bundle_kwargs()
        del kwargs["source_signal_ids"]
        with pytest.raises(ValidationError):
            EvidenceBundle(**kwargs)

    def test_rejects_missing_signal_type(self):
        kwargs = _valid_bundle_kwargs()
        del kwargs["signal_type"]
        with pytest.raises(ValidationError):
            EvidenceBundle(**kwargs)

    def test_rejects_missing_signal_strength(self):
        kwargs = _valid_bundle_kwargs()
        del kwargs["signal_strength"]
        with pytest.raises(ValidationError):
            EvidenceBundle(**kwargs)

    def test_rejects_missing_affected_entity_types(self):
        kwargs = _valid_bundle_kwargs()
        del kwargs["affected_entity_types"]
        with pytest.raises(ValidationError):
            EvidenceBundle(**kwargs)

    def test_rejects_missing_ontology_module(self):
        kwargs = _valid_bundle_kwargs()
        del kwargs["ontology_module"]
        with pytest.raises(ValidationError):
            EvidenceBundle(**kwargs)

    def test_signal_strength_rejects_out_of_range(self):
        kwargs = _valid_bundle_kwargs()
        kwargs["signal_strength"] = 1.5
        with pytest.raises(ValidationError):
            EvidenceBundle(**kwargs)

        kwargs["signal_strength"] = -0.1
        with pytest.raises(ValidationError):
            EvidenceBundle(**kwargs)

    def test_optional_fields_accepted(self):
        kwargs = _valid_bundle_kwargs()
        kwargs["example_documents"] = ["doc1.pdf"]
        kwargs["example_text_snippets"] = ["snippet 1"]
        kwargs["extraction_failure_count"] = 5
        kwargs["co_occurrence_count"] = 3
        kwargs["cq_text"] = "What types exist?"
        kwargs["evidence_summary_nl"] = "Missing entity type detected."
        bundle = EvidenceBundle(**kwargs)
        assert bundle.extraction_failure_count == 5
        assert bundle.evidence_summary_nl == "Missing entity type detected."


class TestSchemaProposalEvidenceTyping:
    def test_schema_proposal_accepts_evidence_bundle(self):
        bundle = EvidenceBundle(**_valid_bundle_kwargs())
        proposal = SchemaProposal(
            proposal_type="add_entity_type",
            change_tier=2,
            kgcl_command="create class Legal_Entity",
            proposed_diff={},
            evidence=bundle,
            raw_confidence=0.8,
            current_schema_version_id=uuid4(),
        )
        assert isinstance(proposal.evidence, EvidenceBundle)
        assert proposal.evidence.signal_type == "A"


class TestEvidenceBundleFromDb:
    def test_legacy_human_initiated_stub(self):
        # F-0042 / ISS-0053: legacy stubs no longer fabricate signal_type="A"
        # / signal_strength=0.0 — a signal-less bundle carries None for both.
        raw = {
            "affected_types": [],
            "signal_provenance": {"signal_type": "human_initiated"},
        }
        b = evidence_bundle_from_db(raw)
        assert b.source_signal_ids == []
        assert b.signal_type is None
        assert b.signal_strength is None
        assert b.affected_entity_types == []
        assert b.ontology_module == "general"

    def test_round_trip_new_shape(self):
        kwargs = _valid_bundle_kwargs()
        b = evidence_bundle_from_db(kwargs)
        assert b.signal_type == "A"
        assert len(b.source_signal_ids) == 1


class TestSignalScaffoldingNormalization:
    """F-0042 / ISS-0053: contradictory signal-less pairings are normalized."""

    def test_empty_source_ids_nulls_fabricated_signal_fields(self):
        b = EvidenceBundle(
            source_signal_ids=[],
            signal_type="A",
            signal_strength=0.8,
            affected_entity_types=["Person"],
            ontology_module="general",
        )
        assert b.signal_type is None
        assert b.signal_strength is None

    def test_real_signal_fields_preserved_with_source_ids(self):
        b = EvidenceBundle(**_valid_bundle_kwargs())
        assert b.signal_type == "A"
        assert b.signal_strength == 0.75

    def test_explicit_none_signal_fields_accepted(self):
        b = EvidenceBundle(
            source_signal_ids=[],
            signal_type=None,
            signal_strength=None,
            affected_entity_types=[],
            ontology_module="general",
        )
        assert b.signal_type is None
        assert b.signal_strength is None


class TestRefusalDetection:
    """F-0040 / ISS-0053: refusal-shaped LLM output is never stored as evidence."""

    def test_verbatim_observed_refusal_detected(self):
        from src.ontology.evidence_bundle import looks_like_refusal

        assert looks_like_refusal(
            "I don't have enough information to summarise this evidence. "
            "Could you provide additional details?"
        )

    def test_question_to_user_detected(self):
        from src.ontology.evidence_bundle import looks_like_refusal

        assert looks_like_refusal("Can you provide the affected entity types?")

    def test_declarative_summary_not_flagged(self):
        from src.ontology.evidence_bundle import looks_like_refusal

        assert not looks_like_refusal(
            "Extraction failures suggest a missing Zoning_Variance entity "
            "type in the property module."
        )

    @pytest.mark.asyncio
    async def test_generate_summary_stores_null_on_refusal(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from src.ontology import evidence_bundle as eb

        provider = MagicMock()
        provider.generate = AsyncMock(
            return_value=MagicMock(
                text="I don't have enough information. Could you provide additional details?"
            )
        )
        monkeypatch.setattr(
            "src.shared.llm_provider.get_provider", lambda: provider
        )
        bundle = EvidenceBundle(**_valid_bundle_kwargs())
        result = await eb.generate_evidence_summary(bundle)
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_summary_prompt_carries_legend_and_target(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from src.ontology import evidence_bundle as eb

        provider = MagicMock()
        provider.generate = AsyncMock(
            return_value=MagicMock(text="A concise declarative summary.")
        )
        monkeypatch.setattr(
            "src.shared.llm_provider.get_provider", lambda: provider
        )
        bundle = EvidenceBundle(**_valid_bundle_kwargs())
        result = await eb.generate_evidence_summary(
            bundle,
            kgcl_command="obsolete class 'Legal_Entity'",
            proposal_type="deprecate_type",
        )
        assert result == "A concise declarative summary."
        prompt = provider.generate.call_args.kwargs["user_prompt"]
        # Signal legend for signal A is present.
        assert "extraction failures" in prompt.lower()
        # The proposed change target is present.
        assert "obsolete class 'Legal_Entity'" in prompt
        assert "deprecate_type" in prompt
        # Anti-refusal instruction is present.
        assert "Do NOT ask questions" in prompt


class TestAffectedTypesFromParsedChange:
    """F-0040 / ISS-0053: affected_entity_types derived from the parse result."""

    def _parse(self, command: str):
        from src.ontology.kgcl_parser import parse_kgcl

        return parse_kgcl(command)

    def test_obsolete_class_yields_target(self):
        from src.ontology.evidence_bundle import affected_types_from_parsed_change

        change = self._parse("obsolete class 'Legal_Entity'")
        assert affected_types_from_parsed_change(change) == ["Legal_Entity"]

    def test_add_property_yields_owning_class(self):
        from src.ontology.evidence_bundle import affected_types_from_parsed_change

        change = self._parse("add property 'valid_from' to class 'Insurance_Policy'")
        assert affected_types_from_parsed_change(change) == ["Insurance_Policy"]

    def test_split_class_yields_target_and_splits(self):
        from src.ontology.evidence_bundle import affected_types_from_parsed_change

        change = self._parse("split class 'Asset' into 'Real_Asset' 'Financial_Asset'")
        types = affected_types_from_parsed_change(change)
        assert types[0] == "Asset"
        assert "Real_Asset" in types
        assert "Financial_Asset" in types
