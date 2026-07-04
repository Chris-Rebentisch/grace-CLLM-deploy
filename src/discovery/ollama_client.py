"""Thin async Ollama HTTP client with retry, timeout, and JSON parsing."""

import json

import httpx
import structlog
from pydantic import BaseModel, Field

from src.shared.config import get_settings

logger = structlog.get_logger()


class OllamaConfig(BaseModel):
    """Configuration for Ollama API calls."""

    base_url: str = Field(default="http://localhost:11434", description="Ollama API base URL")
    model: str = Field(default="qwen2.5:7b", description="Model name")
    temperature: float = Field(default=0.0, description="Sampling temperature (0.0 = deterministic)")
    timeout_seconds: int = Field(default=300, description="Request timeout in seconds")
    max_retries: int = Field(default=2, description="Max retry attempts on failure")
    num_ctx: int = Field(
        default=8192,
        description="Ollama context window (tokens). Without this, Ollama defaults to "
        "~4096 and silently truncates long prompts — capping CQ/schema input regardless "
        "of max_document_chars_per_batch. Raise for large-context models (gpt-oss:120b).",
    )


class OllamaResponse(BaseModel):
    """Parsed response from Ollama /api/chat."""

    raw_text: str = Field(description="Raw text output from the model")
    parsed_json: dict | list | None = Field(default=None, description="Parsed JSON, or None if parsing failed")
    model: str = Field(default="", description="Model that generated the response")
    total_duration_ms: int = Field(default=0, description="Total duration in milliseconds")
    prompt_eval_count: int = Field(default=0, description="Tokens in prompt")
    eval_count: int = Field(default=0, description="Tokens generated")


def get_default_config() -> OllamaConfig:
    """Build OllamaConfig from GraceSettings."""
    settings = get_settings()
    return OllamaConfig(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout,
    )


def _parse_json_robust(raw_text: str) -> dict | list | None:
    """Parse JSON from LLM output with recovery strategies.

    1. Try json.loads directly
    2. Strip markdown code fences
    3. Extract first [ ... ] or { ... } substring
    4. Try JSONL (one object per line)
    """
    text = raw_text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: strip markdown fences (extract between first and last ```)
    if "```" in text:
        first_fence = text.find("```")
        last_fence = text.rfind("```")
        if first_fence != last_fence:
            inner = text[first_fence:last_fence]
            # Remove the opening fence line (```json or ```)
            first_newline = inner.find("\n")
            if first_newline != -1:
                inner = inner[first_newline + 1:]
            stripped = inner.strip()
            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                pass

    # Strategy 3: extract first [...] or {...}
    for open_ch, close_ch in [("[", "]"), ("{", "}")]:
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                pass

    # Strategy 4: JSONL
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    objects = []
    for line in lines:
        try:
            objects.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    if objects:
        return objects

    return None


async def generate(
    prompt: str,
    system_prompt: str = "",
    config: OllamaConfig | None = None,
    response_format: str = "json",
) -> OllamaResponse:
    """Call Ollama /api/chat with structured output.

    If response_format="json", sets format="json" in the request body
    (Ollama's native JSON mode).

    Retries on connection errors and 5xx responses.
    """
    if config is None:
        config = get_default_config()

    # gpt-oss and other chat/reasoning models only emit through /api/chat
    # (the harmony template path); /api/generate raw-completion returns empty.
    # /api/chat is universal across instruct + reasoning models.
    url = f"{config.base_url}/api/chat"
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": config.temperature,
            "num_ctx": config.num_ctx,
        },
    }
    if response_format == "json":
        payload["format"] = "json"

    last_error = None
    for attempt in range(config.max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}: {resp.text}"
                    logger.warning(
                        "ollama_retry",
                        attempt=attempt + 1,
                        status_code=resp.status_code,
                    )
                    continue
                resp.raise_for_status()
                data = resp.json()
                raw_text = data.get("message", {}).get("content", "")
                parsed = _parse_json_robust(raw_text) if response_format == "json" else None

                return OllamaResponse(
                    raw_text=raw_text,
                    parsed_json=parsed,
                    model=data.get("model", config.model),
                    total_duration_ms=data.get("total_duration", 0) // 1_000_000,
                    prompt_eval_count=data.get("prompt_eval_count", 0),
                    eval_count=data.get("eval_count", 0),
                )

        except httpx.TimeoutException:
            last_error = "Request timed out"
            logger.warning("ollama_timeout", attempt=attempt + 1, timeout=config.timeout_seconds)
        except httpx.ConnectError as e:
            last_error = f"Connection error: {e}"
            logger.warning("ollama_connect_error", attempt=attempt + 1, error=str(e))

    raise RuntimeError(f"Ollama request failed after {config.max_retries + 1} attempts: {last_error}")


