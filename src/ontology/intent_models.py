"""Intent-layer ontology models — the human-reasoning meta-layer (source of truth).

NORTH STAR: extract human intent and rationale and connect it to data in a queryable
KG, so future tasks (including agentic, human-inspired resolutions) can ask the graph
*how* and *why* a decision should be made — not just *what* is true.

The KG fact plane (``Agreement``, ``Obligation``, …) captures what documents say. This
module defines the orthogonal, domain-agnostic layer that captures the reasoning behind
those facts — the why no extraction pipeline can produce. Three vertex types:

  * ``Decision_Principle`` — a REUSABLE, domain-agnostic mental model ("control follows
    value"). One principle links many facts across many documents/domains. This is the
    transferable asset an agent applies to a decision it has never seen.
  * ``Decision_Rationale`` — the per-decision instance binding specific facts to the
    principle(s) they apply, under specific constraint / stakes / leverage / negotiation.
  * ``Counterfactual`` — the rejected alternative, FENCED (``is_term=False``,
    ``epistemic_status='rejected_alternative'``) so retrieval never serves a road-not-taken
    as a real term.

Pydantic is the source of truth (project rule). ``build_intent_schema_fragment()`` DERIVES
the flat GrACE schema entries from these models for the v#9 ontology ratification — the
schema is generated, not hand-written.

Design + decision record: ``grace-intent-elicitation/references/intent-layer-design.md``.
Provenance stamps (``decision_source`` etc.) are applied at write time by
``src/extraction/intent_writer.py`` — they are runtime stamps, not part of the semantic
schema (mirrors how decay stamps are not in the ``Obligation`` schema_json).
"""
from __future__ import annotations

from typing import ClassVar, Literal, get_args

from pydantic import BaseModel, Field

# --- closed vocabularies (D120/D217 bands; D-int-3 epistemic planes) ----------------
CertaintyBand = Literal["high", "medium", "low", "insufficient_evidence"]
# `compelled` (v#10, P1): the fact's PRESENCE is mandated, not a discretionary choice —
# distinct from `human_rationale` (a chosen why) so retrieval/agents never read a
# statute-boilerplate clause as a decision someone made.
EpistemicStatus = Literal["asserted_fact", "human_rationale", "rejected_alternative", "compelled"]

# v#10 P1 — mandatory-architecture vocabularies.
SourceOfCompulsion = Literal["statute", "regulation", "case_law", "payor_requirement"]
DegreeOfFreedom = Literal["none", "dollar_amount_only", "minor_wording"]

INTENT_MODULE = "intent"  # domain-agnostic meta-layer tag

# Edge types (D-int-1). (name, source_type, target_type, description). "Any" = links to
# any fact-plane vertex; recorded as schema metadata, not a DDL constraint.
INTENT_EDGE_TYPES: tuple[tuple[str, str, str, str], ...] = (
    ("explains", "Decision_Principle", "Any",
     "A reusable principle explains why a fact is the way it is (fast agent-retrieval path)."),
    ("justifies", "Decision_Rationale", "Any",
     "A per-decision rationale grounds a specific fact/clause."),
    ("applies_principle", "Decision_Rationale", "Decision_Principle",
     "A rationale is an instance applying an abstract principle."),
    ("rejected_alternative_to", "Counterfactual", "Any",
     "A fenced rejected alternative attached to the fact it was an alternative to."),
    ("traded_for", "Any", "Any",
     "Quid-pro-quo: two REAL facts linked as a negotiated exchange (the pair is the story)."),
    # v#10 P2 — dependency: A only functions as designed if B exists and is robust.
    # Distinct from traded_for (bilateral exchange) and Counterfactual (rejected path);
    # structural, load-bearing, often same document / same side of the table.
    ("depends_on", "Any", "Any",
     "Load-bearing dependency: the source fact only functions as designed if the target fact exists and is robust."),
    # v#10 P1 — a Mandatory_Provision compels the existence of a fact (statute boilerplate).
    ("compels", "Mandatory_Provision", "Any",
     "A mandated provision compels the existence of a fact — the clause had no live choice about its shape."),
    # v#11 P4 — principle hierarchy: a specific principle is a child of a more general one.
    # Lets an agent fall back to the general principle when the specific one does not fit.
    ("specializes", "Decision_Principle", "Decision_Principle",
     "The source principle is a more specific case of the target (general) principle."),
)


