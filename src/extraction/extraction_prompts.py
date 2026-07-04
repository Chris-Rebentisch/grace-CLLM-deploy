"""System and user prompt templates for extraction.

Plain Python f-strings for editability, testability, and version control.
No external template engine.
"""

import structlog

log = structlog.get_logger()


def build_system_prompt(schema: dict) -> str:
    """Construct the system prompt with ontology schema and extraction instructions.

    Args:
        schema: Ontology JSON Schema dict (flat GrACE format or $defs format).

    Returns:
        Complete system prompt string for the extraction LLM call.
    """
    formatted_entity_types = _format_entity_types(schema)
    formatted_relationship_types = _format_relationship_types(schema)

    return (
        "You are a knowledge extraction system. Your task is to extract ALL entities\n"
        "and relationships from the provided text that match the ontology schema below.\n"
        "\n"
        "IMPORTANT: Extract EVERY entity and relationship the ontology can capture,\n"
        "not just what seems most important. Completeness is more critical than\n"
        "relevance. If in doubt, include it.\n"
        "\n"
        "=== ONTOLOGY SCHEMA ===\n"
        "\n"
        "ENTITY TYPES:\n"
        f"{formatted_entity_types}\n"
        "\n"
        "RELATIONSHIP TYPES:\n"
        f"{formatted_relationship_types}\n"
        "\n"
        "=== EXTRACTION INSTRUCTIONS ===\n"
        "\n"
        "For each entity you identify:\n"
        "- Set entity_type to one of the entity types listed above\n"
        "- Extract all properties defined for that entity type\n"
        "- Record which sentence indices [S0], [S1], etc. mention this entity in\n"
        "  source_sentence_indices\n"
        "- If the text mentions dates, time periods, or temporal references related\n"
        '  to this entity, include them in temporal_hints as raw strings\n'
        '  (e.g. {"start": "January 2024", "end": "March 2025"})\n'
        "\n"
        "For each relationship you identify:\n"
        "- Set predicate to one of the relationship types listed above\n"
        "- Set subject_name and object_name to the entity names involved\n"
        "- Set subject_type and object_type to their ontology entity types\n"
        "- Record which sentence indices support this relationship\n"
        "- Include temporal_hints if the relationship has temporal context\n"
        "\n"
        # F-0024 / ISS-0029: 4 confirmed subject/object reversals in the human
        # claims review (submitted_by, performed_by, has_landlord, governed_by,
        # plus an appoints_manager self-loop) — the extractor consistently put
        # the AGENT as subject where the ontology anchors the predicate on the
        # document/event. ~10 of 17 quarantined claims traced to this habit.
        # The schema signature is the contract; say so explicitly with
        # document-subject examples.
        "RELATIONSHIP DIRECTION:\n"
        "- The schema signature 'predicate: SourceType -> TargetType' DICTATES\n"
        "  the order: subject_name MUST be an entity of SourceType and\n"
        "  object_name an entity of TargetType. Never swap them, even when the\n"
        "  sentence names the agent first or uses passive voice.\n"
        "- Many predicates are anchored on the document or event, not the actor.\n"
        "  Example: submitted_by is Bid -> Vendor, so 'Vendor X submitted bid B'\n"
        "  yields subject_name=B (the BID), object_name=Vendor X.\n"
        "  Example: governed_by is Agreement -> Law, so the AGREEMENT is the\n"
        "  subject even though the law 'governs' it.\n"
        "- subject_name and object_name must be two DIFFERENT entities; never\n"
        "  emit a relationship from an entity to itself.\n"
        "\n"
        # F-009 / ISS-0016: claude-haiku returned entities but ZERO relationships
        # on 3/3 relationship-dense documents. Both list fields default to []
        # so an empty relationships list validates silently — the prompt must
        # make relationship capture explicitly mandatory, with a concrete
        # example, so weaker models do not treat it as optional.
        "RELATIONSHIP CAPTURE IS MANDATORY:\n"
        "- Every relationship explicitly stated in the text (e.g. ownership,\n"
        "  management, employment, leasing, insuring, being party to an agreement)\n"
        "  MUST be captured whenever a matching relationship type exists in the\n"
        "  schema above.\n"
        "- An EMPTY relationships list is ONLY acceptable when the text truly\n"
        "  contains no relationships between the extracted entities. If you\n"
        "  extracted two or more entities, re-check the text for connections\n"
        "  between them before returning an empty relationships list.\n"
        '- Example: the sentence "Acme Holdings LLC owns Cedar Grove, which is\n'
        '  managed by Jane Doe" yields TWO relationships:\n'
        "    subject_name=Acme Holdings LLC, predicate=owns, object_name=Cedar Grove\n"
        "    subject_name=Jane Doe, predicate=manages, object_name=Cedar Grove\n"
        "  (using whichever matching predicates the schema defines).\n"
        "\n"
        # F-024 / ISS-0016: email-bridge extraction produced first-name fragment
        # entities ("Diane", "Tom") never resolved to full names.
        "ENTITY NAMING:\n"
        "- Use the fullest canonical name that appears ANYWHERE in the text. If a\n"
        "  person is called 'Diane' in one sentence and 'Diane Mercer' anywhere\n"
        "  else in the document or email (including the From/sender line), the\n"
        "  entity name MUST be 'Diane Mercer'. Never emit a first-name-only\n"
        "  fragment or a truncated name when the full form appears in the text.\n"
        # F-0021 / ISS-0030: entity naming favored raw identifiers and
        # boilerplate titles ("GP-8894-1120", "Residential Lease Agreement" —
        # two different leases collide on that name). F-0022 / ISS-0030:
        # synthesized names for un-named events/decisions were nondeterministic
        # across identical temp=0 runs, churning every touching edge; a fixed
        # template makes the name reproducible.
        "- For document, contract, or case entities, synthesize a descriptive\n"
        "  canonical name: the subject plus a distinguishing qualifier, e.g.\n"
        "  'Residential Lease — 214 Cedar Grove'. Never use a boilerplate title\n"
        "  alone ('Residential Lease Agreement') or a bare reference number\n"
        "  ('GP-8894-1120') as the name. Keep the raw identifier or file number\n"
        "  as a property of the entity, not as its name.\n"
        "- For event or decision entities with no stated name in the text, build\n"
        "  the name from EXACTLY this template:\n"
        "    <deciding body or actor> <event kind> — <ISO date>\n"
        "  e.g. 'Meridian Family Council decision — 2026-01-20'. Add nothing\n"
        "  else — the same input must always produce the same name. Omit the\n"
        "  date part only if no date appears in the text.\n"
        "\n"
        "Only extract entity types and relationship types that appear in the schema\n"
        "above. Do not invent new types. If an entity does not fit any type, skip it."
    )


