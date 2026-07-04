"""PromptAssembler — deterministic system/context/query prompt assembly.

§5 of chunk-23-spec.md. D134: only context is truncated under budget
pressure. System and query are never truncated; if they alone exceed
the budget, PromptAssemblyError is raised.
"""

from __future__ import annotations

import structlog

from src.regeneration.regeneration_config import RegenSettings
from src.regeneration.regeneration_models import (
    AssembledPrompt,
    PhaseState,
    RegenerationQuery,
)
from src.retrieval.retrieval_models import RetrievalResponse

logger = structlog.get_logger()

# F-0048 / ISS-0039 (validation run, 2026-07-03) — compose-context
# supplement bounds. CQ-21: the composed context carried only an
# Insurance_Policy document's header info even though retrieval had the
# policy vertex + edges — the upstream ``serialized_context`` is built under
# the RETRIEVAL serializer's (smaller) token budget and can drop entity
# property values (and, for the "llm" serialization format, summarize them
# away) that ``results[*].properties`` still carry — including the D532/G-F5
# intent reasoning prose hydrated into intent-type results. The supplement
# re-serializes property values that are missing from the inherited context,
# bounded per value and per result, and is APPENDED so the existing D134
# tail-truncation drops the supplement before ever touching the base context.
_SUPPLEMENT_HEADER = "Additional entity detail:"
_SUPPLEMENT_MAX_PROP_VALUE_CHARS = 300
_SUPPLEMENT_MAX_PROPS_PER_RESULT = 20
# System-plane keys never serialized into LLM context (defense-in-depth —
# the retrieval pipeline already strips `_embedding` per F-0042/ISS-0037).
_SUPPLEMENT_SKIP_KEYS: frozenset[str] = frozenset(
    {"name", "grace_id", "_embedding", "_deprecated", "sensitivity_tags"}
)
# Prefix length for the already-present containment probe: the template
# serializer emits properties as `key=value`, so a prefix match on the pair
# detects upstream-serialized values without repeating boilerplate.
_SUPPLEMENT_DEDUP_PREFIX_CHARS = 80


class PromptAssemblyError(Exception):
    """System + query alone exceeded budget — operator config issue."""