class DecisionPrinciple(BaseModel):
    """A reusable, domain-agnostic mental model that transfers across decisions.

    The crown jewel for the north star: an agent facing a novel decision retrieves the
    principle (by ``applies_when`` scope) and applies the human's reasoning. One principle
    node ``explains`` many facts across many documents — never collapse it into a rationale.
    """

    name: str = Field(description="Stable canonical slug; the dedup key (D106 + semantic canonicalization).")
    statement: str = Field(description="The reusable rule, in plain language ('control follows value; risk follows control').")
    applies_when: str = Field(description="The trigger/scope — when this principle applies. Embedded WITH the statement for retrieval (D-int-5).")
    certainty_band: CertaintyBand = Field(default="high", description="Human's confidence in the principle (bands only, never numbers — D120/D217).")
    domain_agnostic: bool = Field(default=True, description="True when the principle generalizes beyond the originating domain.")

    EPISTEMIC_STATUS: ClassVar[str] = "human_rationale"
    GRACE_TYPE: ClassVar[str] = "Decision_Principle"


class DecisionRationale(BaseModel):
    """The per-decision instance: why these specific facts were decided this way.

    Binds the fact(s) to the principle(s) they apply, plus the contextual reasoning the
    documents never contain — what constrained it, what was at stake, who had leverage,
    and what happened at the negotiating table.
    """

    name: str = Field(description="Stable canonical slug for this decision instance; the dedup key.")
    summary: str = Field(description="One-paragraph statement of the decision and its reasoning, plain language.")
    constraint: str = Field(default="", description="What forced the decision to be this way / what could not change.")
    stakes: str = Field(default="", description="What is protected or what breaks if the decision were reversed.")
    leverage: str = Field(default="", description="The power asymmetry that shaped where the term landed.")
    negotiation: str = Field(default="", description="Negotiation provenance — who argued what, what was conceded/traded.")
    certainty_band: CertaintyBand = Field(default="high", description="Human's confidence in this rationale (bands only).")
    resolver: str = Field(default="", description="For non-high bands: what evidence would raise the band (and any rival hypothesis).")
    # v#10 P3 — rationale salience: a rationale can carry a load-bearing reason plus
    # subordinate ones. "If you record one, record the controlling reason."
    controlling_reason: str = Field(default="", description="The load-bearing reason — the one that, alone, would not flip if the subordinate reasons changed.")
    subordinate_reasons: str = Field(default="", description="Contributing reasons that support but would NOT have flipped the outcome on their own.")

    EPISTEMIC_STATUS: ClassVar[str] = "human_rationale"
    GRACE_TYPE: ClassVar[str] = "Decision_Rationale"


class Counterfactual(BaseModel):
    """The rejected alternative — the road not taken. FENCED.

    Intent is defined by what was rejected; an agent reasons by elimination. But this is
    NOT a fact: ``is_term=False`` + ``epistemic_status='rejected_alternative'`` keep it out
    of the fact plane so retrieval never serves a rejected option as a real term.
    """

    name: str = Field(description="Stable canonical slug for the rejected alternative; the dedup key.")
    description: str = Field(description="Short label of the rejected path.")
    demanded: str = Field(description="What the alternative would have required.")
    why_rejected: str = Field(description="Why it was rejected — often the most diagnostic part of the decision.")
    is_term: bool = Field(default=False, description="ALWAYS False — the fence. This is not an actual term of the deal (D-int-3).")

    EPISTEMIC_STATUS: ClassVar[str] = "rejected_alternative"
    GRACE_TYPE: ClassVar[str] = "Counterfactual"


