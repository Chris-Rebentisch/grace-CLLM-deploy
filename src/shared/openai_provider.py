"""OpenAI-compatible API provider via httpx (no SDK). Covers OpenAI, DeepSeek, Groq, etc."""

import asyncio
import time

import httpx
import structlog

from src.shared.llm_provider import LLMProvider, LLMResponse

logger = structlog.get_logger()


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible API. Works with OpenAI, DeepSeek, Groq, etc."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4.1-nano",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 300,
        structured_output: str = "auto",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._structured_output = structured_output
        self._structured_tier_b: bool = False  # cached downgrade flag

    @property
    def provider_name(self) -> str:
        return "openai"

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = True,
    ) -> LLMResponse:
        """POST to {base_url}/chat/completions.

        json_mode=True adds response_format: {"type": "json_object"}.
        Retries on 429 with exponential backoff.
        """
        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )

        url = f"{self.base_url}/chat/completions"
        payload: dict = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with record_llm_call(
            system="openai",
            model=self.model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            last_error = None
            for attempt in range(4):
                start = time.monotonic()
                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(url, json=payload, headers=headers)

                        if resp.status_code == 429:
                            wait = float(resp.headers.get("retry-after", 2 ** attempt))
                            logger.warning("openai_retry", status=429, wait=wait, attempt=attempt + 1)
                            await asyncio.sleep(wait)
                            continue

                        if resp.status_code == 401:
                            raise ValueError("API key is invalid (401 Unauthorized)")

                        resp.raise_for_status()
                        data = resp.json()
                        elapsed = int((time.monotonic() - start) * 1000)

                        text = data["choices"][0]["message"]["content"]
                        usage = data.get("usage", {})

                        llm_ctx.set_input_tokens(int(usage.get("prompt_tokens", 0)))
                        llm_ctx.set_output_tokens(int(usage.get("completion_tokens", 0)))
                        return LLMResponse(
                            text=text,
                            raw_response=data,
                            model=data.get("model", self.model),
                            input_tokens=usage.get("prompt_tokens", 0),
                            output_tokens=usage.get("completion_tokens", 0),
                            duration_ms=elapsed,
                            provider="openai",
                        )

                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    last_error = str(e)
                    logger.warning("openai_error", error=str(e), attempt=attempt + 1)

            raise RuntimeError(f"OpenAI-compatible request failed after retries: {last_error}")

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate with strict-mode response_format (Tier A) or json_object fallback (Tier B).

        # D444 — OpenAI strict-mode Tier-A with cached Tier-B downgrade. Authorization: D444.2/D444.3.
        """
        import json

        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )
        from src.shared.schema_transform import to_strict_json_schema

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        use_tier_b = self._structured_tier_b or self._structured_output == "tier_b"
        force_tier_a = self._structured_output == "tier_a"

        async with record_llm_call(
            system="openai",
            model=self.model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            if not use_tier_b:
                # Tier A: strict-mode json_schema
                strict_schema = to_strict_json_schema(response_model)
                payload: dict = {
                    "model": self.model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": response_model.__name__,
                            "strict": True,
                            "schema": strict_schema,
                        },
                    },
                }

                start = time.monotonic()
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)

                    if resp.status_code == 400 and not force_tier_a:
                        # Cache downgrade for this process lifetime
                        self._structured_tier_b = True
                        logger.warning(
                            "openai_structured_tier_b_downgrade",
                            status=400,
                            detail=resp.text[:200],
                        )
                        # Fall through to Tier B below
                    elif resp.status_code == 400 and force_tier_a:
                        resp.raise_for_status()
                    else:
                        resp.raise_for_status()
                        data = resp.json()
                        elapsed = int((time.monotonic() - start) * 1000)
                        text = data["choices"][0]["message"]["content"]
                        usage = data.get("usage", {})
                        parsed_data = json.loads(text)
                        instance = response_model.model_validate(parsed_data)
                        llm_ctx.set_input_tokens(int(usage.get("prompt_tokens", 0)))
                        llm_ctx.set_output_tokens(int(usage.get("completion_tokens", 0)))
                        return LLMResponse(
                            text=text,
                            raw_response=data,
                            model=data.get("model", self.model),
                            input_tokens=usage.get("prompt_tokens", 0),
                            output_tokens=usage.get("completion_tokens", 0),
                            duration_ms=elapsed,
                            provider="openai",
                            parsed=instance,
                        )

            # Tier B: json_object mode + recovery parser
            from src.discovery.ollama_client import _parse_json_robust

            payload_b: dict = {
                "model": self.model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt + "\n\nIMPORTANT: Respond with ONLY valid JSON."},
                ],
                "response_format": {"type": "json_object"},
            }

            start = time.monotonic()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload_b, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                elapsed = int((time.monotonic() - start) * 1000)
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                parsed_data = _parse_json_robust(text)
                if parsed_data is None:
                    raise ValueError("Tier-B fallback: JSON parse failed")
                instance = response_model.model_validate(parsed_data)
                llm_ctx.set_input_tokens(int(usage.get("prompt_tokens", 0)))
                llm_ctx.set_output_tokens(int(usage.get("completion_tokens", 0)))
                return LLMResponse(
                    text=text,
                    raw_response=data,
                    model=data.get("model", self.model),
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    duration_ms=elapsed,
                    provider="openai",
                    parsed=instance,
                )

    async def generate_vision(
        self,
        prompt: str,
        images: list[bytes],
        response_model: type | None = None,
    ) -> LLMResponse:
        """Generate from images + text via OpenAI-compatible chat completions (D500).

        D500: 5th method on LLMProvider. Assembles `image_url` content blocks
        with `data:image/...;base64,...` data URIs. Default vision model: gpt-4o.
        When response_model is supplied, routes through D444 strict-mode
        response_format path.
        """
        import base64
        import json

        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )
        from src.shared.llm_provider import _resize_image_if_needed

        # Resize images exceeding 2576px (D500 Pillow resize)
        processed_images = [_resize_image_if_needed(img) for img in images]

        # Build content array: image_url blocks + text block
        content: list[dict] = []
        for img_bytes in processed_images:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            # Detect media type from magic bytes
            media_type = "image/jpeg"
            if img_bytes[:4] == b"\x89PNG":
                media_type = "image/png"
            elif img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
                media_type = "image/webp"
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
            })
        content.append({"type": "text", "text": prompt})

        url = f"{self.base_url}/chat/completions"
        payload: dict = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content}],
        }

        # When response_model is supplied, add D444 strict-mode response_format
        if response_model is not None:
            from src.shared.schema_transform import to_strict_json_schema

            strict_schema = to_strict_json_schema(response_model)
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": strict_schema,
                },
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with record_llm_call(
            system="openai",
            model=self.model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            start = time.monotonic()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 400 and response_model is not None:
                    # Tier-B fallback (D444)
                    from src.discovery.ollama_client import _parse_json_robust

                    logger.warning("openai_vision_tier_b_fallback", status=400, detail=resp.text[:200])
                    payload.pop("response_format", None)
                    payload["response_format"] = {"type": "json_object"}
                    # Append JSON instruction
                    content_copy = list(content)
                    content_copy.append({"type": "text", "text": "\n\nIMPORTANT: Respond with ONLY valid JSON."})
                    payload["messages"] = [{"role": "user", "content": content_copy}]
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    elapsed = int((time.monotonic() - start) * 1000)
                    text = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    parsed_data = _parse_json_robust(text)
                    if parsed_data is None:
                        raise ValueError("Vision Tier-B fallback: JSON parse failed")
                    instance = response_model.model_validate(parsed_data)
                    llm_ctx.set_input_tokens(int(usage.get("prompt_tokens", 0)))
                    llm_ctx.set_output_tokens(int(usage.get("completion_tokens", 0)))
                    return LLMResponse(
                        text=text, raw_response=data, model=data.get("model", self.model),
                        input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0),
                        duration_ms=elapsed, provider="openai", parsed=instance,
                    )

                resp.raise_for_status()
                data = resp.json()
                elapsed = int((time.monotonic() - start) * 1000)
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})

                parsed_instance = None
                if response_model is not None:
                    parsed_data = json.loads(text)
                    parsed_instance = response_model.model_validate(parsed_data)

                llm_ctx.set_input_tokens(int(usage.get("prompt_tokens", 0)))
                llm_ctx.set_output_tokens(int(usage.get("completion_tokens", 0)))
                return LLMResponse(
                    text=text, raw_response=data, model=data.get("model", self.model),
                    input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0),
                    duration_ms=elapsed, provider="openai", parsed=parsed_instance,
                )

    async def health_check(self) -> dict:
        """Check model availability. Try /models first, fall back to tiny completion."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m.get("id", "") for m in data.get("data", [])]
                    model_found = self.model in models
                    return {
                        "healthy": True,
                        "model_available": model_found,
                        "provider": "openai",
                        "model": self.model,
                        "details": f"Found {len(models)} models" + ("" if model_found else f", '{self.model}' not in list"),
                    }
                elif resp.status_code == 401:
                    return {
                        "healthy": False,
                        "model_available": False,
                        "provider": "openai",
                        "model": self.model,
                        "details": "API key is invalid (401)",
                    }
        except Exception:
            pass

        # Fallback: tiny completion
        try:
            result = await self.generate(
                system_prompt="You are a test.",
                user_prompt="Say hi.",
                max_tokens=5,
                json_mode=False,
            )
            return {
                "healthy": True,
                "model_available": True,
                "provider": "openai",
                "model": self.model,
                "details": f"Response: {result.text[:50]}",
            }
        except ValueError as e:
            return {
                "healthy": False,
                "model_available": False,
                "provider": "openai",
                "model": self.model,
                "details": str(e),
            }
        except Exception as e:
            return {
                "healthy": False,
                "model_available": False,
                "provider": "openai",
                "model": self.model,
                "details": str(e),
            }