class OllamaProvider:
    """Local Ollama provider implementing LLMProvider interface.

    Wraps existing module-level functions for backward compatibility.
    """

    def __init__(
        self,
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        timeout: int = 300,
        num_ctx: int = 8192,
    ):
        self.config = OllamaConfig(
            base_url=base_url, model=model, timeout_seconds=timeout, num_ctx=num_ctx
        )

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = True,
    ):
        """Call existing module-level generate() and return LLMResponse."""
        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )
        from src.shared.llm_provider import LLMResponse

        async with record_llm_call(
            system="ollama",
            model=self.config.model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            config = OllamaConfig(
                base_url=self.config.base_url,
                model=self.config.model,
                temperature=temperature,
                timeout_seconds=self.config.timeout_seconds,
                num_ctx=self.config.num_ctx,
            )
            response = await generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                config=config,
                response_format="json" if json_mode else "text",
            )
            llm_ctx.set_input_tokens(response.prompt_eval_count)
            llm_ctx.set_output_tokens(response.eval_count)
            return LLMResponse(
                text=response.raw_text,
                raw_response={"parsed_json": response.parsed_json},
                model=response.model,
                input_tokens=response.prompt_eval_count,
                output_tokens=response.eval_count,
                duration_ms=response.total_duration_ms,
                provider="ollama",
            )

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        """Generate with grammar-constrained schema decoding (XGrammar Tier A).

        # D444 — XGrammar Tier-A with recovery to Tier-B on 400. Authorization: D444.2/D444.3.
        """
        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )
        from src.shared.llm_provider import LLMResponse

        schema = response_model.model_json_schema()

        # /api/chat for universal model support (gpt-oss/reasoning models emit
        # nothing via /api/generate). Grammar-constrained `format=schema` works
        # identically on /api/chat.
        url = f"{self.config.base_url}/api/chat"
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {
                "temperature": temperature,
                "num_ctx": self.config.num_ctx,
            },
        }

        async with record_llm_call(
            system="ollama",
            model=self.config.model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    resp = await client.post(url, json=payload)

                    if resp.status_code == 400:
                        # Tier-B fallback: XGrammar schema rejection
                        logger.warning(
                            "ollama_xgrammar_fallback",
                            status=400,
                            detail=resp.text[:200],
                        )
                        payload["format"] = "json"
                        resp = await client.post(url, json=payload)
                        resp.raise_for_status()
                        data = resp.json()
                        raw_text = data.get("message", {}).get("content", "")
                        parsed_data = _parse_json_robust(raw_text)
                        if parsed_data is None:
                            raise ValueError("Tier-B fallback: JSON parse failed")
                        instance = response_model.model_validate(parsed_data)
                        llm_ctx.set_input_tokens(data.get("prompt_eval_count", 0))
                        llm_ctx.set_output_tokens(data.get("eval_count", 0))
                        return LLMResponse(
                            text=raw_text,
                            raw_response=data,
                            model=data.get("model", self.config.model),
                            input_tokens=data.get("prompt_eval_count", 0),
                            output_tokens=data.get("eval_count", 0),
                            duration_ms=data.get("total_duration", 0) // 1_000_000,
                            provider="ollama",
                            parsed=instance,
                        )

                    resp.raise_for_status()
                    data = resp.json()
                    raw_text = data.get("message", {}).get("content", "")
                    # XGrammar normally guarantees valid JSON, but guard the
                    # success path anyway: recover via _parse_json_robust and
                    # raise a typed error (matching Tier-B) instead of leaking
                    # an unhandled JSONDecodeError.
                    try:
                        parsed_data = json.loads(raw_text)
                    except json.JSONDecodeError:
                        parsed_data = _parse_json_robust(raw_text)
                        if parsed_data is None:
                            raise ValueError(
                                "Structured output: JSON parse failed after recovery"
                            )
                    instance = response_model.model_validate(parsed_data)
                    llm_ctx.set_input_tokens(data.get("prompt_eval_count", 0))
                    llm_ctx.set_output_tokens(data.get("eval_count", 0))
                    return LLMResponse(
                        text=raw_text,
                        raw_response=data,
                        model=data.get("model", self.config.model),
                        input_tokens=data.get("prompt_eval_count", 0),
                        output_tokens=data.get("eval_count", 0),
                        duration_ms=data.get("total_duration", 0) // 1_000_000,
                        provider="ollama",
                        parsed=instance,
                    )

            except (httpx.TimeoutException, httpx.ConnectError):
                raise

    async def generate_vision(
        self,
        prompt: str,
        images: list[bytes],
        response_model: type | None = None,
    ):
        """Generate from images + text via Ollama /api/chat (D500).

        D500: 5th method on LLMProvider. Uses base64 `images` array in
        /api/chat messages. When response_model is supplied, routes through
        D444 generate_structured grammar-constrained path.
        D138 airgap-default: localhost Ollama, no network egress.
        """
        import base64
        import json as _json

        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )
        from src.shared.llm_provider import LLMResponse, _resize_image_if_needed, read_vision_config_from_yaml

        vision_cfg = read_vision_config_from_yaml()
        if not vision_cfg["enabled"]:
            raise RuntimeError("Vision is disabled (llm.vision.enabled=false in config/discovery.yaml)")

        vision_model = vision_cfg["model"]

        # Resize images that exceed provider limits (D500 Pillow resize)
        processed_images = [_resize_image_if_needed(img) for img in images]
        b64_images = [base64.b64encode(img).decode("ascii") for img in processed_images]

        url = f"{self.config.base_url}/api/chat"
        payload: dict = {
            "model": vision_model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": b64_images,
                },
            ],
        }

        # When response_model is supplied, add grammar-constrained format (D444 reuse)
        if response_model is not None:
            schema = response_model.model_json_schema()
            payload["format"] = schema

        async with record_llm_call(
            system="ollama",
            model=vision_model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                resp = await client.post(url, json=payload)

                if resp.status_code == 400 and response_model is not None:
                    # Tier-B fallback: XGrammar schema rejection (D444)
                    logger.warning(
                        "ollama_vision_xgrammar_fallback",
                        status=400,
                        detail=resp.text[:200],
                    )
                    payload.pop("format", None)
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    raw_text = data.get("message", {}).get("content", "")
                    parsed_data = _parse_json_robust(raw_text)
                    if parsed_data is None:
                        raise ValueError("Vision Tier-B fallback: JSON parse failed")
                    instance = response_model.model_validate(parsed_data)
                    llm_ctx.set_input_tokens(data.get("prompt_eval_count", 0))
                    llm_ctx.set_output_tokens(data.get("eval_count", 0))
                    return LLMResponse(
                        text=raw_text,
                        raw_response=data,
                        model=data.get("model", vision_model),
                        input_tokens=data.get("prompt_eval_count", 0),
                        output_tokens=data.get("eval_count", 0),
                        duration_ms=data.get("total_duration", 0) // 1_000_000,
                        provider="ollama",
                        parsed=instance,
                    )

                resp.raise_for_status()
                data = resp.json()
                raw_text = data.get("message", {}).get("content", "")

                parsed_instance = None
                if response_model is not None:
                    # Guarded like the text path: recover malformed JSON via
                    # _parse_json_robust, raise typed ValueError (Tier-B parity)
                    # instead of an unhandled JSONDecodeError.
                    try:
                        parsed_data = _json.loads(raw_text)
                    except _json.JSONDecodeError:
                        parsed_data = _parse_json_robust(raw_text)
                        if parsed_data is None:
                            raise ValueError(
                                "Vision structured output: JSON parse failed after recovery"
                            )
                    parsed_instance = response_model.model_validate(parsed_data)

                llm_ctx.set_input_tokens(data.get("prompt_eval_count", 0))
                llm_ctx.set_output_tokens(data.get("eval_count", 0))
                return LLMResponse(
                    text=raw_text,
                    raw_response=data,
                    model=data.get("model", vision_model),
                    input_tokens=data.get("prompt_eval_count", 0),
                    output_tokens=data.get("eval_count", 0),
                    duration_ms=data.get("total_duration", 0) // 1_000_000,
                    provider="ollama",
                    parsed=parsed_instance,
                )

    async def health_check(self) -> dict:
        """Call existing check_ollama_health() and reformat."""
        result = await check_ollama_health(self.config)
        return {
            "healthy": result.get("healthy", False),
            "model_available": result.get("model_available", False),
            "provider": "ollama",
            "model": self.config.model,
            "details": ", ".join(result.get("models", [])) or result.get("error", ""),
        }


async def check_ollama_health(config: OllamaConfig | None = None) -> dict:
    """GET /api/tags — verify Ollama is running and target model is available."""
    if config is None:
        config = get_default_config()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{config.base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            # Check if target model is available (match with or without tag)
            model_available = any(
                config.model in m or m.startswith(config.model.split(":")[0])
                for m in models
            )
            return {
                "healthy": True,
                "model_available": model_available,
                "models": models,
            }
    except Exception as e:
        return {
            "healthy": False,
            "model_available": False,
            "models": [],
            "error": str(e),
        }
