"""D461 / Subject 3: Snapshot regression tests for existing KGCL long-form commands.

Ensures the parser changes in CP1-CP2 do not alter existing long-form behavior.
Exercises a representative sample of all 14 KGCLCommandKind values.
"""

from __future__ import annotations

import pytest

from src.ontology.kgcl_models import KGCLCommandKind, ProposedSchemaChange
from src.ontology.kgcl_parser import parse_kgcl


def test_rename_property_long_form_unchanged() -> None:
    """Long-form rename property parses identically pre/post D461."""
    result = parse_kgcl("rename property 'old_name' to 'new_name' on class 'Legal_Entity'")
    assert isinstance(result, ProposedSchemaChange)
    assert result.command_kind == KGCLCommandKind.RENAME_PROPERTY
    assert result.target_name == "old_name"
    assert result.property_name == "old_name"
    assert result.new_name == "new_name"
    assert result.entity_name == "Legal_Entity"


def test_add_synonym_long_form_unchanged() -> None:
    """Long-form add synonym parses identically pre/post D461."""
    result = parse_kgcl("add synonym 'Corp' for class 'Legal_Entity'")
    assert isinstance(result, ProposedSchemaChange)
    assert result.command_kind == KGCLCommandKind.ADD_SYNONYM
    assert result.target_name == "Legal_Entity"
    assert result.synonym == "Corp"


# Parametrized spot-check across the remaining 12 command kinds.
_COMMAND_SAMPLES = [
    ("create class 'NewType'", KGCLCommandKind.CREATE_CLASS, "NewType"),
    ("obsolete class 'OldType'", KGCLCommandKind.OBSOLETE_CLASS, "OldType"),
    ("add property 'field' to class 'Entity'", KGCLCommandKind.ADD_PROPERTY, "field"),
    ("remove property 'field' from class 'Entity'", KGCLCommandKind.REMOVE_PROPERTY, "field"),
    ("change property 'field' on class 'Entity'", KGCLCommandKind.CHANGE_PROPERTY, "field"),
    ("change description of 'Entity'", KGCLCommandKind.CHANGE_DESCRIPTION, "Entity"),
    ("create relationship 'has_parent'", KGCLCommandKind.CREATE_RELATIONSHIP, "has_parent"),
    ("obsolete relationship 'old_rel'", KGCLCommandKind.OBSOLETE_RELATIONSHIP, "old_rel"),
    ("change relationship 'has_parent'", KGCLCommandKind.CHANGE_RELATIONSHIP, "has_parent"),
    ("split class 'Org' into 'CorpOrg' 'GovOrg'", KGCLCommandKind.SPLIT_CLASS, "Org"),
    ("move class 'SubType' from 'OldParent' to 'NewParent'", KGCLCommandKind.MOVE_CLASS, "SubType"),
    ("change domain of 'has_parent' to 'Person'", KGCLCommandKind.CHANGE_DOMAIN_RANGE, "has_parent"),
]


@pytest.mark.parametrize("command,expected_kind,expected_target", _COMMAND_SAMPLES)
def test_existing_commands_spot_check(
    command: str,
    expected_kind: KGCLCommandKind,
    expected_target: str,
) -> None:
    """Each of the 12 remaining command kinds returns expected command_kind and target_name."""
    result = parse_kgcl(command)
    assert result.command_kind == expected_kind
    assert result.target_name == expected_target
