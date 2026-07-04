"""Prompt templates for two-stage CQ-constrained schema extraction.

Stage 1: Identify types (lightweight — names, descriptions, CQ IDs only).
Stage 2: Detail each type (properties, evidence documents, relationships).
"""

import json

# --- Stage 1 prompts (identify types) ---

STAGE1_SYSTEM_PROMPT = """You are an ontology engineer analyzing organizational documents to propose entity types
and relationship types for a knowledge graph schema. You are extracting TYPES (categories),
not INSTANCES (specific things).

RULES:
- Every proposed entity type MUST cite at least one competency question it helps answer.
- Every proposed relationship MUST cite at least one competency question it helps answer.
- Use PascalCase_With_Underscores for entity types (e.g. Legal_Entity), snake_case for relationships.
- If a type aligns with a seed pattern, note the alignment. Do NOT include a type just because
  it's in the seed — only include types with CQ support AND document evidence.
- Do NOT include properties in this response (those come later).
- A non-technical business owner will review these. For every type and relationship you MUST also write:
  * "display_label": a plain-English, non-technical label a CFO would recognize. For an entity type
    use a PLURAL noun phrase (e.g. Legal_Entity -> "Companies & Organizations"; Agreement -> "Contracts
    & Agreements"). For a relationship use a readable verb phrase (e.g. party_to -> "is a party to").
    No underscores, no PascalCase, no jargon.
  * "plain_description": ONE sentence, no graph/ontology terminology, explaining what this is in the
    reader's own world.
  * "example_snippet": a SHORT verbatim quote (<= 160 characters) copied from the supplied document
    text that shows a real instance of this type/relationship. Copy it exactly; never invent one. If
    you genuinely cannot find a quote, use null.
  * "evidence_documents" (entity types only): up to 5 filenames taken from the
    "--- Document: <name> ---" headers in the supplied text where this type appears. This shows the
    reviewer how common the type is. Only list documents you actually saw it in.
- Return a MAXIMUM of {max_types} entity types and {max_relationships} relationships.
  Focus on the most important types that answer the most competency questions.
  If you identify more than the limit, prioritize by CQ coverage.
- Respond with ONLY valid JSON. Do NOT wrap in markdown code fences (no ```json blocks)."""

STAGE1_RESPONSE_SCHEMA = json.dumps(
    {
        "entity_types": [
            {
                "name": "Legal_Entity",
                "parent_type": None,
                "description": "An organization with legal standing",
                "display_label": "Companies & Organizations",
                "plain_description": "The businesses, trusts, and legal entities named in your documents.",
                "example_snippet": "Acme Capital Partners, LLC, a Delaware limited liability company",
                "evidence_documents": ["operating_agreement.pdf", "articles_of_incorporation.pdf"],
                "domain": "corporate_structure",
                "answerable_cqs": ["cq_001", "cq_005"],
                "seed_alignment": "LegalEntity",
            }
        ],
        "relationships": [
            {
                "name": "registered_in",
                "source_type": "Legal_Entity",
                "target_type": "Jurisdiction",
                "description": "Where an entity is legally registered",
                "display_label": "is registered in",
                "plain_description": "Connects a company to the place where it is legally formed.",
                "example_snippet": "a Delaware limited liability company",
                "answerable_cqs": ["cq_001"],
                "seed_alignment": "isRecognizedIn",
            }
        ],
    },
    indent=2,
)


# --- Stage 2 prompts (detail one type) ---

STAGE2_SYSTEM_PROMPT = """You are an ontology engineer detailing a specific entity type for a knowledge graph schema.
Given the entity type name and description, identify its properties, evidence documents,
and outgoing relationships from this type.

RULES:
- Properties: name (snake_case), data_type (string/datetime/float/boolean/integer/reference),
  description, required flag, and CQ IDs the property helps answer.
- Evidence documents: filenames from the documents where you found evidence for this type.
- Relationships from this type: only relationships where this type is the SOURCE.
  Include name (snake_case), target_type, description, richness_hint (simple/attributed/reified),
  edge_properties (for attributed/reified), and CQ IDs.
- Respond with ONLY valid JSON. Do NOT wrap in markdown code fences (no ```json blocks)."""

STAGE2_RESPONSE_SCHEMA = json.dumps(
    {
        "name": "Legal_Entity",
        "properties": [
            {
                "name": "jurisdiction",
                "data_type": "string",
                "description": "Legal jurisdiction of registration",
                "required": True,
                "answerable_cqs": ["cq_001"],
            }
        ],
        "evidence_documents": ["articles_of_incorporation.pdf"],
        "relationships_from_this_type": [
            {
                "name": "registered_in",
                "target_type": "Jurisdiction",
                "description": "Where an entity is legally registered",
                "richness_hint": "simple",
                "edge_properties": [],
                "answerable_cqs": ["cq_001"],
            }
        ],
    },
    indent=2,
)


