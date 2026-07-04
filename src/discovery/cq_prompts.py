"""Prompt templates for three-pass CQ generation pipeline."""

SYSTEM_PROMPT = """You are an ontology engineer analyzing organizational documents to generate
competency questions (CQs). A competency question is a natural language question that a
knowledge system must be able to answer about this organization.

Rules:
- Generate questions, not statements
- Each question must be answerable from the type of information in these documents
- Be specific — use entity names, dates, and domain terms from the documents
- Do not generate duplicate or near-duplicate questions
- Output valid JSON only — no markdown, no explanation, no preamble. Do NOT wrap your response in markdown code fences (no ```json blocks)

Output format: a JSON array of objects, each with these fields:
{
  "question": "The competency question text",
  "cq_type": "SCOPING|VALIDATING|FOUNDATIONAL|RELATIONSHIP|METAPROPERTY",
  "rationale": "Brief explanation of why this question matters (1 sentence)",
  "source_document_names": ["filename1.pdf", "filename2.docx"],
  "priority": "HIGH|MEDIUM|LOW"
}"""


TOP_DOWN_PROMPT = """You are analyzing documents from the "{domain}" area of this organization.

CONTEXT:
{context_digest}

KEY TERMS: {key_terms}

DOCUMENTS:
{document_text}

TASK: Generate 15-25 STRATEGIC competency questions about this domain.
Think like a board member, CEO, or senior executive reviewing this area.
What high-level questions would they need answered?

Focus on:
- Organizational structure and control
- Risk and compliance
- Cross-domain connections
- Strategic obligations and deadlines

Generate questions that require understanding relationships between entities,
not just retrieving single facts.

Output ONLY the JSON array."""


BOTTOM_UP_PROMPT = """You are analyzing documents from the "{domain}" area of this organization.

CONTEXT:
{context_digest}

KEY TERMS: {key_terms}

DOCUMENTS:
{document_text}

TASK: Generate 15-25 SPECIFIC FACTUAL competency questions that could be answered
directly from the information in these documents.

Think like a researcher extracting every answerable fact from these documents.
Be specific — use actual names, dates, amounts, and identifiers from the text.

Focus on:
- Specific entities mentioned (people, companies, properties, policies)
- Dates, deadlines, and temporal relationships
- Amounts, values, and numeric data
- Document-specific details that would be lost without extraction

For each question, cite the specific document(s) it comes from in source_document_names.

Output ONLY the JSON array."""


NEGATIVE_EVIDENCE_PROMPT = """You just analyzed documents from the "{domain}" area.

The documents covered these topics: {key_terms}

TASK: Generate 5-10 questions that this organization SHOULD be able to answer
about {domain}, but the documents provided do NOT contain enough information to answer.

These are COVERAGE GAPS — important questions where the knowledge system would
need additional documents or data sources to provide answers.

Think about:
- What is conspicuously absent from these documents?
- What related information would a complete picture require?
- What questions would someone ask that these documents cannot answer?

Output ONLY the JSON array (same format as before, but set priority to "HIGH"
for all gap questions)."""


MIDDLE_OUT_PROMPT = """You are analyzing documents from the "{domain}" area of this organization.

CONTEXT:
{context_digest}

KEY TERMS: {key_terms}

DOCUMENTS:
{document_text}

TASK: Generate 15-25 PRACTICAL OPERATIONAL competency questions about this domain.
Think like someone who works at this organization every day and needs to query
a knowledge system to do their job.

Focus on:
- Day-to-day operational questions ("which vendors are working on X?")
- Status and progress tracking ("what contracts expire in the next 90 days?")
- Cross-reference needs ("show me everything related to project X")
- Workflow questions ("what approvals are needed for Y?")

Use the natural vocabulary from the documents — the terms people actually use,
not formal abstractions.

Output ONLY the JSON array."""


_TEMPLATES = {
    "top_down": TOP_DOWN_PROMPT,
    "bottom_up": BOTTOM_UP_PROMPT,
    "negative_evidence": NEGATIVE_EVIDENCE_PROMPT,
    "middle_out": MIDDLE_OUT_PROMPT,
}


