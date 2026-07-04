"""Tests for extraction prompt construction."""

from src.extraction.extraction_prompts import build_system_prompt, build_user_prompt


class TestBuildSystemPrompt:
    def test_includes_entity_types(self, sample_ontology_schema):
        """System prompt contains all entity type names from schema."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "Legal_Entity" in prompt
        assert "Contract" in prompt

    def test_includes_relationship_predicates(self, sample_ontology_schema):
        """System prompt contains all relationship predicate names."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "party_to" in prompt

    def test_completeness_instruction(self, sample_ontology_schema):
        """System prompt contains the completeness emphasis instruction."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "Extract EVERY entity and relationship" in prompt
        assert "Completeness is more critical than" in prompt

    def test_temporal_hint_instruction(self, sample_ontology_schema):
        """System prompt mentions temporal_hints extraction."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "temporal_hints" in prompt

    def test_empty_schema(self):
        """Empty schema produces valid prompt with no types listed."""
        prompt = build_system_prompt({})
        assert "No entity types defined" in prompt
        assert "No relationship types defined" in prompt

    def test_entity_properties_shown(self, sample_ontology_schema):
        """Entity type properties are included in the prompt."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "jurisdiction" in prompt
        assert "effective_date" in prompt


class TestF009RelationshipMandate:
    """F-009 / ISS-0016: relationship capture must be explicitly mandatory."""

    def test_relationship_capture_mandatory_section(self, sample_ontology_schema):
        """System prompt carries the imperative relationship-capture block."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "RELATIONSHIP CAPTURE IS MANDATORY" in prompt
        assert "MUST be captured" in prompt

    def test_empty_relationships_only_when_none(self, sample_ontology_schema):
        """Empty relationships list is only acceptable when text has none."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "EMPTY relationships list is ONLY acceptable" in prompt
        assert "re-check the text" in prompt

    def test_concrete_relationship_example(self, sample_ontology_schema):
        """A concrete two-relationship example is included."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "Acme Holdings LLC" in prompt
        assert "predicate=owns" in prompt
        assert "predicate=manages" in prompt

    def test_full_canonical_name_rule(self, sample_ontology_schema):
        """F-024: no first-name-only fragments when the full name appears."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "ENTITY NAMING" in prompt
        assert "Diane Mercer" in prompt
        assert "first-name-only" in prompt


class TestF0024RelationshipDirection:
    """F-0024 / ISS-0029: schema signature dictates subject/object order."""

    def test_direction_section_present(self, sample_ontology_schema):
        """System prompt carries the explicit direction rule block."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "RELATIONSHIP DIRECTION" in prompt

    def test_signature_dictates_order(self, sample_ontology_schema):
        """The SourceType -> TargetType signature is named as the contract."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "SourceType -> TargetType" in prompt
        assert "subject_name MUST be an entity of SourceType" in prompt
        assert "Never swap them" in prompt

    def test_document_subject_example(self, sample_ontology_schema):
        """A document/event-subject example (submitted_by: Bid -> Vendor)."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "submitted_by is Bid -> Vendor" in prompt
        assert "subject_name=B (the BID)" in prompt

    def test_governed_by_example(self, sample_ontology_schema):
        """governed_by anchors on the agreement, not the law."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "governed_by is Agreement -> Law" in prompt

    def test_self_loop_forbidden(self, sample_ontology_schema):
        """The appoints_manager self-loop class is explicitly forbidden."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "two DIFFERENT entities" in prompt
        assert "relationship from an entity to itself" in prompt


class TestF0021F0022EntityNaming:
    """F-0021 + F-0022 / ISS-0030: descriptive + deterministic entity names."""

    def test_descriptive_document_name(self, sample_ontology_schema):
        """Document/contract entities get subject + qualifier names."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "Residential Lease — 214 Cedar Grove" in prompt
        assert "distinguishing qualifier" in prompt

    def test_identifier_stays_a_property(self, sample_ontology_schema):
        """Raw identifiers/file numbers are properties, never the name."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "GP-8894-1120" in prompt
        assert "as a property of the entity, not as its name" in prompt

    def test_boilerplate_title_forbidden(self, sample_ontology_schema):
        """Bare boilerplate titles that collide across documents are banned."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "boilerplate title" in prompt
        assert "'Residential Lease Agreement'" in prompt

    def test_deterministic_event_template(self, sample_ontology_schema):
        """Un-named events/decisions use the fixed naming template."""
        prompt = build_system_prompt(sample_ontology_schema)
        assert "<deciding body or actor> <event kind> — <ISO date>" in prompt
        assert "Meridian Family Council decision — 2026-01-20" in prompt
        assert "same input must always produce the same name" in prompt


class TestBuildUserPrompt:
    def test_sentence_annotation(self):
        """Sentences are annotated with [S0], [S1], etc."""
        text = "First sentence. Second sentence. Third sentence."
        offsets = [(0, 15), (16, 32), (33, 49)]
        prompt = build_user_prompt(text, offsets)
        assert "[S0]" in prompt
        assert "[S1]" in prompt
        assert "[S2]" in prompt

    def test_overlap_note(self):
        """When overlap_char_count > 0, overlap note is included."""
        text = "Overlap text. New text here."
        offsets = [(0, 13), (14, 28)]
        prompt = build_user_prompt(text, offsets, overlap_char_count=13)
        assert "overlap with the previous" in prompt

    def test_no_overlap_no_note(self):
        """When overlap_char_count == 0, no overlap note."""
        text = "Some text."
        offsets = [(0, 10)]
        prompt = build_user_prompt(text, offsets, overlap_char_count=0)
        assert "overlap with the previous" not in prompt

    def test_empty_offsets_raw_text(self):
        """Empty sentence_offsets uses raw chunk_text without annotation."""
        text = "Raw text without sentence splitting."
        prompt = build_user_prompt(text, [])
        assert "[S0]" not in prompt
        assert "Raw text without sentence splitting." in prompt

    def test_text_boundaries(self):
        """Prompt contains TEXT START and TEXT END markers."""
        text = "Test."
        prompt = build_user_prompt(text, [(0, 5)])
        assert "--- TEXT START ---" in prompt
        assert "--- TEXT END ---" in prompt
