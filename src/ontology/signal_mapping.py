"""Deterministic signal-type to proposal-type mapping rules (D386, Chunk 47).

Pure functions — no LLM calls, no external dependencies. Each
``SignalTypeLiteral`` value maps to one or more ``ProposalType`` enum
values with KGCL command template strings. Uses the same KGCL surface
syntax as ``src/graph/kgcl_generator.py`` but does NOT import from it
(no coupling per spec §2.4).
"""

from __future__ import annotations

from src.analytics.signal_pipeline.base import SignalTypeLiteral
from src.ontology.models import ProposalType, SignalType

# Canonical encoding bridge: single-char signal literal to SignalType enum.
# This is the sole mapping site (spec §18.1).
SIGNAL_LITERAL_TO_ENUM: dict[str, SignalType] = {
    "A": SignalType.SIGNAL_A,
    "B": SignalType.SIGNAL_B,
    "C": SignalType.SIGNAL_C,
    "D": SignalType.SIGNAL_D,
    "E": SignalType.SIGNAL_E,
    "F": SignalType.SIGNAL_F,
}


# F-37: names that mean "no real grounding" — never emit a proposal carrying one.
_PLACEHOLDER_NAMES: frozenset[str] = frozenset(
    {
        "",
        "unknowntype",
        "unknown_property",
        "related_to",
        "new_property",
        "__none__",
        "__global__",
    }
)


def _is_placeholder(name: str | None) -> bool:
    """True when ``name`` is missing or a known ungrounded placeholder (F-37)."""
    return name is None or str(name).strip().lower() in _PLACEHOLDER_NAMES


def _extract_entity_name(signal_type: str, ev: dict) -> str | None:
    """Extract the real affected entity-type NAME per signal's actual evidence
    shape (F-37), falling back to the legacy ``affected_entity_types`` key.

    Real detector keys:
      * D  → ``entity_type`` (e.g. "Property")
      * C/E → ``top_tuples[0]["entity_type"]``
      * B  → ``sample_orphan_pairs[0]["subject"]`` (the co-occurring pair)
    """
    # Legacy explicit key (used by the D386/D534 test contract).
    legacy = ev.get("affected_entity_types") or []
    if legacy:
        return legacy[0]

    if signal_type == "D":
        return ev.get("entity_type")
    if signal_type in ("C", "E"):
        tuples = ev.get("top_tuples") or []
        if tuples and isinstance(tuples[0], dict):
            return tuples[0].get("entity_type")
    if signal_type == "B":
        pairs = ev.get("sample_orphan_pairs") or []
        if pairs and isinstance(pairs[0], dict):
            return pairs[0].get("subject")
    return None


