"""Tests for KGCL recursive-descent parser (Chunk 48, CP1).

~30 tests: 14 positive, 14 negative, edge cases, round-trip, merge-types.
"""

import pytest

from src.ontology.kgcl_models import KGCLCommandKind, KGCLParseError, ProposedSchemaChange
from src.ontology.kgcl_parser import parse_kgcl


# ---------------------------------------------------------------------------
# 14 positive tests — one per command kind
# ---------------------------------------------------------------------------

class TestPositiveParsing:
    def test_create_class(self) -> None:
        r = parse_kgcl("create class 'Legal_Entity'")
        assert r.command_kind == KGCLCommandKind.CREATE_CLASS
        assert r.target_name == "Legal_Entity"

    def test_obsolete_class(self) -> None:
        r = parse_kgcl("obsolete class 'Old_Type'")
        assert r.command_kind == KGCLCommandKind.OBSOLETE_CLASS
        assert r.target_name == "Old_Type"

    def test_change_description(self) -> None:
        r = parse_kgcl("change description of 'Person'")
        assert r.command_kind == KGCLCommandKind.CHANGE_DESCRIPTION
        assert r.target_name == "Person"

    def test_create_relationship(self) -> None:
        r = parse_kgcl("create relationship 'employs'")
        assert r.command_kind == KGCLCommandKind.CREATE_RELATIONSHIP
        assert r.target_name == "employs"

    def test_obsolete_relationship(self) -> None:
        r = parse_kgcl("obsolete relationship 'owns'")
        assert r.command_kind == KGCLCommandKind.OBSOLETE_RELATIONSHIP
        assert r.target_name == "owns"

    def test_change_relationship(self) -> None:
        r = parse_kgcl("change relationship 'manages'")
        assert r.command_kind == KGCLCommandKind.CHANGE_RELATIONSHIP
        assert r.target_name == "manages"
        # CHANGE_RELATIONSHIP carries only target_name — no to_type or change_target.
        assert r.to_type is None
        assert r.change_target is None

    def test_add_property(self) -> None:
        r = parse_kgcl("add property 'email' to class 'Person'")
        assert r.command_kind == KGCLCommandKind.ADD_PROPERTY
        assert r.property_name == "email"
        assert r.entity_name == "Person"

    def test_remove_property(self) -> None:
        r = parse_kgcl("remove property 'fax' from class 'Company'")
        assert r.command_kind == KGCLCommandKind.REMOVE_PROPERTY
        assert r.property_name == "fax"
        assert r.entity_name == "Company"

    def test_change_property(self) -> None:
        r = parse_kgcl("change property 'name' on class 'Person'")
        assert r.command_kind == KGCLCommandKind.CHANGE_PROPERTY
        assert r.property_name == "name"
        assert r.entity_name == "Person"

    def test_add_synonym(self) -> None:
        r = parse_kgcl("add synonym 'Corp' for class 'Company'")
        assert r.command_kind == KGCLCommandKind.ADD_SYNONYM
        assert r.synonym == "Corp"
        assert r.target_name == "Company"

    def test_rename_property(self) -> None:
        r = parse_kgcl("rename property 'old_name' to 'new_name' on class 'Person'")
        assert r.command_kind == KGCLCommandKind.RENAME_PROPERTY
        assert r.property_name == "old_name"
        assert r.new_name == "new_name"
        assert r.entity_name == "Person"

    def test_split_class(self) -> None:
        r = parse_kgcl("split class 'Entity' into 'PersonEntity' 'OrgEntity'")
        assert r.command_kind == KGCLCommandKind.SPLIT_CLASS
        assert r.target_name == "Entity"
        assert r.split_into == ["PersonEntity", "OrgEntity"]

    def test_move_class(self) -> None:
        r = parse_kgcl("move class 'SubType' from 'OldParent' to 'NewParent'")
        assert r.command_kind == KGCLCommandKind.MOVE_CLASS
        assert r.target_name == "SubType"
        assert r.old_parent == "OldParent"
        assert r.new_parent == "NewParent"

    def test_change_domain_range_domain(self) -> None:
        r = parse_kgcl("change domain of 'employs' to 'Organization'")
        assert r.command_kind == KGCLCommandKind.CHANGE_DOMAIN_RANGE
        assert r.target_name == "employs"
        assert r.to_type == "Organization"
        assert r.change_target == "domain"

    def test_change_domain_range_range(self) -> None:
        r = parse_kgcl("change range of 'employs' to 'Person'")
        assert r.command_kind == KGCLCommandKind.CHANGE_DOMAIN_RANGE
        assert r.target_name == "employs"
        assert r.to_type == "Person"
        assert r.change_target == "range"


# ---------------------------------------------------------------------------
# 14 negative tests — malformed variant of each
# ---------------------------------------------------------------------------

