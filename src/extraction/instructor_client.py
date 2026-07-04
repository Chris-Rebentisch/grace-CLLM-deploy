"""ExtractionLLMClient — thin Instructor wrapper for structured LLM output.

This is the single boundary between the Extraction module and LLM inference.
If Instructor is removed in the future, only this file changes.

Does NOT replace src/shared/llm_provider.py. That module handles Discovery
and other modules. This module handles Extraction structured output only.
"""

from typing import TypeVar

import instructor
import structlog

from src.extraction.extraction_config import ExtractionSettings
from src.shared.config import get_settings
from src.shared.llm_provider import read_llm_config_from_yaml

log = structlog.get_logger()

T = TypeVar("T")


class ExtractionLLMError(Exception):
    """Raised when an Instructor call fails after all retries."""

    def __init__(
        self,
        message: str,
        provider: str = "",
        model: str = "",
        retries_attempted: int = 0,
    ):
        self.provider = provider
        self.model = model
        self.retries_attempted = retries_attempted
        super().__init__(message)


def _normalize_ollama_openai_base_url(base_url: str) -> str:
    """Ensure OpenAI SDK base_url includes /v1 (Ollama OpenAI-compatible API)."""
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"


def _is_temperature_unsupported_error(exc: Exception) -> bool:
    """Detect a 400 that names temperature as unsupported/deprecated.

    F-0015(a) / ISS-0031: claude-sonnet-5 (and other newer Claude models)
    reject the temperature parameter with a 400 invalid_request_error whose
    message names the parameter ("temperature is deprecated for this model").
    Match conservatively: the message must mention temperature AND look like
    a client-side parameter rejection, so we never retry on unrelated errors.
    """
    msg = str(exc).lower()
    if "temperature" not in msg:
        return False
    return any(
        marker in msg
        for marker in (
            "400",
            "invalid_request",
            "bad request",
            "badrequest",
            "deprecated",
            "unsupported",
            "not supported",
        )
    )


def _build_provider_string(provider: str, model: str) -> str:
    """Construct Instructor provider string from provider name and model."""
    if provider == "ollama":
        return f"ollama/{model}"
    elif provider == "anthropic":
        return f"anthropic/{model}"
    elif provider == "openai":
        return f"openai/{model}"
    else:
        raise ExtractionLLMError(
            f"Unknown provider: '{provider}'. Must be one of: ollama, anthropic, openai",
            provider=provider,
            model=model,
        )


