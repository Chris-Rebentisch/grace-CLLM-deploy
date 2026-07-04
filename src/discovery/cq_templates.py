"""CLaRO-inspired template engine for guided CQ authoring."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from src.discovery.cq_models import CQType

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "cq_templates.yaml"


class CQTemplate(BaseModel):
    """A template for guided CQ authoring."""

    id: str = Field(description="Unique template identifier, e.g., 'scoping_what_types'")
    cq_type: CQType = Field(description="Which Keet CQ type this template produces")
    pattern: str = Field(
        description="Template pattern with {placeholders}, e.g., 'What types of {entity} does the organization have?'"
    )
    placeholders: list[str] = Field(
        description="List of placeholder names the user fills in"
    )
    example: str = Field(description="A concrete example of this template filled in")
    hint: str = Field(
        description="Plain-language guidance for the user on when to use this template"
    )
    domain_affinity: list[str] = Field(
        default_factory=list,
        description="Domains where this template is most useful. Empty = universal.",
    )


@lru_cache
def load_templates() -> tuple[CQTemplate, ...]:
    """Load and cache CQ templates from config/cq_templates.yaml.

    Returns a tuple (for lru_cache hashability). Use list() if you need a list.
    """
    with open(_CONFIG_PATH) as f:
        data = yaml.safe_load(f)
    templates = [CQTemplate(**t) for t in data["templates"]]
    return tuple(templates)


def get_templates_for_domain(domain: str) -> list[CQTemplate]:
    """Return templates with affinity for the given domain, plus universal templates."""
    return [
        t for t in load_templates()
        if not t.domain_affinity or domain in t.domain_affinity
    ]


def get_templates_by_type(cq_type: CQType) -> list[CQTemplate]:
    """Return templates that produce the given CQ type."""
    return [t for t in load_templates() if t.cq_type == cq_type]


def render_template(template_id: str, values: dict[str, str]) -> str:
    """Fill in a template's placeholders with user-provided values. Returns the CQ text."""
    templates = {t.id: t for t in load_templates()}
    if template_id not in templates:
        raise ValueError(f"Template '{template_id}' not found")

    template = templates[template_id]
    missing = [p for p in template.placeholders if p not in values]
    if missing:
        raise ValueError(f"Missing placeholders: {missing}")

    result = template.pattern
    for key, val in values.items():
        result = result.replace(f"{{{key}}}", val)
    return result


def suggest_templates(raw_input: str) -> list[CQTemplate]:
    """Given raw user brainstorm text, suggest the most relevant templates.

    Uses keyword matching against template patterns, hints, and examples.
    """
    words = set(raw_input.lower().split())
    scored: list[tuple[int, CQTemplate]] = []

    for template in load_templates():
        search_text = f"{template.pattern} {template.hint} {template.example}".lower()
        search_words = set(search_text.split())
        overlap = len(words & search_words)
        if overlap > 0:
            scored.append((overlap, template))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:5]]