class MandatoryProvision(BaseModel):
    """A fact whose PRESENCE is compelled — not a discretionary decision (v#10, P1).

    Some clauses have zero degrees of freedom: federal safe-harbor language, statute-
    mandated terms, payor requirements. Eliciting a "why did you choose this shape"
    manufactures intent that does not exist. This type captures the *real* why — *why the
    clause must exist in any contract of this type* — and routes elicitation away from the
    discretionary-rationale prompt. ``compels`` edge links it to the fact it mandates.
    """

    name: str = Field(description="Stable canonical slug; the dedup key.")
    source_of_compulsion: SourceOfCompulsion = Field(description="What compels the clause (statute / regulation / case_law / payor_requirement) — the stable, real why.")
    degree_of_freedom: DegreeOfFreedom = Field(default="none", description="The negotiable surface around a fixed core (none / dollar_amount_only / minor_wording).")
    basis: str = Field(description="Why this clause must exist in ANY contract of this type — NOT 'why we chose this shape' (there was no choice).")

    EPISTEMIC_STATUS: ClassVar[str] = "compelled"
    GRACE_TYPE: ClassVar[str] = "Mandatory_Provision"


INTENT_VERTEX_MODELS: tuple[type[BaseModel], ...] = (
    DecisionPrinciple, DecisionRationale, Counterfactual, MandatoryProvision,
)

# Python annotation -> GrACE data_type
_DATA_TYPE = {str: "string", bool: "boolean", int: "integer", float: "float"}


def _grace_data_type(annotation) -> str:
    """Map a Pydantic field annotation to a GrACE schema data_type. Literals -> string."""
    if annotation in _DATA_TYPE:
        return _DATA_TYPE[annotation]
    # Literal[...] (e.g. CertaintyBand) -> string
    if get_args(annotation):
        return "string"
    return "string"


def _model_to_grace_properties(model: type[BaseModel]) -> dict:
    """DERIVE the flat GrACE property block from a Pydantic model's fields.

    Honors 'Pydantic is the source of truth' — the schema_json properties for v#9 are
    generated from the model, not hand-written. Field descriptions feed the schema.
    """
    props: dict = {}
    for fname, field in model.model_fields.items():
        props[fname] = {
            "name": fname,
            "required": field.is_required(),
            "data_type": _grace_data_type(field.annotation),
            "description": field.description or "",
            "answerable_cqs": [],
        }
    return props


def build_intent_schema_fragment() -> dict:
    """Build the {entity_types, relationships} fragment to merge into v8 schema_json for v#9.

    Domain = ``intent`` (the meta-layer). Vertex types carry ``_embedding`` so the existing
    ``index_manager.create_vector_indexes`` (D463 meta-entity carve-out) builds the
    LSMVectorIndex for server-side ``vectorNeighbors()`` (D-int-5).
    """
    entity_types: dict = {}
    for model in INTENT_VERTEX_MODELS:
        props = _model_to_grace_properties(model)
        # _embedding declared so create_vector_indexes() indexes this type (D-int-5/D463).
        props["_embedding"] = {
            "name": "_embedding", "required": False, "data_type": "embedding",
            "description": "768-dim nomic-embed-text vector over statement+applies_when (D-int-5).",
            "answerable_cqs": [],
        }
        entity_types[model.GRACE_TYPE] = {
            "domain": INTENT_MODULE,
            "confidence": 1.0,  # human-authored
            "epistemic_status": model.EPISTEMIC_STATUS,
            "properties": props,
        }

    relationships: dict = {}
    for name, src, tgt, desc in INTENT_EDGE_TYPES:
        relationships[name] = {
            "domain": INTENT_MODULE,
            "confidence": 1.0,
            "properties": {},
            "provenance": "human_intent",
            "description": desc,
            "source_type": src,
            "target_type": tgt,
            "richness_tier": "simple",
            "edge_properties": [],
        }
    return {"entity_types": entity_types, "relationships": relationships}