def build_pass_prompt(
    pass_name: str,
    domain: str,
    context_digest: str,
    key_terms: list[str],
    document_text: str,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the given pass.

    Fills template placeholders with domain-specific content.
    """
    if pass_name not in _TEMPLATES:
        raise ValueError(f"Unknown pass: {pass_name}. Must be one of: {list(_TEMPLATES.keys())}")

    template = _TEMPLATES[pass_name]
    key_terms_str = ", ".join(key_terms) if key_terms else "none identified"

    user_prompt = template.format(
        domain=domain,
        context_digest=context_digest,
        key_terms=key_terms_str,
        document_text=document_text,
    )

    return SYSTEM_PROMPT, user_prompt


# --- Combined single-call generation (A3, validated 2026-06-08) ---
#
# One multi-perspective call replaces the 4 sequential passes. On the benchmark fixture
# this matched/beat the 4-pass baseline on distinct themes (20 vs 19) at ~8x less decode
# time, with far higher schema-shaping share (~85% vs ~60%), lower redundancy (1.5 vs 4.0),
# and ~half the verbosity. The two levers that drove the win (measured) were few-shot
# exemplars + a generality/conciseness instruction; the rationale-before-question field
# order was neutral but is retained as part of the validated config. See
# docs/cq-generation-research-and-test-plan.md and docs/cq-generation-benchmark-25docs.md.
# Evidence: Rebboud et al. ESWC 2024 (few-shot lift), AskCQ arXiv:2507.02989 (verbosity),
# Tam et al. EMNLP 2024 (field order), map-reduce per-document batching (Lu et al. 2025).

COMBINED_SYSTEM_PROMPT = """You are an ontology engineer analyzing documents to generate competency questions (CQs). A CQ is a natural-language question a knowledge system must be able to answer.

Rules:
- Generate questions, not statements
- Each question must be answerable from the type of information in these documents
- Prefer GENERAL, reusable questions that define entity types, relationships, and attributes spanning many documents — NOT facts about one named party
- Keep each question concise (under ~20 words)
- Do not generate duplicate or near-duplicate questions
- Output valid JSON only — no markdown, no code fences, no preamble

Output format: a JSON array of objects. State the rationale BEFORE the question (think, then ask):
{
  "rationale": "Which ontology element (entity type, relationship, or attribute) this question defines",
  "cq_type": "FOUNDATIONAL|SCOPING|RELATIONSHIP|METAPROPERTY|VALIDATING",
  "question": "The competency question text"
}"""

COMBINED_FEWSHOT = """EXAMPLES of the GENERAL, reusable style wanted (define types/relationships/attributes that span many documents):
- "What categories of party enter into an agreement, and in what role?" (FOUNDATIONAL)
- "Which party grants a license to which party, and on what terms?" (RELATIONSHIP)
- "What is the effective date and term of an agreement?" (METAPROPERTY)"""

COMBINED_PROMPT = """You are analyzing documents from the "{domain}" area of this organization.

CONTEXT:
{context_digest}

KEY TERMS: {key_terms}

DOCUMENTS:
{document_text}

TASK: Generate up to {target} competency questions covering ALL of these angles in a single combined set:
  1. STRATEGIC (top-down): high-level questions an executive needs about structure, risk, obligations.
  2. FACTUAL (bottom-up): concrete questions answerable from specific clauses.
  3. COVERAGE GAPS: questions about information that SHOULD exist but may be missing.
  4. RELATIONAL: questions about relationships/connections between entities.

{fewshot}

Output ONLY the JSON array of question objects."""


def build_combined_prompt(
    domain: str,
    context_digest: str,
    key_terms: list[str],
    document_text: str,
    target: int = 30,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the single-call combined generation (A3)."""
    key_terms_str = ", ".join(key_terms) if key_terms else "none identified"
    user_prompt = COMBINED_PROMPT.format(
        domain=domain,
        context_digest=context_digest,
        key_terms=key_terms_str,
        document_text=document_text,
        target=target,
        fewshot=COMBINED_FEWSHOT,
    )
    return COMBINED_SYSTEM_PROMPT, user_prompt