class PromptAssembler:
    """Deterministic prompt assembly.

    Same inputs produce byte-identical outputs. Phase-state directive
    lookup is total (every PhaseState maps to a configured string).
    """

    def __init__(self, settings: RegenSettings) -> None:
        self.settings = settings

    def _directive_for(self, phase_state: PhaseState) -> str:
        mapping = {
            "prepare": self.settings.phase_style_prepare,
            "open": self.settings.phase_style_open,
            "structure": self.settings.phase_style_structure,
            "clarify": self.settings.phase_style_clarify,
            "close": self.settings.phase_style_close,
            "none": self.settings.phase_style_none,
        }
        return mapping[phase_state]

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // self.settings.chars_per_token

    @staticmethod
    def _build_context_supplement(
        retrieval_response: RetrievalResponse, base_context: str
    ) -> str:
        """Serialize result property values missing from the inherited context.

        F-0048 / ISS-0039: entity property values (and D532 intent reasoning
        prose, which lives in ``result.properties`` after retrieval-side
        hydration) present on the ranked results must reach the composed
        context — the upstream ``serialized_context`` may have truncated or
        summarized them away under the retrieval serializer's own budget.
        Deterministic (same inputs → same string); one line per result,
        mirroring the TemplateSerializer entity-line shape; values bounded to
        ``_SUPPLEMENT_MAX_PROP_VALUE_CHARS`` so an oversized property set is
        truncated, never crashing or blowing the budget. Property pairs whose
        ``key=value`` prefix already appears in the base context are skipped
        (completeness, not repetition).
        """
        lines: list[str] = []
        for result in retrieval_response.results:
            rendered: list[str] = []
            for key, value in (result.properties or {}).items():
                if value is None or value == "":
                    continue
                if key in _SUPPLEMENT_SKIP_KEYS or key.startswith("@"):
                    continue
                raw = str(value)
                if f"{key}={raw}"[:_SUPPLEMENT_DEDUP_PREFIX_CHARS] in base_context:
                    continue  # already serialized upstream
                if len(raw) > _SUPPLEMENT_MAX_PROP_VALUE_CHARS:
                    raw = raw[:_SUPPLEMENT_MAX_PROP_VALUE_CHARS].rstrip() + "…"
                rendered.append(f"{key}={raw}")
                if len(rendered) >= _SUPPLEMENT_MAX_PROPS_PER_RESULT:
                    break
            if rendered:
                lines.append(
                    f'Entity: {result.entity_type} "{result.name}"'
                    f" ({', '.join(rendered)})"
                )
        if not lines:
            return ""
        return _SUPPLEMENT_HEADER + "\n" + "\n".join(lines)

    def assemble(
        self,
        query: RegenerationQuery,
        retrieval_response: RetrievalResponse,
    ) -> AssembledPrompt:
        directive = self._directive_for(query.phase_state)
        system_prompt = self.settings.system_prompt_template.format(
            phase_style_directive=directive
        )
        user_query = query.query_text
        context = retrieval_response.serialized_context

        # F-0048 / ISS-0039: append the property-value supplement AFTER the
        # inherited serialization so D134 tail-truncation under budget
        # pressure drops supplement lines first and the base context is
        # never displaced. Capture-the-why (D356): invariant = D193
        # regeneration hard-lock; carve-out = this compose-context
        # supplement in prompt_assembly.py; authorization = F-0048 /
        # ISS-0039 (serializer content-completeness), allowlisted in
        # scripts/check-regeneration-unchanged.sh.
        supplement = self._build_context_supplement(retrieval_response, context)
        if supplement:
            context = f"{context}\n{supplement}" if context else supplement

        system_est = self._estimate_tokens(system_prompt)
        query_est = self._estimate_tokens(user_query)
        context_est_initial = self._estimate_tokens(context)

        total_budget = self.settings.total_input_budget_tokens

        # D134: system + query never truncated.
        if system_est + query_est > total_budget:
            overflow = system_est + query_est - total_budget
            raise PromptAssemblyError(
                "System + query alone exceed total_input_budget_tokens "
                f"({system_est + query_est} > {total_budget}, "
                f"overflow={overflow}). Increase budget or shorten "
                "system prompt template."
            )

        context_truncated = False
        truncation_details: str | None = None

        if system_est + context_est_initial + query_est > total_budget:
            allowed_context_tokens = total_budget - system_est - query_est
            allowed_chars = allowed_context_tokens * self.settings.chars_per_token
            if allowed_chars < 0:
                allowed_chars = 0

            original_tokens = context_est_initial
            if len(context) > allowed_chars:
                truncated = context[:allowed_chars]
                newline_pos = truncated.rfind("\n")
                if newline_pos > 0:
                    context = truncated[:newline_pos]
                else:
                    context = truncated
            context_truncated = True
            new_context_est = self._estimate_tokens(context)
            dropped_tokens = original_tokens - new_context_est
            truncation_details = (
                f"original_context_tokens={original_tokens} "
                f"budgeted_context_tokens={allowed_context_tokens} "
                f"dropped_tokens={dropped_tokens}"
            )
            logger.warning(
                "prompt_assembly.context_truncated",
                original_context_tokens=original_tokens,
                budgeted_context_tokens=allowed_context_tokens,
                dropped_tokens=dropped_tokens,
            )

        context_est = self._estimate_tokens(context)
        total_est = system_est + context_est + query_est

        return AssembledPrompt(
            system_prompt=system_prompt,
            context=context,
            user_query=user_query,
            system_token_estimate=system_est,
            context_token_estimate=context_est,
            query_token_estimate=query_est,
            total_token_estimate=total_est,
            context_truncated=context_truncated,
            truncation_details=truncation_details,
            phase_style_applied=directive,
        )