def _format_cq_corpus(cqs: list) -> str:
    """Format CQ list for prompt injection.

    Each CQ includes its short ID (first 8 chars of UUID) and canonical_text.
    Accepts CompetencyQuestion Pydantic models or dicts with 'id' and 'canonical_text'.
    """
    lines = []
    for cq in cqs:
        if hasattr(cq, "id") and hasattr(cq, "canonical_text"):
            short_id = str(cq.id)[:8]
            text = cq.canonical_text
        elif isinstance(cq, dict):
            short_id = str(cq.get("id", ""))[:8]
            text = cq.get("canonical_text", "")
        else:
            continue
        lines.append(f"  [{short_id}] {text}")
    return "\n".join(lines)


# --- Stage 1 per-pass templates ---

# NOTE: Section order is load-bearing for Ollama/llama.cpp prompt caching.
# The large, call-invariant block (DOCUMENTS + CQ corpus) is the SHARED PREFIX
# across all three Stage-1 passes; only the small approach-specific tail varies.
# This lets the ~48k-token corpus be prefilled once and its KV cache reused for
# every subsequent pass. Do NOT move {document_text}/{cq_corpus} below the
# pass-specific APPROACH text — that reintroduces the per-call full re-prefill.
STAGE1_TOP_DOWN = """DOCUMENTS:
{document_text}

Here are the questions this organization needs to answer:
{cq_corpus}

APPROACH: Top-Down — Start with the broadest categories, then drill into subtypes.

{seed_section}

Looking at the documents above, identify the 5-{max_types} broadest categories of entities
needed to answer these questions. For each category, identify specific subtypes.
Organize your output as a hierarchy: parent types with their children.
Ground every proposed type in at least one CQ it helps answer.

RESPONSE FORMAT:
{response_schema}"""

STAGE1_BOTTOM_UP = """DOCUMENTS:
{document_text}

Here are the questions this organization needs to answer:
{cq_corpus}

APPROACH: Bottom-Up — Start with specific things you find, then group them.

IMPORTANT: For your initial analysis, IGNORE the seed reference patterns below.
List the {max_types} most important specific entity types and relationship types you can identify
in the documents that would be needed to answer one or more of these questions.
Be specific — if you see different kinds of contracts, list each kind separately.
After listing, group related items and propose parent categories.
Cite which CQs each type addresses.

After your analysis, compare your findings against these seed patterns and note
where they align or diverge:
{seed_section}

RESPONSE FORMAT:
{response_schema}"""

STAGE1_MIDDLE_OUT = """DOCUMENTS:
{document_text}

Here are the questions this organization needs to answer:
{cq_corpus}

APPROACH: Middle-Out — Start with the practical working vocabulary.

{seed_vocab_section}

What terms would someone working at this organization naturally use when asking
and answering these questions? Identify the 10-{max_types} most practical entity types
and relationships that carry the most day-to-day operational weight.
For each, note which CQs it helps answer.

RESPONSE FORMAT:
{response_schema}"""


_STAGE1_TEMPLATES = {
    "top_down": STAGE1_TOP_DOWN,
    "bottom_up": STAGE1_BOTTOM_UP,
    "middle_out": STAGE1_MIDDLE_OUT,
}


