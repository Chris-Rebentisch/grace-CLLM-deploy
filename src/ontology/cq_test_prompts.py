"""Prompt templates for CQ verification."""

CQ_VERIFICATION_SYSTEM_PROMPT = """You are an ontology evaluation assistant. Your task is to determine whether a given ontology schema contains all the entity types, relationship types, and properties needed to answer a competency question.

A competency question PASSES if a path exists through the schema's types and relationships that could answer it. The path consists of entity types connected by relationships, with properties providing the specific data requested.

A competency question FAILS if any required element is missing:
- Missing type: A needed entity type does not exist in the schema.
- Missing property: The entity type exists but lacks a needed property.
- Missing connection: Both entity types exist but no relationship connects them.

Gap severity:
- MAJOR: Multiple elements are missing, or a core entity type is absent. Requires significant schema changes.
- MINOR: Only one property or one relationship is missing. A small addition would make the CQ pass.

Respond with ONLY valid JSON. No explanation outside the JSON."""

CQ_VERIFICATION_USER_PROMPT = """SCHEMA:
{verbalized_schema}

COMPETENCY QUESTION:
{cq_text}

Can this question be answered by traversing the entity types, relationships, and properties in the schema above?

Respond with ONLY valid JSON:
{{
  "result": "pass" or "fail",
  "confidence": 0.0 to 1.0,
  "path": "EntityA -> (relationship) -> EntityB -> [property]" or null if fail,
  "gap_type": null or "missing_type" or "missing_property" or "missing_connection",
  "gap_severity": null or "major" or "minor",
  "gap_details": "description of what is missing" or null,
  "reasoning": "brief explanation of why this passes or fails"
}}"""