class TestNegativeParsing:
    def test_create_class_missing_name(self) -> None:
        with pytest.raises(KGCLParseError):
            parse_kgcl("create class")

    def test_obsolete_class_missing_name(self) -> None:
        with pytest.raises(KGCLParseError):
            parse_kgcl("obsolete class")

    def test_change_description_missing_of(self) -> None:
        with pytest.raises(KGCLParseError, match="Expected 'of'"):
            parse_kgcl("change description 'Person'")

    def test_create_relationship_missing_name(self) -> None:
        with pytest.raises(KGCLParseError):
            parse_kgcl("create relationship")

    def test_obsolete_relationship_missing_name(self) -> None:
        with pytest.raises(KGCLParseError):
            parse_kgcl("obsolete relationship")

    def test_change_relationship_missing_name(self) -> None:
        with pytest.raises(KGCLParseError):
            parse_kgcl("change relationship")

    def test_add_property_missing_to(self) -> None:
        with pytest.raises(KGCLParseError, match="Expected 'to'"):
            parse_kgcl("add property 'email' class 'Person'")

    def test_remove_property_missing_from(self) -> None:
        with pytest.raises(KGCLParseError, match="Expected 'from'"):
            parse_kgcl("remove property 'fax' class 'Company'")

    def test_change_property_missing_on(self) -> None:
        with pytest.raises(KGCLParseError, match="Expected 'on'"):
            parse_kgcl("change property 'name' class 'Person'")

    def test_add_synonym_missing_for(self) -> None:
        with pytest.raises(KGCLParseError, match="Expected 'for'"):
            parse_kgcl("add synonym 'Corp' class 'Company'")

    def test_rename_property_missing_to(self) -> None:
        with pytest.raises(KGCLParseError, match="Expected 'to'"):
            parse_kgcl("rename property 'old' 'new' on class 'Person'")

    def test_split_class_only_one_target(self) -> None:
        with pytest.raises(KGCLParseError, match="at least two"):
            parse_kgcl("split class 'Entity' into 'OnlyOne'")

    def test_move_class_missing_from(self) -> None:
        with pytest.raises(KGCLParseError, match="Expected 'from'"):
            parse_kgcl("move class 'Sub' to 'NewParent'")

    def test_change_domain_missing_of(self) -> None:
        with pytest.raises(KGCLParseError, match="Expected 'of'"):
            parse_kgcl("change domain 'employs' to 'Org'")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_input(self) -> None:
        with pytest.raises(KGCLParseError, match="Empty command"):
            parse_kgcl("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(KGCLParseError, match="Empty command"):
            parse_kgcl("   ")

    def test_unknown_command(self) -> None:
        with pytest.raises(KGCLParseError, match="Unknown command"):
            parse_kgcl("frobnicate class 'Foo'")

    def test_unterminated_quote(self) -> None:
        with pytest.raises(KGCLParseError, match="Unterminated"):
            parse_kgcl("create class 'Foo")

    def test_missing_arguments(self) -> None:
        with pytest.raises(KGCLParseError):
            parse_kgcl("create")

    def test_merge_types_specific_error(self) -> None:
        """D390: merge types must raise a specific error, not generic 'unknown command'."""
        with pytest.raises(KGCLParseError, match="merge types is not supported in v1"):
            parse_kgcl("merge types 'A' 'B'")


# ---------------------------------------------------------------------------
# Round-trip: kgcl_generator.py output -> parser
# ---------------------------------------------------------------------------

class TestRoundTrip:
    """Feed kgcl_generator.py output into parser, verify structural equivalence.

    Import kgcl_generator in TEST only — never in runtime code (spec constraint).
    """

    def test_generator_output_parses_all_9_templates(self) -> None:
        from src.graph.kgcl_generator import generate_kgcl_commands

        diff_summary = {
            "entity_types": {
                "added": ["NewType"],
                "removed": ["OldType"],
                "modified": [{"name": "ChangedType"}],
            },
            "relationships": {
                "added": ["new_rel"],
                "removed": ["old_rel"],
                "modified": [{"name": "changed_rel"}],
            },
            "properties": {
                "added": [{"entity": "Person", "property": "email"}],
                "removed": [{"entity": "Company", "property": "fax"}],
                "modified": [{"entity": "Person", "property": "name"}],
            },
        }

        commands = generate_kgcl_commands(diff_summary)
        assert len(commands) == 9

        expected_kinds = [
            KGCLCommandKind.CREATE_CLASS,
            KGCLCommandKind.OBSOLETE_CLASS,
            KGCLCommandKind.CHANGE_DESCRIPTION,
            KGCLCommandKind.CREATE_RELATIONSHIP,
            KGCLCommandKind.OBSOLETE_RELATIONSHIP,
            KGCLCommandKind.CHANGE_RELATIONSHIP,
            KGCLCommandKind.ADD_PROPERTY,
            KGCLCommandKind.REMOVE_PROPERTY,
            KGCLCommandKind.CHANGE_PROPERTY,
        ]

        for cmd_str, expected_kind in zip(commands, expected_kinds):
            parsed = parse_kgcl(cmd_str)
            assert parsed.command_kind == expected_kind, (
                f"Command '{cmd_str}' parsed as {parsed.command_kind}, expected {expected_kind}"
            )