class ExtractionLLMClient:
    """Thin wrapper around Instructor for structured extraction calls.

    Responsibilities:
    - Provider/model resolution from config
    - Structured call invocation with response_model
    - Retry and timeout wiring
    - Normalized error surface

    NOT responsible for:
    - Prompt construction (Chunk 18: extraction_prompts.py)
    - Verdict mapping (Chunk 19: verification.py)
    - Chunk orchestration (Chunk 18: extraction_pipeline.py)
    """

    def __init__(self, config: ExtractionSettings):
        """Create Instructor clients for extraction and verification.

        Reads provider config from config/discovery.yaml when
        ExtractionSettings provider fields are None (fallback to global).
        """
        self._config = config
        global_config = read_llm_config_from_yaml()
        settings = get_settings()

        # Resolve extraction provider/model
        ext_provider = config.extraction_provider or global_config["provider"]
        ext_model = config.extraction_model or global_config["model"]
        self._extraction_provider = ext_provider
        self._extraction_model = ext_model

        # Resolve verification provider/model
        ver_provider = config.verification_provider or global_config["provider"]
        ver_model = config.verification_model or global_config["model"]
        self._verification_provider = ver_provider
        self._verification_model = ver_model

        # Build kwargs for provider-specific options
        ext_kwargs = self._build_kwargs(
            ext_provider, global_config, settings, config.extraction_base_url
        )
        ver_kwargs = self._build_kwargs(
            ver_provider, global_config, settings, config.verification_base_url
        )

        ext_provider_string = _build_provider_string(ext_provider, ext_model)
        ver_provider_string = _build_provider_string(ver_provider, ver_model)

        # F-0014 (validation run, 2026-07-03): in the default ANTHROPIC_TOOLS
        # mode, claude-haiku splits ExtractionResult across TWO PARALLEL tool_use
        # blocks ({entities:[...]} + {relationships:[...]}); instructor 1.15.1
        # validates a single block — the relationships block was silently
        # discarded (relationship recall collapsed to zero), and once the
        # relationships field became required, instructor's retry re-send broke
        # against the API (400: tool_use without tool_result). ANTHROPIC_JSON
        # mode (plain-JSON completion, parsed+validated) sidesteps parallel
        # tool-use entirely; the same prompts produced complete entity AND
        # relationship output in JSON mode. Applied to anthropic providers only.
        def _mode_kwargs(provider: str) -> dict:
            if provider == "anthropic":
                return {"mode": instructor.Mode.ANTHROPIC_JSON}
            return {}

        self._extraction_client = instructor.from_provider(
            ext_provider_string, async_client=True,
            **_mode_kwargs(ext_provider), **ext_kwargs
        )
        self._verification_client = instructor.from_provider(
            ver_provider_string, async_client=True,
            **_mode_kwargs(ver_provider), **ver_kwargs
        )

        # Resolve client for ER Tier 3
        res_provider = config.er_provider or ext_provider
        res_model = config.er_model or ext_model
        self._resolve_provider = res_provider
        self._resolve_model = res_model

        res_kwargs = self._build_kwargs(
            res_provider, global_config, settings,
            config.er_base_url or config.extraction_base_url,
        )
        res_provider_string = _build_provider_string(res_provider, res_model)
        self._resolve_client = instructor.from_provider(
            res_provider_string, async_client=True,
            **_mode_kwargs(res_provider), **res_kwargs
        )

        log.info(
            "extraction_llm_client_initialized",
            extraction_provider=ext_provider_string,
            verification_provider=ver_provider_string,
            resolve_provider=res_provider_string,
        )

    @property
    def extraction_provider(self) -> str:
        """Resolved extraction provider name: ollama, anthropic, or openai."""
        return self._extraction_provider

    @property
    def extraction_model(self) -> str:
        """Resolved extraction model identifier (e.g. qwen2.5:7b)."""
        return self._extraction_model

    @property
    def verification_provider(self) -> str:
        """Resolved verification provider name."""
        return self._verification_provider

    @property
    def verification_model(self) -> str:
        """Resolved verification model identifier."""
        return self._verification_model

    @staticmethod
    def _build_kwargs(
        provider: str,
        global_config: dict,
        settings,
        base_url_override: str | None = None,
    ) -> dict:
        """Build provider-specific kwargs for instructor.from_provider().

        Base URL precedence:
        1. ExtractionSettings override (base_url_override)
        2. Global YAML base_url fallback
        """
        kwargs: dict = {}
        if provider == "ollama":
            raw_base = base_url_override or global_config.get(
                "base_url", "http://localhost:11434"
            )
            kwargs["base_url"] = _normalize_ollama_openai_base_url(raw_base)
        elif provider == "anthropic":
            kwargs["api_key"] = settings.llm_api_key
        elif provider == "openai":
            global_base_url = global_config.get("base_url", "")
            base_url = base_url_override or global_base_url
            if not base_url_override and ":11434" in global_base_url:
                log.warning(
                    "extraction_provider overridden to 'openai' but extraction_base_url not set; "
                    "falling back to global base_url which appears to be Ollama",
                    global_base_url=global_base_url,
                    provider=provider,
                )
            kwargs["base_url"] = base_url
            api_key = settings.llm_api_key
            if not api_key:
                from src.shared.llm_provider import _is_private_network_url

                if _is_private_network_url(base_url):
                    api_key = "no-key"
            kwargs["api_key"] = api_key
        return kwargs

    async def _create_with_temperature_fallback(
        self, instructor_client, call_kwargs: dict, *, provider: str, model: str
    ):
        """Invoke create(); retry ONCE without temperature on a 400 naming it.

        F-0015(a) / ISS-0031: some newer models (claude-sonnet-5) reject
        temperature with a 400 "temperature is deprecated for this model".
        When the first attempt fails that way and temperature was sent,
        strip it and retry exactly once (bounded — a second failure
        propagates), logging a warning so operators can set
        EXTRACTION_TEMPERATURE to None permanently.
        """
        try:
            return await instructor_client.chat.completions.create(**call_kwargs)
        except Exception as e:
            if "temperature" not in call_kwargs or not _is_temperature_unsupported_error(e):
                raise
            log.warning(
                "temperature_rejected_retrying_without",
                provider=provider,
                model=model,
                error=str(e),
                hint="Model rejects the temperature parameter; retried once "
                     "without it. Set the temperature setting to None to stop "
                     "sending it.",
            )
            retry_kwargs = {k: v for k, v in call_kwargs.items() if k != "temperature"}
            return await instructor_client.chat.completions.create(**retry_kwargs)

    async def extract(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        """Call the extraction model with structured output.

        Uses Instructor create() with response_model, max_retries,
        and timeout from config. Returns a validated Pydantic instance.

        Raises ExtractionLLMError on all failures (timeout, validation
        exhausted, provider unreachable).
        """
        from src.analytics.llm_instrumentation import record_llm_call

        async with record_llm_call(
            system=self._extraction_provider,
            model=self._extraction_model,
            grace_module="extraction",
            grace_operation="extract",
        ):
            try:
                call_kwargs: dict = {
                    "response_model": response_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_retries": self._config.max_retries,
                    # F-0015(c) / ISS-0031: provider-aware ceiling (ollama 4096,
                    # anthropic/openai 8192) unless explicitly configured.
                    "max_tokens": self._config.effective_max_output_tokens(
                        self._extraction_provider
                    ),
                    "timeout": self._config.extraction_timeout,
                }
                # F-0015(a) / ISS-0031: omit temperature when None — newer
                # Claude models 400 on the parameter.
                if self._config.temperature is not None:
                    call_kwargs["temperature"] = self._config.temperature
                result = await self._create_with_temperature_fallback(
                    self._extraction_client,
                    call_kwargs,
                    provider=self._extraction_provider,
                    model=self._extraction_model,
                )
                return result
            except Exception as e:
                raise ExtractionLLMError(
                    f"Extraction call failed: {e}",
                    provider=self._extraction_provider,
                    model=self._extraction_model,
                    retries_attempted=self._config.max_retries,
                ) from e

    async def verify(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        """Call the verification model with structured output.

        Same interface as extract(), but uses verification_provider
        and verification_model from config. Can be a different model
        and/or different provider.
        """
        from src.analytics.llm_instrumentation import record_llm_call

        async with record_llm_call(
            system=self._verification_provider,
            model=self._verification_model,
            grace_module="extraction",
            grace_operation="verify",
        ):
            try:
                call_kwargs: dict = {
                    "response_model": response_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_retries": self._config.max_retries,
                    "max_tokens": self._config.verification_max_output_tokens,
                    "timeout": self._config.verification_timeout,
                }
                # F-0015(a) / ISS-0031: omit temperature when None.
                if self._config.verification_temperature is not None:
                    call_kwargs["temperature"] = self._config.verification_temperature
                result = await self._create_with_temperature_fallback(
                    self._verification_client,
                    call_kwargs,
                    provider=self._verification_provider,
                    model=self._verification_model,
                )
                return result
            except Exception as e:
                raise ExtractionLLMError(
                    f"Verification call failed: {e}",
                    provider=self._verification_provider,
                    model=self._verification_model,
                    retries_attempted=self._config.max_retries,
                ) from e

    async def resolve(
        self,
        response_model: type[T],
        messages: list[dict],
    ) -> T:
        """Tier 3 entity resolution disambiguation.

        Uses _resolve_client and ER-specific inference settings only.
        """
        from src.analytics.llm_instrumentation import record_llm_call

        async with record_llm_call(
            system=self._resolve_provider,
            model=self._resolve_model,
            grace_module="extraction",
            grace_operation="resolve",
        ):
            try:
                call_kwargs: dict = {
                    "response_model": response_model,
                    "messages": messages,
                    "max_retries": self._config.max_retries,
                    "max_tokens": self._config.er_max_output_tokens,
                    "timeout": self._config.er_timeout,
                }
                # F-0015(a) / ISS-0031: omit temperature when None.
                if self._config.er_temperature is not None:
                    call_kwargs["temperature"] = self._config.er_temperature
                result = await self._create_with_temperature_fallback(
                    self._resolve_client,
                    call_kwargs,
                    provider=self._resolve_provider,
                    model=self._resolve_model,
                )
                return result
            except Exception as e:
                raise ExtractionLLMError(
                    f"Resolution call failed: {e}",
                    provider=self._resolve_provider,
                    model=self._resolve_model,
                    retries_attempted=self._config.max_retries,
                ) from e