def map_signal_to_proposals(
    signal_type: SignalTypeLiteral,
    evidence_snapshot: dict,
) -> list[tuple[ProposalType, str]]:
    """Map a signal type to ``(ProposalType, kgcl_command_template)`` pairs.

    Returns an empty list for unknown signal types.

    Capture-the-why (D534, 2026-06-22): every emitted ``kgcl_command`` MUST parse
    via ``src.ontology.kgcl_parser.parse_kgcl`` — otherwise ``proposal_generator``
    persists ``schema_proposals`` that ``change_executor`` can never apply. The
    prior templates emitted non-grammar forms for B/C/E and the F relationship/
    property branches (``create edge {r} between {s} and {t}``,
    ``change property {p} on {e}``, ``change domain of {p} from {e} to … and
    range to …``, ``create property {p} on {e}``) — 5 of 8 branches were rejected
    by the executor's own parser (surfaced by the A3 grace-gap-remediation-harness).
    The surface syntax now matches the canonical reference ``kgcl_generator.py``
    and quotes every name so multi-word entities tokenize as one token. Invariant
    guarded by ``tests/ontology/test_signal_mapping.py::test_all_templates_parse``.
    """
    # F-37 (validation run, 2026-07-01) capture-the-why: this function
    # read ``affected_entity_types`` / ``relationship_name`` / ``property_name``,
    # but NO signal detector emits those keys — Signal D emits ``entity_type``,
    # B emits ``sample_orphan_pairs``, C/E emit ``top_tuples``. So every real
    # proposal was named ``'UnknownType'`` / ``'unknown_property'`` / generic
    # ``'related_to'`` (reviewer-anon-01 rejected 7/8 as ungrounded noise). The
    # extractors below read the REAL detector keys first, fall back to the legacy
    # keys (preserving the D386/D534 test contract), and — per the finding —
    # REFUSE (return []) when a required name resolves to a placeholder: better no
    # proposal than an ungrounded one.
    entity_name = _extract_entity_name(signal_type, evidence_snapshot)

    match signal_type:
        case "A":
            # Signal A evidence carries only the PRESENT types (top_entity_types),
            # never a missing-type NAME, so a grounded `create class` requires the
            # legacy explicit key; refuse otherwise.
            if _is_placeholder(entity_name):
                return []
            return [(ProposalType.ADD_ENTITY_TYPE, f"create class '{entity_name}'")]
        case "B":
            # A co-occurrence-without-edge names WHICH types need a relationship,
            # but the relationship LABEL is not in evidence (KGCL captures only the
            # name). Emit only when a real relationship_name is supplied.
            rel_name = evidence_snapshot.get("relationship_name")
            if _is_placeholder(rel_name):
                return []
            return [(ProposalType.ADD_RELATIONSHIP, f"create relationship '{rel_name}'")]
        case "C":
            prop_name = evidence_snapshot.get("property_name")
            if _is_placeholder(entity_name) or _is_placeholder(prop_name):
                return []
            return [
                (
                    ProposalType.MODIFY_PROPERTY,
                    f"change property '{prop_name}' on class '{entity_name}'",
                ),
            ]
        case "D":
            # Signal D's evidence DOES carry the drifting/deprecated entity_type
            # (e.g. 'Property') — this is the grounded case F-37 highlights.
            if _is_placeholder(entity_name):
                return []
            return [(ProposalType.DEPRECATE_TYPE, f"obsolete class '{entity_name}'")]
        case "E":
            # Grammar allows one target per command; emit domain + range separately.
            prop_name = evidence_snapshot.get("property_name")
            new_domain = evidence_snapshot.get("new_domain", entity_name)
            new_range = evidence_snapshot.get("new_range")
            if _is_placeholder(prop_name) or _is_placeholder(new_domain) or _is_placeholder(new_range):
                return []
            return [
                (
                    ProposalType.CHANGE_DOMAIN_RANGE,
                    f"change domain of '{prop_name}' to '{new_domain}'",
                ),
                (
                    ProposalType.CHANGE_DOMAIN_RANGE,
                    f"change range of '{prop_name}' to '{new_range}'",
                ),
            ]
        case "F":
            return _map_signal_f(evidence_snapshot, entity_name)
        case _:
            return []


def _map_signal_f(
    evidence_snapshot: dict,
    entity_name: str,
) -> list[tuple[ProposalType, str]]:
    """Signal F branches based on evidence-snapshot shape."""
    gap_type = evidence_snapshot.get("gap_type")

    # F-37: refuse when the required name is a placeholder — Signal F's evidence
    # (top_failing_cqs) rarely carries a groundable type/relationship name.
    if gap_type == "relationship":
        rel_name = evidence_snapshot.get("relationship_name")
        if _is_placeholder(rel_name):
            return []
        return [(ProposalType.ADD_RELATIONSHIP, f"create relationship '{rel_name}'")]
    elif gap_type == "property":
        prop_name = evidence_snapshot.get("property_name")
        if _is_placeholder(prop_name) or _is_placeholder(entity_name):
            return []
        return [
            (
                ProposalType.ADD_PROPERTY,
                f"add property '{prop_name}' to class '{entity_name}'",
            ),
        ]
    else:
        # Default fallback: ADD_ENTITY_TYPE — most common CQ-driven gap pattern.
        if _is_placeholder(entity_name):
            return []
        return [(ProposalType.ADD_ENTITY_TYPE, f"create class '{entity_name}'")]
