"""Tests for signal-type → proposal-type mapping (CP2, D386, Chunk 47)."""

import pytest

from src.ontology.kgcl_parser import parse_kgcl
from src.ontology.models import ProposalType, SignalType
from src.ontology.signal_mapping import (
    SIGNAL_LITERAL_TO_ENUM,
    map_signal_to_proposals,
)


class TestSignalToProposalMapping:
    def test_signal_a_maps_to_add_entity_type(self):
        result = map_signal_to_proposals("A", {"affected_entity_types": ["Foo"]})
        assert len(result) == 1
        assert result[0][0] == ProposalType.ADD_ENTITY_TYPE

    def test_signal_b_maps_to_add_relationship(self):
        result = map_signal_to_proposals("B", {
            "source_type": "Person",
            "target_type": "Org",
            "relationship_name": "works_for",
        })
        assert len(result) == 1
        assert result[0][0] == ProposalType.ADD_RELATIONSHIP

    def test_signal_c_maps_to_modify_property(self):
        result = map_signal_to_proposals("C", {
            "affected_entity_types": ["Policy"],
            "property_name": "valid_from",
        })
        assert len(result) == 1
        assert result[0][0] == ProposalType.MODIFY_PROPERTY

    def test_signal_d_maps_to_deprecate_type(self):
        result = map_signal_to_proposals("D", {"affected_entity_types": ["OldType"]})
        assert len(result) == 1
        assert result[0][0] == ProposalType.DEPRECATE_TYPE

    def test_signal_e_maps_to_change_domain_range(self):
        # D534: grammar allows one target per command, so E emits domain + range
        # as two separate (well-formed) commands.
        result = map_signal_to_proposals("E", {
            "affected_entity_types": ["Entity"],
            "property_name": "owner",
            "new_domain": "Person",
            "new_range": "UUID",
        })
        assert len(result) == 2
        assert all(p == ProposalType.CHANGE_DOMAIN_RANGE for p, _ in result)
        assert "change domain of" in result[0][1]
        assert "change range of" in result[1][1]

    def test_signal_f_entity_gap_default(self):
        result = map_signal_to_proposals("F", {"affected_entity_types": ["NewType"]})
        assert len(result) == 1
        assert result[0][0] == ProposalType.ADD_ENTITY_TYPE

    def test_signal_f_relationship_gap(self):
        result = map_signal_to_proposals("F", {
            "gap_type": "relationship",
            "source_type": "A",
            "target_type": "B",
            "relationship_name": "links_to",
        })
        assert len(result) == 1
        assert result[0][0] == ProposalType.ADD_RELATIONSHIP

    def test_signal_f_property_gap(self):
        result = map_signal_to_proposals("F", {
            "gap_type": "property",
            "affected_entity_types": ["Person"],
            "property_name": "birth_date",
        })
        assert len(result) == 1
        assert result[0][0] == ProposalType.ADD_PROPERTY

    def test_kgcl_templates_non_empty(self):
        # F-37: a signal only emits when its evidence is groundable — B/C/E need
        # a relationship/property name in addition to an entity type, so each is
        # fed complete evidence here. (Refusal on ungrounded evidence is asserted
        # separately in the F-37 refuse tests below.)
        groundable = {
            "A": {"affected_entity_types": ["X"]},
            "B": {"affected_entity_types": ["X"], "relationship_name": "works_for"},
            "C": {"affected_entity_types": ["X"], "property_name": "valid_from"},
            "D": {"affected_entity_types": ["X"]},
            "E": {
                "affected_entity_types": ["X"],
                "property_name": "owner",
                "new_domain": "Person",
                "new_range": "String",
            },
            "F": {"affected_entity_types": ["X"]},
        }
        for signal in ["A", "B", "C", "D", "E", "F"]:
            result = map_signal_to_proposals(signal, groundable[signal])
            assert len(result) >= 1
            for _ptype, template in result:
                assert template.strip(), f"Empty KGCL template for signal {signal}"

    # ---------- F-37: refuse ungrounded proposals ----------

    def test_signal_d_grounds_from_real_entity_type_key(self):
        """Real Signal D evidence carries ``entity_type`` (not the legacy
        ``affected_entity_types``). It must ground the deprecation."""
        result = map_signal_to_proposals("D", {"entity_type": "Property"})
        assert len(result) == 1
        assert result[0][0] == ProposalType.DEPRECATE_TYPE
        assert "'Property'" in result[0][1]

    def test_signal_a_refuses_without_groundable_name(self):
        # Real Signal A evidence lists PRESENT types, not a missing-type name.
        assert map_signal_to_proposals("A", {"top_entity_types": [{"entity_type": "Person"}]}) == []
        assert map_signal_to_proposals("A", {}) == []

    def test_signal_b_refuses_without_relationship_name(self):
        # Real Signal B evidence: a co-occurring pair but no relationship label.
        ev = {"sample_orphan_pairs": [{"subject": "Fairview", "object": "Riverside"}]}
        assert map_signal_to_proposals("B", ev) == []

    def test_signals_never_emit_placeholder_names(self):
        # No emitted template may carry an ungrounded placeholder token.
        forbidden = ("UnknownType", "unknown_property", "related_to", "new_property")
        evidences = [
            ("A", {}),
            ("B", {}),
            ("C", {"top_tuples": [{"entity_type": "__none__"}]}),
            ("D", {"entity_type": "__none__"}),
            ("E", {}),
            ("F", {"gap_type": "relationship"}),
        ]
        for signal, ev in evidences:
            for _p, tmpl in map_signal_to_proposals(signal, ev):
                for tok in forbidden:
                    assert tok not in tmpl, f"{signal} emitted placeholder: {tmpl}"

    def test_all_mapped_types_are_valid_enum_members(self):
        for signal in ["A", "B", "C", "D", "E", "F"]:
            result = map_signal_to_proposals(signal, {"affected_entity_types": ["X"]})
            for ptype, _ in result:
                assert isinstance(ptype, ProposalType)

    def test_all_templates_parse(self):
        """D534 CI guard: every emitted KGCL command MUST parse via the executor's
        own parser — otherwise proposal_generator persists schema_proposals that
        change_executor can never apply. This contract test would have caught the
        5-of-8 malformed-branch bug (create edge / change property / change domain
        from … / create property). Covers every branch incl. F's three sub-shapes
        and multi-word (quoted) names.
        """
        cases = [
            ("A", {"affected_entity_types": ["Missing Type"]}),
            ("B", {"affected_entity_types": ["Acme Holdings"],
                   "relationship_name": "affiliated_with"}),
            ("C", {"affected_entity_types": ["Real Property"],
                   "property_name": "recorded_date"}),
            ("D", {"affected_entity_types": ["Legacy Deed"]}),
            ("E", {"affected_entity_types": ["Deed"], "property_name": "owner",
                   "new_domain": "Party", "new_range": "String"}),
            ("F", {"affected_entity_types": ["New Thing"]}),  # default branch
            ("F", {"gap_type": "relationship", "relationship_name": "links_to"}),
            ("F", {"gap_type": "property", "affected_entity_types": ["Person"],
                   "property_name": "birth_date"}),
            ("B", {}),  # empty evidence -> defaults must still parse
            ("E", {}),
        ]
        for signal, snapshot in cases:
            for _ptype, kgcl in map_signal_to_proposals(signal, snapshot):
                # parse_kgcl raises KGCLParseError on a malformed command
                parsed = parse_kgcl(kgcl)
                assert parsed.command_kind is not None, (
                    f"signal {signal} emitted unparseable KGCL: {kgcl!r}"
                )


class TestSignalLiteralToEnum:
    def test_exhaustive_coverage(self):
        assert set(SIGNAL_LITERAL_TO_ENUM.keys()) == {"A", "B", "C", "D", "E", "F"}
        for lit, enum_val in SIGNAL_LITERAL_TO_ENUM.items():
            assert isinstance(enum_val, SignalType)
            assert enum_val.value == f"signal_{lit.lower()}"
