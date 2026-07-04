"""ResponseSynthesizer — thin wrapper around get_provider().

§6 of chunk-23-spec.md. D132: json_mode=False mandatory. D137: the
overrides.regeneration_model field is accepted at request time but not
dispatched end-to-end in v1; the pipeline sets
response_metadata.model_override_applied=False accordingly.
"""

from __future__ import annotations

import structlog

from src.regeneration.regeneration_config import RegenSettings
from src.regeneration.regeneration_models import (
    AssembledPrompt,
    RegenOverrides,
)
from src.shared.llm_provider import LLMResponse, get_provider

logger = structlog.get_logger()


class ResponseSynthesizer:
    """Dispatches the assembled prompt to the configured LLM provider."""

    def __init__(self, settings: RegenSettings) -> None:
        self.settings = settings

    async def synthesize(
        self,
        assembled: AssembledPrompt,
        overrides: RegenOverrides | None = None,
    ) -> LLMResponse:
        temperature = self.settings.regeneration_temperature
        max_tokens = self.settings.response_budget_tokens
        if overrides is not None:
            if overrides.temperature is not None:
                temperature = overrides.temperature
            if overrides.response_max_tokens is not None:
                max_tokens = overrides.response_max_tokens
            if overrides.regeneration_model is not None:
                # D137: accepted and logged, not dispatched in v1.
                logger.info(
                    "regeneration.synthesize.model_override_requested",
                    requested_model=overrides.regeneration_model,
                    dispatched=False,
                )

        provider = get_provider()
        user_prompt = f"{assembled.context}\n\n{assembled.user_query}"
        return await provider.generate(
            system_prompt=assembled.system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )
