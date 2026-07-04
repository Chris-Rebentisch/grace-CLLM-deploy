"""Tests for KGCL Pydantic models (Chunk 48, CP1)."""

import pytest
from pydantic import ValidationError

from src.ontology.kgcl_models import KGCLCommandKind, KGCLParseError, ProposedSchemaChange


class TestKGCLCommandKind:
    def test_exactly_14_members(self) -> None:
        assert len(KGCLCommandKind) == 14

    def test_all_expected_values_present(self) -> None:
        expected = {
            "create_class", "obsolete_class", "change_description",
            "create_relationship", "obsolete_relationship", "change_relationship",
            "add_property", "remove_property", "change_property",
            "add_synonym", "rename_property", "split_class",
            "move_class", "change_domain_range",
        }
        actual = {m.value for m in KGCLCommandKind}
        assert actual == expected


class TestProposedSchemaChange:
    def test_basic_creation(self) -> None:
        change = ProposedSchemaChange(
            command_kind=KGCLCommandKind.CREATE_CLASS,
            target_name="Foo",
        )
        assert change.command_kind == KGCLCommandKind.CREATE_CLASS
        assert change.target_name == "Foo"
        assert change.property_name is None

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ProposedSchemaChange(
                command_kind=KGCLCommandKind.CREATE_CLASS,
                target_name="Foo",
                bogus="bad",
            )

    def test_domain_range_fields(self) -> None:
        change = ProposedSchemaChange(
            command_kind=KGCLCommandKind.CHANGE_DOMAIN_RANGE,
            target_name="employs",
            to_type="Organization",
            change_target="domain",
        )
        assert change.change_target == "domain"
        assert change.to_type == "Organization"


class TestKGCLParseError:
    def test_attributes(self) -> None:
        err = KGCLParseError("bad", token="foo", offset=5)
        assert err.token == "foo"
        assert err.offset == 5
        assert err.message == "bad"
        assert str(err) == "bad"

    def test_defaults(self) -> None:
        err = KGCLParseError("oops")
        assert err.token is None
        assert err.offset == 0
