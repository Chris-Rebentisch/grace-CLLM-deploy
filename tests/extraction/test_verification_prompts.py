"""Tests for verification prompt construction."""

from src.extraction.extraction_models import ExtractedEntity, ExtractedRelationship
from src.extraction.verification_prompts import (
    build_verification_system_prompt,
    build_verification_user_prompt,
    entity_to_hypothesis,
    relationship_to_hypothesis,
)


class TestEntityToHypothesis:
    def test_basic_entity(self):
        """Entity with no properties: '{name} is a {type}.'"""
        entity = ExtractedEntity(name="Acme Corp", entity_type="Legal_Entity")
        result = entity_to_hypothesis(entity)
        assert result == "Acme Corp is a Legal_Entity."

    def test_entity_with_properties(self):
        """Properties produce 'Its {key} is {value}.' statements."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="Legal_Entity",
            properties={"jurisdiction": "Delaware", "formation_date": "2019"},
        )
        result = entity_to_hypothesis(entity)
        assert "Acme Corp is a Legal_Entity." in result
        assert "Its jurisdiction is Delaware." in result
        assert "Its formation_date is 2019." in result

    def test_entity_property_cap(self):
        """More than 5 properties: only first 5 in hypothesis."""
        props = {f"prop_{i}": f"val_{i}" for i in range(8)}
        entity = ExtractedEntity(
            name="Test", entity_type="T", properties=props
        )
        result = entity_to_hypothesis(entity)
        assert result.count("Its ") == 5

    def test_empty_properties(self):
        """Empty properties dict: no 'Its' lines."""
        entity = ExtractedEntity(name="Test", entity_type="T", properties={})
        result = entity_to_hypothesis(entity)
        assert "Its " not in result
        assert result == "Test is a T."


class TestRelationshipToHypothesis:
    def test_basic_relationship(self):
        """Relationship format with types and predicate."""
        rel = ExtractedRelationship(
            subject_name="Acme",
            subject_type="Legal_Entity",
            predicate="party_to",
            object_name="Deal",
            object_type="Contract",
        )
        result = relationship_to_hypothesis(rel)
        assert "Acme (Legal_Entity) party to Deal (Contract)." in result

    def test_predicate_underscore_to_space(self):
        """Underscores in predicate replaced with spaces."""
        rel = ExtractedRelationship(
            subject_name="A",
            subject_type="T1",
            predicate="signed_by",
            object_name="B",
            object_type="T2",
        )
        result = relationship_to_hypothesis(rel)
        assert "signed by" in result
        assert "signed_by" not in result


class TestBuildVerificationUserPrompt:
    def test_includes_hypothesis_and_source(self):
        """Both hypothesis and annotated source text present."""
        hypothesis = "Acme Corp is a Legal_Entity."
        source = "Acme Corp is a legal entity. It was founded in 2019."
        offsets = [(0, 27), (28, 52)]
        result = build_verification_user_prompt(hypothesis, source, offsets)
        assert "CLAIMED FACT:" in result
        assert hypothesis in result
        assert "[S0]" in result
        assert "[S1]" in result
        assert "SOURCE TEXT" in result

    def test_empty_offsets_uses_raw_text(self):
        """Empty sentence_offsets: raw source text used."""
        hypothesis = "Test claim."
        source = "Raw text without offsets."
        result = build_verification_user_prompt(hypothesis, source, [])
        assert source in result
        assert "[S0]" not in result


class TestBuildVerificationSystemPrompt:
    def test_system_prompt_content(self):
        """System prompt contains key verification instructions."""
        result = build_verification_system_prompt()
        assert "SUPPORTED" in result
        assert "REFUTED" in result
        assert "INSUFFICIENT" in result
        assert "step by step" in result

    # F-0025a / ISS-0056 rubric-hardening pins: the validation run
    # showed the judge refuting claims its single source document merely
    # failed to mention. These assertions pin the hardened rubric lines
    # (whitespace-normalized so line-reflow doesn't break them) so a
    # prompt regression reopens the finding visibly.
    @staticmethod
    def _flat() -> str:
        """System prompt with all whitespace collapsed to single spaces."""
        return " ".join(build_verification_system_prompt().split())

    def test_refuted_requires_explicit_contradiction_in_this_text(self):
        """REFUTED definition is scoped to THIS text explicitly contradicting."""
        assert "Only when THIS text explicitly contradicts" in self._flat()

    def test_absence_of_mention_maps_to_insufficient(self):
        """Not-mentioned/not-supported must be steered to INSUFFICIENT."""
        flat = self._flat()
        assert (
            "If this text simply does not mention or support the fact, "
            "answer INSUFFICIENT" in flat
        )
        assert "even if you suspect the fact is false" in flat

    def test_single_document_scope_stated(self):
        """Judge is told other documents may support the fact (single-doc scope)."""
        flat = self._flat()
        assert "Other documents may support it" in flat
        assert "you are judging THIS text only" in flat

    def test_trade_name_dba_caution_present(self):
        """A dba/trade name is not a contradiction unless the text says so."""
        flat = self._flat()
        assert (
            "A party acting under a different name, abbreviation, or d/b/a "
            "is NOT a contradiction" in flat
        )
        assert "unless this text states they are different parties" in flat
