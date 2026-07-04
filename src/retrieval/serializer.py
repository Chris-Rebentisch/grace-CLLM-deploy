"""Subgraph serialization — template prose, Turtle/RDF, and LLM summary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import rdflib
import structlog
from rdflib import Literal, Namespace, RDF

from src.retrieval.retrieval_models import RankedResult

if TYPE_CHECKING:
    from src.retrieval.retrieval_config import RetrievalConfig

logger = structlog.get_logger()

# Rough token estimate: ~4 chars per token
_CHARS_PER_TOKEN = 4


class SubgraphSerializer(ABC):
    """Common interface for all serialization formats."""

    @abstractmethod
    def serialize(
        self,
        results: list[RankedResult],
        relationships: list[dict],
        token_budget: int = 2000,
    ) -> str:
        """Serialize ranked results + relationships into LLM-consumable text."""

    async def serialize_async(
        self,
        results: list[RankedResult],
        relationships: list[dict],
        token_budget: int = 2000,
    ) -> str:
        """Async serialization. Default wraps sync serialize()."""
        return self.serialize(results, relationships, token_budget=token_budget)


class TemplateSerializer(SubgraphSerializer):
    """Template-based prose serialization.

    Example output:
    Entity: Legal_Entity "Acme Capital" (jurisdiction=BVI, registered=2015)
    Relationship: Acme Capital --[owns]--> Cedar Cay (since=2018, confidence=0.92)
    """

    def serialize(
        self,
        results: list[RankedResult],
        relationships: list[dict],
        token_budget: int = 2000,
    ) -> str:
        """Serialize results and relationships as template prose."""
        char_budget = token_budget * _CHARS_PER_TOKEN
        parts: list[str] = []
        current_chars = 0

        for r in results:
            props = _format_props(r.properties)
            line = f'Entity: {r.entity_type} "{r.name}"'
            if props:
                line += f" ({props})"
            if current_chars + len(line) + 1 > char_budget:
                break
            parts.append(line)
            current_chars += len(line) + 1

        for rel in relationships:
            source = rel.get("source_name", rel.get("source_grace_id", "?"))
            target = rel.get("target_name", rel.get("target_grace_id", "?"))
            rel_type = rel.get("relationship_type", rel.get("type", "related_to"))
            rel_props = _format_props(
                {k: v for k, v in rel.items()
                 if k not in ("source_grace_id", "target_grace_id", "source_name",
                              "target_name", "relationship_type", "type",
                              "@rid", "@type", "@cat", "@in", "@out")}
            )
            line = f"{source} --[{rel_type}]--> {target}"
            if rel_props:
                line += f" ({rel_props})"
            if current_chars + len(line) + 1 > char_budget:
                break
            parts.append(line)
            current_chars += len(line) + 1

        return "\n".join(parts)


class TurtleSerializer(SubgraphSerializer):
    """RDF Turtle serialization via rdflib."""

    def serialize(
        self,
        results: list[RankedResult],
        relationships: list[dict],
        token_budget: int = 2000,
    ) -> str:
        """Serialize results and relationships as Turtle/RDF."""
        g = rdflib.Graph()
        grace_ns = Namespace("http://grace.local/entity/")
        rel_ns = Namespace("http://grace.local/rel/")
        g.bind("grace", grace_ns)
        g.bind("rel", rel_ns)

        char_budget = token_budget * _CHARS_PER_TOKEN

        for r in results:
            subj = grace_ns[_safe_uri(r.grace_id)]
            g.add((subj, RDF.type, grace_ns[_safe_uri(r.entity_type)]))
            g.add((subj, grace_ns["name"], Literal(r.name)))

            for k, v in r.properties.items():
                if k in ("name", "grace_id", "@rid", "@type") or v is None:
                    continue
                g.add((subj, grace_ns[k], Literal(str(v))))

        for rel in relationships:
            src_id = rel.get("source_grace_id", "")
            tgt_id = rel.get("target_grace_id", "")
            rel_type = rel.get("relationship_type", rel.get("type", "related_to"))
            if src_id and tgt_id:
                g.add((
                    grace_ns[_safe_uri(src_id)],
                    rel_ns[_safe_uri(rel_type)],
                    grace_ns[_safe_uri(tgt_id)],
                ))

        turtle = g.serialize(format="turtle")
        # Enforce token budget
        if len(turtle) > char_budget:
            turtle = turtle[:char_budget]

        return turtle


class LLMSerializer(SubgraphSerializer):
    """LLM-generated natural language summary from subgraph data.

    Uses the shared LLM provider abstraction (get_provider) for inference.
    Async-only: serialize() raises NotImplementedError; use serialize_async().
    Falls back to TemplateSerializer output on LLM failure.
    """

    def __init__(self, retrieval_config: "RetrievalConfig | None" = None):
        self._config = retrieval_config
        self._template_fallback = TemplateSerializer()

    def serialize(
        self,
        results: list[RankedResult],
        relationships: list[dict],
        token_budget: int = 2000,
    ) -> str:
        """Sync serialize is not supported — use serialize_async()."""
        raise NotImplementedError(
            "LLMSerializer is async-only. Use serialize_async() instead."
        )

    async def serialize_async(
        self,
        results: list[RankedResult],
        relationships: list[dict],
        token_budget: int = 2000,
    ) -> str:
        """Produce a natural language summary of the subgraph via LLM.

        Token budget split: 60% input context, 40% output cap.
        Falls back to template-serialized string on LLM failure.
        """
        input_budget = int(token_budget * 0.6)
        output_token_budget = max(1, int(token_budget * 0.4))

        # Build structured input via template serializer
        structured_input = self._template_fallback.serialize(
            results, relationships, token_budget=input_budget
        )

        if not structured_input.strip():
            return ""

        system_prompt = (
            "You are a knowledge graph summarizer. Given structured "
            "entity and relationship data, write a concise factual "
            "summary in natural language. Preserve all entity names, "
            "types, and key facts. Do not add information not present "
            "in the input."
        )
        user_prompt = (
            f"Summarize the following knowledge graph data in natural "
            f"language. Stay within {output_token_budget} tokens.\n\n"
            f"{structured_input}"
        )

        try:
            from src.shared.llm_provider import get_llm_config, get_provider

            # Full provider config (provider, api_key, timeout) from YAML + .env
            config_override = dict(get_llm_config())
            if self._config:
                config_override["model"] = self._config.serialization_model
                if config_override.get("provider") == "ollama":
                    config_override["base_url"] = (
                        self._config.ollama_base_url
                        or config_override.get("base_url")
                        or "http://localhost:11434"
                    )

            provider = get_provider(config_override=config_override)
            response = await provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0,
                max_tokens=max(64, output_token_budget),
                json_mode=False,
            )
            return response.text.strip()
        except Exception:
            logger.warning("llm_serializer.failed", exc_info=True)
            return structured_input


def get_serializer(
    format_name: str,
    *,
    config: "RetrievalConfig | None" = None,
) -> SubgraphSerializer:
    """Factory for serializer instances.

    For 'llm' format, pass config to provide model and base URL.
    When config is None, LLMSerializer falls back to YAML defaults.
    """
    if format_name == "template":
        return TemplateSerializer()
    elif format_name == "turtle":
        return TurtleSerializer()
    elif format_name == "llm":
        return LLMSerializer(retrieval_config=config)
    else:
        raise ValueError(f"Unknown serialization format: {format_name}")


def _format_props(props: dict[str, Any]) -> str:
    """Format properties as key=value pairs."""
    parts = [
        f"{k}={v}" for k, v in props.items()
        if v is not None and k not in ("name", "grace_id")
    ]
    return ", ".join(parts)


def _safe_uri(value: str) -> str:
    """Make a string safe for use as a URI fragment."""
    return value.replace(" ", "_").replace("/", "_").replace(":", "_")
