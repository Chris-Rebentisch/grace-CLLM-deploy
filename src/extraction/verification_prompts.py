"""System and user prompt templates for verification.

Plain Python f-strings. No external template engine. Follows the same
[S#] sentence annotation pattern as extraction_prompts.py but with
verification-specific framing text.
"""

from src.extraction.extraction_models import ExtractedEntity, ExtractedRelationship


def build_verification_system_prompt() -> str:
    """Return the static system prompt for verification calls.

    Rubric hardened per F-0025a / ISS-0056 (validation run): the judge
    refuted claims that its single source document merely failed to mention
    (the fact was supported by a DIFFERENT document), quarantining true
    claims. Verification is deliberately single-document (GrACE-Product §10:
    a verdict is an auditable statement about one source; cross-document
    truth belongs to the corroboration/reconciliation layer), so the REFUTED
    definition now explicitly forbids absence-as-contradiction, and a
    trade-name/dba caution prevents refuting a party acting under a
    different name.
    """
    return (
        "You are a fact verification system. Your task is to determine whether a\n"
        "claimed fact is SUPPORTED, REFUTED, or has INSUFFICIENT evidence based\n"
        "on the provided source text.\n"
        "\n"
        "DEFINITIONS:\n"
        "- SUPPORTED: The source text directly states or clearly implies the\n"
        "  claimed fact. The evidence is explicit.\n"
        "- REFUTED: Only when THIS text explicitly contradicts the claimed\n"
        "  fact. There is explicit evidence against it. If this text simply\n"
        "  does not mention or support the fact, answer INSUFFICIENT — even\n"
        "  if you suspect the fact is false. Other documents may support it;\n"
        "  you are judging THIS text only.\n"
        "- INSUFFICIENT: The source text does not contain enough information to\n"
        "  confirm or deny the claimed fact. The fact may be true, but the\n"
        "  evidence is not in this text.\n"
        "\n"
        "CAUTION: A party acting under a different name, abbreviation, or\n"
        "d/b/a is NOT a contradiction unless this text states they are\n"
        "different parties.\n"
        "\n"
        "INSTRUCTIONS:\n"
        "- Read the source text carefully.\n"
        "- Compare it to the claimed fact.\n"
        "- Identify which sentences in the source text are relevant as evidence.\n"
        "- Determine your verdict: SUPPORTED, REFUTED, or INSUFFICIENT.\n"
        "- If REFUTED, explain what in the source text contradicts the claim.\n"
        "- Think step by step before giving your verdict."
    )


def build_verification_user_prompt(
    hypothesis: str,
    source_text: str,
    sentence_offsets: list[tuple[int, int]],
) -> str:
    """Construct the user prompt with hypothesis and annotated source text.

    Args:
        hypothesis: Natural-language claim to verify.
        source_text: Chunk text content.
        sentence_offsets: List of (start, end) character positions of sentences.

    Returns:
        User prompt string.
    """
    if sentence_offsets:
        annotated_sentences = []
        for idx, (start, end) in enumerate(sentence_offsets):
            sentence = source_text[start:end]
            annotated_sentences.append(f"[S{idx}] {sentence}")
        annotated_source_text = "\n".join(annotated_sentences)
    else:
        annotated_source_text = source_text

    return (
        "Verify whether the following claimed fact is supported by the source text.\n"
        "\n"
        "CLAIMED FACT:\n"
        f"{hypothesis}\n"
        "\n"
        "SOURCE TEXT (sentences marked with [S#]):\n"
        f"{annotated_source_text}"
    )


def entity_to_hypothesis(entity: ExtractedEntity) -> str:
    """Convert an extracted entity to a natural-language hypothesis.

    Args:
        entity: The entity to reformulate.

    Returns:
        Hypothesis string for verification.
    """
    hypothesis = f"{entity.name} is a {entity.entity_type}."
    for i, (key, value) in enumerate(entity.properties.items()):
        if i >= 5:
            break
        hypothesis += f" Its {key} is {value}."
    return hypothesis


def relationship_to_hypothesis(rel: ExtractedRelationship) -> str:
    """Convert an extracted relationship to a natural-language hypothesis.

    Args:
        rel: The relationship to reformulate.

    Returns:
        Hypothesis string for verification.
    """
    predicate_display = rel.predicate.replace("_", " ")
    hypothesis = (
        f"{rel.subject_name} ({rel.subject_type}) {predicate_display} "
        f"{rel.object_name} ({rel.object_type})."
    )
    for i, (key, value) in enumerate(rel.properties.items()):
        if i >= 5:
            break
        hypothesis += f" Its {key} is {value}."
    return hypothesis