def build_stage1_prompt(
    pass_name: str,
    domain: str,
    document_text: str,
    cqs: list,
    seed_reference_text: str | None = None,
    config: dict | None = None,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a Stage 1 extraction pass.

    Stage 1 identifies types with names, descriptions, CQ IDs only.
    No properties, no evidence documents.
    """
    if pass_name not in _STAGE1_TEMPLATES:
        raise ValueError(
            f"Unknown pass: {pass_name}. Must be one of: {list(_STAGE1_TEMPLATES.keys())}"
        )

    template = _STAGE1_TEMPLATES[pass_name]
    cq_corpus = _format_cq_corpus(cqs)

    # Read caps from config (with defaults)
    schema_config = (config or {}).get("schema_extraction", {})
    max_types = schema_config.get("stage1_max_types", 15)
    max_relationships = schema_config.get("stage1_max_relationships", 15)

    # Format system prompt with caps
    system_prompt = STAGE1_SYSTEM_PROMPT.format(
        max_types=max_types, max_relationships=max_relationships
    )

    # "other" domain guidance
    other_note = ""
    if domain == "other":
        other_note = (
            "\nNOTE: This domain contains documents from multiple business areas. "
            "Look for common patterns across these diverse documents. "
            "Propose types that would apply broadly.\n"
        )

    if pass_name == "top_down":
        seed_section = (
            ("Here are structural patterns from established ontologies:\n" + seed_reference_text)
            if seed_reference_text else ""
        )
        user_prompt = template.format(
            cq_corpus=cq_corpus,
            seed_section=seed_section,
            response_schema=STAGE1_RESPONSE_SCHEMA,
            document_text=document_text,
            max_types=max_types,
        )
    elif pass_name == "bottom_up":
        seed_section = seed_reference_text or "(No seed reference available)"
        user_prompt = template.format(
            cq_corpus=cq_corpus,
            seed_section=seed_section,
            response_schema=STAGE1_RESPONSE_SCHEMA,
            document_text=document_text,
            max_types=max_types,
        )
    elif pass_name == "middle_out":
        seed_vocab_section = (
            (
                "Here are naming patterns from established ontologies — use these as vocabulary\n"
                "guidance. If a seed pattern has a standard name for something you found, prefer\n"
                "that name. But do NOT include a pattern just because it's in the seed:\n"
                + seed_reference_text
            )
            if seed_reference_text else ""
        )
        user_prompt = template.format(
            cq_corpus=cq_corpus,
            seed_vocab_section=seed_vocab_section,
            response_schema=STAGE1_RESPONSE_SCHEMA,
            document_text=document_text,
            max_types=max_types,
        )

    if other_note:
        user_prompt = other_note + user_prompt

    return system_prompt, user_prompt


def build_stage2_prompt(
    type_name: str,
    type_description: str,
    domain: str,
    document_text: str,
    cqs: list,
    seed_reference_text: str | None = None,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a Stage 2 detail call.

    Stage 2 details ONE entity type: properties, evidence, outgoing relationships.
    """
    cq_corpus = _format_cq_corpus(cqs)

    seed_note = ""
    if seed_reference_text:
        seed_note = f"\nSeed reference (for naming guidance):\n{seed_reference_text}\n"

    # Section order is load-bearing for prompt caching: DOCUMENTS + CQ corpus are
    # identical across every Stage-2 type-detail call, so they form the shared
    # prefix that Ollama prefills once and reuses; only the per-type instruction
    # at the tail varies. Do NOT move {document_text}/{cq_corpus} below the
    # type-specific lines (that reintroduces a full ~48k-token re-prefill per type).
    user_prompt = f"""DOCUMENTS:
{document_text}

Competency questions in scope:
{cq_corpus}

Detail the entity type "{type_name}" for the "{domain}" domain.

Description from Stage 1: {type_description}
{seed_note}
For this entity type, identify:
1. All properties (name, data_type, description, required, answerable_cqs)
2. Evidence documents (filenames where you found evidence)
3. Outgoing relationships from this type (name, target_type, description, richness_hint, edge_properties, answerable_cqs)

RESPONSE FORMAT:
{STAGE2_RESPONSE_SCHEMA}"""

    return STAGE2_SYSTEM_PROMPT, user_prompt


STAGE2_BATCH_RESPONSE_SCHEMA = json.dumps(
    {
        "types": [
            {
                "name": "Legal_Entity",
                "properties": [
                    {
                        "name": "jurisdiction",
                        "data_type": "string",
                        "description": "Legal jurisdiction of registration",
                        "required": True,
                        "answerable_cqs": ["cq_001"],
                    }
                ],
                "evidence_documents": ["articles_of_incorporation.pdf"],
                "relationships_from_this_type": [
                    {
                        "name": "registered_in",
                        "target_type": "Jurisdiction",
                        "description": "Where an entity is legally registered",
                        "richness_hint": "simple",
                        "edge_properties": [],
                        "answerable_cqs": ["cq_001"],
                    }
                ],
            }
        ]
    },
    indent=2,
)


def build_stage2_batch_prompt(
    type_specs: list[tuple[str, str]],
    domain: str,
    document_text: str,
    cqs: list,
    seed_reference_text: str | None = None,
) -> tuple[str, str]:
    """Return (system, user) detailing SEVERAL entity types in one LLM call.

    ``type_specs`` is a list of (name, description). Section order is the same
    cache-friendly shape as the single-type prompt: DOCUMENTS + CQ corpus form
    the shared prefix (reused across batches), the variable type list and
    response schema come last.
    """
    cq_corpus = _format_cq_corpus(cqs)

    seed_note = ""
    if seed_reference_text:
        seed_note = f"\nSeed reference (for naming guidance):\n{seed_reference_text}\n"

    type_list = "\n".join(f'- "{name}": {desc}' for name, desc in type_specs)

    user_prompt = f"""DOCUMENTS:
{document_text}

Competency questions in scope:
{cq_corpus}

Detail the following {len(type_specs)} entity types for the "{domain}" domain.
Return one object per type in the "types" array, each with the type's exact "name"
plus its properties, evidence_documents, and outgoing relationships_from_this_type.

TYPES TO DETAIL:
{type_list}
{seed_note}
For EACH type, identify:
1. All properties (name, data_type, description, required, answerable_cqs)
2. Evidence documents (filenames where you found evidence)
3. Outgoing relationships from this type (name, target_type, description, richness_hint, edge_properties, answerable_cqs)

RESPONSE FORMAT:
{STAGE2_BATCH_RESPONSE_SCHEMA}"""

    return STAGE2_SYSTEM_PROMPT, user_prompt


# --- Backward compatibility alias ---
# build_schema_pass_prompt delegates to build_stage1_prompt for callers that haven't updated
build_schema_pass_prompt = build_stage1_prompt
