"""Rule-based confidence scoring for extracted triples.

Initial confidence comes from extraction metadata (source sentences,
ontology type legality, properties, multi-chunk mentions). Verification
verdict adjusts the score into SUPPORTED/INSUFFICIENT/REFUTED bands.

Factor weights are unvalidated initial defaults. Calibrate from
entity_resolution_log / MINE-1 data after first batch run.
"""

from src.extraction.claim_models import ClaimVerdict
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.schema_utils import extract_allowed_types


def compute_initial_confidence(
    entity_or_rel,
    schema: dict,
    pre_dedup_chunk_count: int = 1,
) -> float:
    """Rule-based initial confidence from extraction metadata.

    Factor weights are unvalidated initial defaults. Calibrate from
    entity_resolution_log / MINE-1 data after first batch run.
    """
    score = 0.0

    # +0.20 if has source sentence references
    if len(entity_or_rel.source_sentence_indices) > 0:
        score += 0.20

    # +0.30 if entity_type or predicate is in schema-allowed types
    entity_types, predicates = extract_allowed_types(schema)
    type_name = getattr(entity_or_rel, "entity_type", None)
    predicate_name = getattr(entity_or_rel, "predicate", "")
    if type_name and type_name in entity_types:
        score += 0.30
    elif predicate_name and predicate_name in predicates:
        score += 0.30

    # +0.10 if has properties
    if len(entity_or_rel.properties) > 0:
        score += 0.10

    # +0.15 if appeared in 2+ chunks before dedup
    if pre_dedup_chunk_count >= 2:
        score += 0.15

    return min(score, 0.75)


def adjust_confidence_for_verdict(
    initial: float,
    verdict: ClaimVerdict,
    config: ExtractionSettings,
) -> float:
    """Apply verification verdict to adjust initial confidence.

    SUPPORTED: floor at config.confidence_threshold_supported (0.8).
    INSUFFICIENT: ceiling at config.confidence_threshold_insufficient (0.5).
    REFUTED: fixed at config.confidence_threshold_refuted (0.05).
    """
    if verdict == ClaimVerdict.SUPPORTED:
        return min(max(initial * 1.2, config.confidence_threshold_supported), 1.0)
    elif verdict == ClaimVerdict.INSUFFICIENT:
        return min(initial * 0.5, config.confidence_threshold_insufficient)
    elif verdict == ClaimVerdict.REFUTED:
        return config.confidence_threshold_refuted
    else:
        return initial


def score_claim(
    entity_or_rel,
    verdict: ClaimVerdict,
    schema: dict,
    config: ExtractionSettings,
    pre_dedup_chunk_count: int = 1,
) -> float:
    """Compute final confidence: initial rule-based + verification adjustment."""
    initial = compute_initial_confidence(entity_or_rel, schema, pre_dedup_chunk_count)
    return adjust_confidence_for_verdict(initial, verdict, config)