def build_user_prompt(
    chunk_text: str,
    sentence_offsets: list[tuple[int, int]],
    overlap_char_count: int = 0,
) -> str:
    """Construct the user prompt with sentence-annotated chunk text.

    Args:
        chunk_text: The chunk text content.
        sentence_offsets: List of (start, end) character positions of sentences.
        overlap_char_count: Number of leading characters that are overlap from
            the previous chunk.

    Returns:
        User prompt string with sentence annotations.
    """
    # Build annotated text
    if sentence_offsets:
        annotated_sentences = []
        for idx, (start, end) in enumerate(sentence_offsets):
            sentence = chunk_text[start:end]
            annotated_sentences.append(f"[S{idx}] {sentence}")
        annotated_text = "\n".join(annotated_sentences)
    else:
        annotated_text = chunk_text

    # Build overlap note
    overlap_note = ""
    if overlap_char_count > 0:
        overlap_note = (
            "Note: Sentences at the beginning of this text overlap with the previous\n"
            "chunk. Focus extraction on non-overlapping content, but include entities\n"
            "that span the overlap boundary.\n\n"
        )

    return (
        "Extract all entities and relationships from the following text.\n"
        "Each sentence is marked with [S#] for reference.\n"
        "\n"
        f"{overlap_note}"
        "--- TEXT START ---\n"
        f"{annotated_text}\n"
        "--- TEXT END ---"
    )


