"""Anthropic Messages API provider via httpx (no SDK)."""

import asyncio
import time

import httpx
import structlog

from src.shared.llm_provider import LLMProvider, LLMResponse

logger = structlog.get_logger()


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider via httpx."""

    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001", timeout: int = 300):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = True,
    ) -> LLMResponse:
        """POST to Anthropic Messages API.

        json_mode: Anthropic has no native JSON mode. Appends instruction to prompt.
        Retries on 429 and 529 with exponential backoff.
        """
        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )

        prompt = user_prompt
        if json_mode:
            prompt += "\n\nIMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation."

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        async with record_llm_call(
            system="anthropic",
            model=self.model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            last_error = None
            for attempt in range(4):  # max 3 retries
                start = time.monotonic()
                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(self.API_URL, json=payload, headers=headers)

                        if resp.status_code in (429, 529):
                            wait = float(resp.headers.get("retry-after", 2 ** attempt))
                            logger.warning("anthropic_retry", status=resp.status_code, wait=wait, attempt=attempt + 1)
                            await asyncio.sleep(wait)
                            continue

                        if resp.status_code == 401:
                            raise ValueError("Anthropic API key is invalid (401 Unauthorized)")

                        resp.raise_for_status()
                        data = resp.json()
                        elapsed = int((time.monotonic() - start) * 1000)

                        text = data["content"][0]["text"]
                        if json_mode:
                            # F-52: json_mode here is prompt-instruction-only (no native
                            # JSON mode) and Claude routinely wraps output in ```json
                            # fences despite the instruction. Downstream callers
                            # (tier4_llm, voice_tone synthesis, NL->Cypher compile) do
                            # bare json.loads(response.text) and fail-open on the fence.
                            # Strip one leading/trailing markdown fence so the documented
                            # json_mode contract holds for this provider.
                            stripped = text.strip()
                            if stripped.startswith("```"):
                                stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
                                if stripped.rstrip().endswith("```"):
                                    stripped = stripped.rstrip()[:-3].rstrip()
                                text = stripped
                        usage = data.get("usage", {})

                        llm_ctx.set_input_tokens(int(usage.get("input_tokens", 0)))
                        llm_ctx.set_output_tokens(int(usage.get("output_tokens", 0)))
                        return LLMResponse(
                            text=text,
                            raw_response=data,
                            model=data.get("model", self.model),
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                            duration_ms=elapsed,
                            provider="anthropic",
                        )

                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    last_error = str(e)
                    logger.warning("anthropic_error", error=str(e), attempt=attempt + 1)

            raise RuntimeError(f"Anthropic request failed after retries: {last_error}")

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate with Anthropic Structured Outputs GA (Tier A) or prompt-instruction fallback (Tier B).

        # D444 — Anthropic Structured Outputs Tier-A with Tier-B on compilation 400. Authorization: D444.2/D444.3.
        """
        import json

        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )
        from src.shared.schema_transform import to_strict_json_schema

        strict_schema = to_strict_json_schema(response_model)

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": strict_schema,
                },
            },
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        async with record_llm_call(
            system="anthropic",
            model=self.model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(self.API_URL, json=payload, headers=headers)

                    if resp.status_code == 400 and "schema" in resp.text.lower():
                        # Tier-B fallback: schema compilation limit
                        logger.warning(
                            "anthropic_structured_tier_b_fallback",
                            status=400,
                            detail=resp.text[:200],
                        )
                        from src.discovery.ollama_client import _parse_json_robust

                        fallback_payload = {
                            "model": self.model,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            "system": system_prompt,
                            "messages": [{"role": "user", "content": user_prompt + "\n\nIMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation."}],
                        }
                        resp = await client.post(self.API_URL, json=fallback_payload, headers=headers)
                        resp.raise_for_status()
                        data = resp.json()
                        elapsed = int((time.monotonic() - start) * 1000)
                        text = data["content"][0]["text"]
                        usage = data.get("usage", {})
                        parsed_data = _parse_json_robust(text)
                        if parsed_data is None:
                            raise ValueError("Tier-B fallback: JSON parse failed")
                        instance = response_model.model_validate(parsed_data)
                        llm_ctx.set_input_tokens(int(usage.get("input_tokens", 0)))
                        llm_ctx.set_output_tokens(int(usage.get("output_tokens", 0)))
                        return LLMResponse(
                            text=text,
                            raw_response=data,
                            model=data.get("model", self.model),
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                            duration_ms=elapsed,
                            provider="anthropic",
                            parsed=instance,
                        )

                    resp.raise_for_status()
                    data = resp.json()
                    elapsed = int((time.monotonic() - start) * 1000)
                    text = data["content"][0]["text"]
                    usage = data.get("usage", {})
                    parsed_data = json.loads(text)
                    instance = response_model.model_validate(parsed_data)
                    llm_ctx.set_input_tokens(int(usage.get("input_tokens", 0)))
                    llm_ctx.set_output_tokens(int(usage.get("output_tokens", 0)))
                    return LLMResponse(
                        text=text,
                        raw_response=data,
                        model=data.get("model", self.model),
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        duration_ms=elapsed,
                        provider="anthropic",
                        parsed=instance,
                    )

            except (httpx.TimeoutException, httpx.ConnectError):
                raise

    async def generate_vision(
        self,
        prompt: str,
        images: list[bytes],
        response_model: type | None = None,
    ) -> LLMResponse:
        """Generate from images + text via Anthropic Messages API (D500).

        D500: 5th method on LLMProvider. Assembles `image` content blocks
        with `base64` source type. Default vision model: claude-haiku-4-5-20251001.
        When response_model is supplied, routes through D444 output_config.format path.
        """
        import base64
        import json
        import mimetypes

        from src.analytics.llm_instrumentation import (
            _current_grace_module,
            _current_grace_operation,
            record_llm_call,
        )
        from src.shared.llm_provider import _resize_image_if_needed

        # Resize images exceeding 2576px (D500 Pillow resize)
        processed_images = [_resize_image_if_needed(img) for img in images]

        # Build content blocks: image blocks + text block
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
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64,
                },
            })
        content.append({"type": "text", "text": prompt})

        payload: dict = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": content}],
        }

        # When response_model is supplied, add D444 output_config.format
        if response_model is not None:
            from src.shared.schema_transform import to_strict_json_schema

            strict_schema = to_strict_json_schema(response_model)
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": strict_schema,
                },
            }

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        async with record_llm_call(
            system="anthropic",
            model=self.model,
            grace_module=_current_grace_module(),
            grace_operation=_current_grace_operation(),
        ) as llm_ctx:
            start = time.monotonic()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.API_URL, json=payload, headers=headers)

                if resp.status_code == 400 and response_model is not None and "schema" in resp.text.lower():
                    # Tier-B fallback (D444)
                    from src.discovery.ollama_client import _parse_json_robust

                    logger.warning("anthropic_vision_tier_b_fallback", status=400, detail=resp.text[:200])
                    fallback_payload = dict(payload)
                    fallback_payload.pop("output_config", None)
                    fallback_payload["messages"] = [{"role": "user", "content": content + [{"type": "text", "text": "\n\nIMPORTANT: Respond with ONLY valid JSON. No markdown."}]}]
                    resp = await client.post(self.API_URL, json=fallback_payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    elapsed = int((time.monotonic() - start) * 1000)
                    text = data["content"][0]["text"]
                    usage = data.get("usage", {})
                    parsed_data = _parse_json_robust(text)
                    if parsed_data is None:
                        raise ValueError("Vision Tier-B fallback: JSON parse failed")
                    instance = response_model.model_validate(parsed_data)
                    llm_ctx.set_input_tokens(int(usage.get("input_tokens", 0)))
                    llm_ctx.set_output_tokens(int(usage.get("output_tokens", 0)))
                    return LLMResponse(
                        text=text, raw_response=data, model=data.get("model", self.model),
                        input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                        duration_ms=elapsed, provider="anthropic", parsed=instance,
                    )

                resp.raise_for_status()
                data = resp.json()
                elapsed = int((time.monotonic() - start) * 1000)
                text = data["content"][0]["text"]
                usage = data.get("usage", {})

                parsed_instance = None
                if response_model is not None:
                    parsed_data = json.loads(text)
                    parsed_instance = response_model.model_validate(parsed_data)

                llm_ctx.set_input_tokens(int(usage.get("input_tokens", 0)))
                llm_ctx.set_output_tokens(int(usage.get("output_tokens", 0)))
                return LLMResponse(
                    text=text, raw_response=data, model=data.get("model", self.model),
                    input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                    duration_ms=elapsed, provider="anthropic", parsed=parsed_instance,
                )

    async def health_check(self) -> dict:
        """Tiny completion test to verify API key and model."""
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
                "provider": "anthropic",
                "model": self.model,
                "details": f"Response: {result.text[:50]}",
            }
        except ValueError as e:
            return {
                "healthy": False,
                "model_available": False,
                "provider": "anthropic",
                "model": self.model,
                "details": str(e),
            }
        except Exception as e:
            return {
                "healthy": False,
                "model_available": False,
                "provider": "anthropic",
                "model": self.model,
                "details": str(e),
            }
