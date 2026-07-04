"""Typed evidence bundle for schema proposals (D388, Chunk 47).

Replaces the untyped ``evidence: dict`` on ``SchemaProposal`` with a
structured Pydantic model that captures signal provenance, affected
entity types, and optional natural-language summary.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from pydantic import BaseModel, Field, ValidationError, model_validator

from src.analytics.signal_pipeline.base import SignalTypeLiteral
from src.ontology.kgcl_models import KGCLCommandKind, ProposedSchemaChange

logger = structlog.get_logger()


class EvidenceBundle(BaseModel):
    """Typed evidence attached to a schema proposal."""

    source_signal_ids: list[UUID] = Field(
        description="UUIDs of analytics_signals rows that sourced this proposal",
    )
    signal_type: SignalTypeLiteral | None = Field(
        description=(
            "Signal type literal (A–F) that triggered the proposal; None for "
            "human-initiated proposals with no source signals (F-0042)"
        ),
    )
    signal_strength: float | None = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Normalised signal strength at detection time; None when there "
            "are no source signals (F-0042 — never fabricate 0.0)"
        ),
    )
    affected_entity_types: list[str] = Field(
        description="Ontology entity type names affected by the proposed change",
    )
    ontology_module: str = Field(
        description="Ontology module scope for the evidence",
    )

    # Optional enrichment fields
    example_documents: list[str] = Field(
        default_factory=list,
        description="Document identifiers that exemplify the signal",
    )
    example_text_snippets: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 representative text snippets",
    )
    extraction_failure_count: int | None = Field(
        default=None,
        description="Signal A: number of extraction failures observed",
    )
    co_occurrence_count: int | None = Field(
        default=None,
        description="Signal B: co-occurrence count without a schema edge",
    )
    cq_text: str | None = Field(
        default=None,
        description="Signal F: competency question text that exposed the gap",
    )
    evidence_summary_nl: str | None = Field(
        default=None,
        description="LLM-generated natural-language summary of the evidence",
    )

    @model_validator(mode="after")
    def _normalize_signal_scaffolding(self) -> EvidenceBundle:
        """Null out fabricated signal metrics on signal-less bundles.

        Capture-the-why (F-0042 / ISS-0053, validation run 2026-07-03):
        operator-authored proposals shipped ``source_signal_ids: []`` +
        ``signal_strength: 0.0`` (+ ``raw_confidence: 1.0`` on the proposal
        row) — a contradictory pairing accepted silently: the 0.0 strength
        reads as "a signal fired at zero strength" when in fact NO signal
        exists. Documented choice: NORMALIZATION (not 422) — when there are
        no source signals, ``signal_type``/``signal_strength`` are coerced
        to None so downstream readers see "absent", not fabricated numbers.
        Normalization was chosen over a 422 because the same model is used
        by ``evidence_bundle_from_db`` read-coercion of legacy rows, which
        must never raise on the read path.
        """
        if not self.source_signal_ids and (
            self.signal_type is not None or self.signal_strength is not None
        ):
            logger.warning(
                "evidence_bundle.signal_scaffolding_normalized",
                signal_type=self.signal_type,
                signal_strength=self.signal_strength,
                reason="no source_signal_ids — signal metrics would be fabricated",
            )
            self.signal_type = None
            self.signal_strength = None
        return self


def affected_types_from_parsed_change(change: ProposedSchemaChange) -> list[str]:
    """Derive ``affected_entity_types`` from a parsed KGCL change.

    Capture-the-why (F-0040 / ISS-0053, validation run 2026-07-03):
    a ``deprecate_type`` proposal shipped with an EMPTY
    ``affected_entity_types`` even though the target type was right there
    in the KGCL string — reviewers had no machine-readable pointer to what
    the change touches. The parse result carries the target; use it.
    """
    names: list[str] = []

    def _add(value: str | None) -> None:
        if value and value not in names:
            names.append(value)

    kind = change.command_kind
    if kind in (
        KGCLCommandKind.ADD_PROPERTY,
        KGCLCommandKind.REMOVE_PROPERTY,
        KGCLCommandKind.CHANGE_PROPERTY,
        KGCLCommandKind.RENAME_PROPERTY,
    ):
        # Property commands: the OWNING CLASS is the affected type.
        _add(change.entity_name)
        if change.entity_name is None:
            _add(change.target_name)
    else:
        _add(change.target_name)

    if kind == KGCLCommandKind.SPLIT_CLASS:
        for split_target in change.split_into or []:
            _add(split_target)
    if kind == KGCLCommandKind.CHANGE_DOMAIN_RANGE:
        _add(change.to_type)
    if kind == KGCLCommandKind.MOVE_CLASS:
        _add(change.old_parent)
        _add(change.new_parent)

    return names


def evidence_bundle_from_db(raw: dict | None) -> EvidenceBundle:
    """Coerce ``schema_proposals.evidence`` JSONB to :class:`EvidenceBundle`.

    Pre–Chunk-47 rows stored a human-initiated stub
    ``{affected_types, signal_provenance}`` shape. New rows use the full
    EvidenceBundle wire. This helper keeps list/get proposal paths from
    500-ing on legacy DB shapes (read-coercion only; new writes use the
    stricter model).
    """
    # F-0042 / ISS-0053: stubs no longer fabricate signal_type="A" /
    # signal_strength=0.0 — a signal-less bundle carries None for both.
    if not raw:
        return EvidenceBundle(
            source_signal_ids=[],
            signal_type=None,
            signal_strength=None,
            affected_entity_types=[],
            ontology_module="general",
        )
    try:
        return EvidenceBundle(**raw)
    except ValidationError:
        pass

    affected = list(raw.get("affected_entity_types") or raw.get("affected_types") or [])
    ontology_module = str(raw.get("ontology_module") or "general")
    return EvidenceBundle(
        source_signal_ids=[],
        signal_type=None,
        signal_strength=None,
        affected_entity_types=affected,
        ontology_module=ontology_module,
    )


# F-0040 / ISS-0053: legend so the summarizer knows what each signal literal
# MEANS — without it the model was flying blind and refused ("I don't have
# enough information…"), and the refusal text was stored as evidence.
_SIGNAL_LEGEND: dict[str, str] = {
    "A": "extraction failures suggest an entity type is missing from the schema",
    "B": "two entity types co-occur in documents but have no schema relationship (edge)",
    "C": "type drift — extracted instances no longer match their declared entity type",
    "D": "deprecation — an entity type has fallen out of use in the corpus",
    "E": "domain/range violation — a property appears on the wrong entity type",
    "F": "a competency question exposed a coverage gap in the schema",
}

# F-0040 / ISS-0053: refusal-shaped LLM output must never be stored as
# evidence. Heuristic markers: "not enough information" phrasing and
# question-to-the-user phrasing.
_REFUSAL_PHRASES: tuple[str, ...] = (
    "i don't have enough information",
    "i do not have enough information",
    "don't have enough information",
    "not enough information",
    "insufficient information",
    "could you provide",
    "can you provide",
    "please provide",
    "could you clarify",
    "could you share",
    "provide additional details",
    "i'm unable to summar",
    "i am unable to summar",
    "i cannot summar",
    "i can't summar",
)


def looks_like_refusal(text: str) -> bool:
    """True when LLM output is a refusal / question back to the user.

    Capture-the-why (F-0040 / ISS-0053, validation run 2026-07-03):
    ``evidence_summary_nl`` stored a VERBATIM LLM refusal ("I don't have
    enough information… Could you provide additional details?") as if it
    were evidence. A summary that asks the operator questions is not a
    summary — detect it and store null instead.
    """
    lowered = text.lower()
    if any(phrase in lowered for phrase in _REFUSAL_PHRASES):
        return True
    # Question addressed to the user (a real evidence summary is a
    # declarative sentence, never a question back to the reviewer).
    if "?" in text and any(
        marker in lowered for marker in ("could you", "can you", "would you", "do you have")
    ):
        return True
    return False


async def generate_evidence_summary(
    bundle: EvidenceBundle,
    *,
    kgcl_command: str | None = None,
    proposal_type: str | None = None,
) -> str | None:
    """Generate a natural-language summary of the evidence bundle via LLM.

    Returns the summary text on success, ``None`` on any failure (graceful
    degradation — airgap compatibility per D195/EC-7). Failures are logged
    at ``warning`` level; this function never raises.

    F-0040 / ISS-0053 (validation run 2026-07-03): the prompt now
    carries the signal legend + the proposed change target so the model has
    what it needs to summarise, and refusal-shaped output is detected and
    replaced with ``None`` + a structlog warning (never stored as evidence).
    """
    try:
        from src.shared.llm_provider import get_provider

        provider = get_provider()
        signal_meaning = (
            _SIGNAL_LEGEND.get(bundle.signal_type or "", "no automated signal")
            if bundle.signal_type
            else "human-initiated change (no automated signal)"
        )
        prompt = (
            "Summarise the following ontology-change evidence in one declarative "
            "sentence. Do NOT ask questions; if the evidence is thin, summarise "
            "what is present.\n"
            f"Signal type: {bundle.signal_type or 'none'} ({signal_meaning})\n"
            f"Ontology module: {bundle.ontology_module}\n"
            f"Affected entity types: {', '.join(bundle.affected_entity_types) or 'unspecified'}\n"
        )
        if proposal_type:
            prompt += f"Proposed change type: {proposal_type}\n"
        if kgcl_command:
            prompt += f"Proposed change (KGCL): {kgcl_command}\n"
        if bundle.extraction_failure_count is not None:
            prompt += f"Extraction failures observed: {bundle.extraction_failure_count}\n"
        if bundle.co_occurrence_count is not None:
            prompt += f"Co-occurrence count without schema edge: {bundle.co_occurrence_count}\n"
        if bundle.cq_text:
            prompt += f"Competency question that exposed the gap: {bundle.cq_text}\n"
        if bundle.example_text_snippets:
            prompt += f"Example snippets: {'; '.join(bundle.example_text_snippets)}\n"

        response = await provider.generate(
            system_prompt="You are a concise ontology analyst.",
            user_prompt=prompt,
            max_tokens=200,
            json_mode=False,
        )
        summary = (response.text or "").strip()
        if not summary:
            return None
        if looks_like_refusal(summary):
            # F-0040 / ISS-0053: never persist a refusal as evidence.
            logger.warning(
                "evidence_summary.refusal_detected",
                signal_type=bundle.signal_type,
                affected_entity_types=bundle.affected_entity_types,
                refusal_preview=summary[:120],
            )
            return None
        return summary
    except Exception:  # noqa: BLE001
        logger.warning("evidence_summary.generation_failed", exc_info=True)
        return None