def _format_entity_types(schema: dict) -> str:
    """Format entity types section of the system prompt.

    Handles flat GrACE format (entity_types key) and $defs format.
    """
    if "entity_types" in schema:
        return _format_entity_types_flat(schema["entity_types"])
    elif "$defs" in schema:
        return _format_entity_types_defs(schema["$defs"])
    else:
        log.warning("prompt_no_entity_types", keys=list(schema.keys())[:5])
        return "No entity types defined in schema."


def _normalize_properties(properties) -> dict:
    """Normalize a `properties` field into the dict shape this module uses.

    Discovery's ratified ontology format ships ``properties`` as a LIST of
    ``{"name": ..., "type": ..., "description": ...}`` dicts; the Pydantic
    ``$defs`` format ships it as a DICT keyed by name. Both are valid
    upstream shapes — normalize here so the formatter doesn't crash with
    `'list' object has no attribute 'items'` (Phase-4 finding).
    """
    if isinstance(properties, dict):
        return properties
    if isinstance(properties, list):
        return {p["name"]: p for p in properties if isinstance(p, dict) and "name" in p}
    return {}


def _format_entity_types_flat(entity_types: dict) -> str:
    """Format entity types from flat GrACE format."""
    lines = []
    for type_name, type_def in entity_types.items():
        lines.append(f"- {type_name}")
        properties = _normalize_properties(type_def.get("properties", {}))
        for prop_name, prop_def in properties.items():
            prop_type = prop_def.get("type", "string")
            prop_desc = prop_def.get("description", "")
            desc_part = f" — {prop_desc}" if prop_desc else ""
            lines.append(f"    {prop_name}: {prop_type}{desc_part}")
    return "\n".join(lines) if lines else "No entity types defined in schema."


def _format_entity_types_defs(defs: dict) -> str:
    """Format entity types from Pydantic $defs format."""
    lines = []
    for type_name, type_def in defs.items():
        lines.append(f"- {type_name}")
        properties = _normalize_properties(type_def.get("properties", {}))
        for prop_name, prop_def in properties.items():
            prop_type = prop_def.get("type", "string")
            prop_desc = prop_def.get("description", "")
            desc_part = f" — {prop_desc}" if prop_desc else ""
            lines.append(f"    {prop_name}: {prop_type}{desc_part}")
    return "\n".join(lines) if lines else "No entity types defined in schema."


def _format_relationship_types(schema: dict) -> str:
    """Format relationship types section of the system prompt.

    Handles flat GrACE format (relationships key). For $defs format,
    predicates are not extractable.
    """
    if "relationships" in schema:
        lines = []
        for predicate, rel_def in schema["relationships"].items():
            # F-0013 (validation run, 2026-07-03): the ratified/active schema
            # stores endpoints as `source_type`/`target_type`; `domain` holds the
            # ontology MODULE name (e.g. "my_domain"). Reading `domain`/`range`
            # rendered every relationship as "name: my_domain -> ?" — no endpoint
            # types — and extraction relationship recall collapsed to zero (the
            # F-009 prompt hardening could not compensate for a vocabulary the model
            # never saw). Prefer the ratified keys; keep domain/range as legacy
            # fallback for seed-schema-shaped dicts. Description included so the
            # model can disambiguate near-synonym predicates.
            src = rel_def.get("source_type") or rel_def.get("domain") or "?"
            tgt = rel_def.get("target_type") or rel_def.get("range") or "?"
            desc = rel_def.get("description") or ""
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- {predicate}: {src} -> {tgt}{suffix}")
        return "\n".join(lines) if lines else "No relationship types defined in schema."
    elif "$defs" in schema:
        log.warning(
            "prompt_no_relationships_defs",
            msg="$defs schema: relationship predicates not extractable.",
        )
        return "No relationship types defined in schema."
    else:
        return "No relationship types defined in schema."
