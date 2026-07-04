"""Pydantic v2 TriageConfig models + YAML loader (Chunk 56, D429/D431).

Single source of truth for triage YAML schema. All triage modules consume
typed ``TriageConfig`` instances; no module re-parses the YAML independently.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


# Known Tier 1 rule names (D429 precedence set).
_KNOWN_TIER1_RULES: frozenset[str] = frozenset({
    "duplicate_message_id",
    "auto_reply",
    "newsletter",
    "calendar_invite",
    "bounce",
    "system_notification",
    "empty_body",
})


class Tier1RuleConfig(BaseModel):
    """Per-rule toggle. Disabled rules are skipped in first-match-wins evaluation."""

    enabled: bool = Field(default=True, description="Whether this rule is active.")


class EmptyBodyConfig(Tier1RuleConfig):
    """Tier 1 empty-body rule with configurable minimum character threshold."""

    min_chars_after_html_strip: int = Field(
        default=20,
        description="Minimum non-tag characters after HTML stripping.",
    )


class SystemNotificationConfig(Tier1RuleConfig):
    """Tier 1 system-notification rule with configurable sender patterns."""

    patterns: list[str] = Field(
        default_factory=list,
        description="Sender substrings that flag system notifications.",
    )


class Tier1Config(BaseModel):
    """Tier 1 noise filter configuration (D429)."""

    rule_order: list[str] = Field(
        description="Explicit first-match-wins precedence list of rule names.",
    )
    duplicate_message_id: Tier1RuleConfig = Field(default_factory=Tier1RuleConfig)
    auto_reply: Tier1RuleConfig = Field(default_factory=Tier1RuleConfig)
    newsletter: Tier1RuleConfig = Field(default_factory=Tier1RuleConfig)
    calendar_invite: Tier1RuleConfig = Field(default_factory=Tier1RuleConfig)
    bounce: Tier1RuleConfig = Field(default_factory=Tier1RuleConfig)
    system_notification: SystemNotificationConfig = Field(
        default_factory=SystemNotificationConfig,
    )
    empty_body: EmptyBodyConfig = Field(default_factory=EmptyBodyConfig)

    @model_validator(mode="after")
    def _validate_rule_order(self) -> Tier1Config:
        unknown = set(self.rule_order) - _KNOWN_TIER1_RULES
        if unknown:
            raise ValueError(f"Unknown Tier 1 rule names in rule_order: {sorted(unknown)}")
        return self


class Tier2Config(BaseModel):
    """Tier 2 entity lookup configuration (D430; D540 — configurable entity types).

    D540 capture-the-why: Tier 2 hardcoded `Person`/`Organization` vertex labels,
    but a deployment's ontology may have neither (the organization legal ontology
    uses `Legal_Entity`, no Person). With the wrong labels, T2 filtered EVERY email
    as `filtered_t2_no_known_entity` and nothing reached extraction. `entity_types`
    makes the matched labels operator-configurable; the default is a SUPERSET of
    the shipped D430 pair (adds `Legal_Entity`) so legal-ontology deployments work
    out of the box while Person/Organization deployments are unaffected — a label
    absent from the active graph is treated as no-match, never an error.
    """

    entity_types: list[str] = Field(
        default_factory=lambda: ["Person", "Organization", "Legal_Entity"],
        description="Graph vertex labels Tier 2 matches the sender name/alias against. "
        "Set to your ontology's sender types when the defaults don't cover them.",
    )


class Tier3Config(BaseModel):
    """Tier 3 ontology-similarity filter configuration (D431)."""

    threshold: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Cosine similarity floor; events below this are filtered.",
    )
    batch_size: int = Field(
        default=100,
        description="Number of events per embedding batch.",
    )
    flush_interval_seconds: int = Field(
        default=30,
        description="Maximum seconds before flushing a partial batch.",
    )


class Tier4Config(BaseModel):
    """Tier 4 LLM binary relevance filter configuration (Chunk 57, OQ-1)."""

    few_shot_fixture_dir: str = Field(
        default="src/ingestion/communications/triage/tier4_fixtures",
        description="Path to directory containing few-shot fixture JSON files.",
    )
    cost_budget_usd_per_run: float = Field(
        default=1.0,
        ge=0.0,
        description="Warn-only cost budget in USD per pipeline run.",
    )
    batch_size: int = Field(
        default=50,
        description="Number of events per LLM batch.",
    )


class TriageConfig(BaseModel):
    """Top-level triage pipeline configuration (D429/D431/D434)."""

    tier1: Tier1Config
    tier2: Tier2Config = Field(default_factory=Tier2Config)
    tier3: Tier3Config = Field(default_factory=Tier3Config)
    tier4: Tier4Config = Field(default_factory=Tier4Config)


def load_triage_config(path: Path) -> TriageConfig:
    """Read YAML and validate via ``TriageConfig.model_validate()``."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return TriageConfig.model_validate(raw)
