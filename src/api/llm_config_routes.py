"""FastAPI endpoints for LLM provider configuration (Phase 5 settings UI ready)."""

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status

from src.support.refused_routes import no_support_session

from src.shared.llm_provider import (
    PROVIDER_REGISTRY,
    get_provider,
    get_provider_display_config,
    read_llm_config_from_yaml,
    update_env_api_key,
    write_llm_config_to_yaml,
)

router = APIRouter(prefix="/api/llm", tags=["llm-config"])


class UpdateLLMConfigRequest(BaseModel):
    """Request body for updating LLM config."""

    provider: str = Field(description="Provider id: ollama, anthropic, or openai")
    model: str = Field(description="Model name")
    base_url: str = Field(default="", description="Base URL for API")
    timeout: int = Field(default=300, description="Timeout in seconds")
    api_key: str | None = Field(
        default=None,
        description="API key. None=don't change, empty string=clear, value=set",
    )
    airgap_mode: bool | None = Field(
        default=None,
        description=(
            "D232 (Chunk 30): airgap toggle. None means leave the existing "
            "value alone; True/False writes the top-level key to discovery.yaml. "
            "When True and the requested provider has requires_api_key=True, the "
            "request is rejected with 422 (defense-in-depth)."
        ),
    )


class TestLLMConfigRequest(BaseModel):
    """Request body for testing LLM config without saving."""

    provider: str = Field(description="Provider id")
    model: str = Field(description="Model name")
    base_url: str = Field(default="", description="Base URL")
    timeout: int = Field(default=300, description="Timeout in seconds")
    api_key: str = Field(default="", description="API key for cloud providers")


@router.get("/config")
async def get_config() -> dict:
    """Return current LLM provider configuration with masked API key."""
    return get_provider_display_config()


@router.post("/config")
@no_support_session("POST", "/api/llm/config")
async def update_config(request: UpdateLLMConfigRequest) -> dict:
    """Update LLM provider configuration.

    Writes provider/model/base_url/timeout to discovery.yaml.
    If api_key is provided and non-empty, writes to .env.

    D232 (Chunk 30): when ``airgap_mode`` is provided, the top-level key
    in discovery.yaml is also updated. Defense-in-depth: if the request
    declares ``airgap_mode=True`` AND the chosen provider has
    ``requires_api_key=True``, the request is rejected with 422 — this
    backstops the UI dialog that should already have surfaced.
    """
    # Determine the effective airgap state being committed by this request:
    # the explicit value if provided, otherwise the current persisted value.
    if request.airgap_mode is None:
        effective_airgap = bool(read_llm_config_from_yaml().get("airgap_mode", True))
    else:
        effective_airgap = bool(request.airgap_mode)

    if effective_airgap:
        registry_entry = next(
            (p for p in PROVIDER_REGISTRY if p["id"] == request.provider), None
        )
        if registry_entry is not None and registry_entry.get("requires_api_key"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error_type": "airgap_provider_conflict",
                    "message": (
                        "Provider requires sending data over the internet. "
                        "Disable airgap_mode before saving this configuration."
                    ),
                    "provider": request.provider,
                },
            )

    yaml_payload = {
        "provider": request.provider,
        "model": request.model,
        "base_url": request.base_url,
        "timeout": request.timeout,
    }
    if request.airgap_mode is not None:
        yaml_payload["airgap_mode"] = bool(request.airgap_mode)

    write_llm_config_to_yaml(yaml_payload)

    if request.api_key is not None and request.api_key != "":
        update_env_api_key(request.api_key)

    # Clear settings cache so new values are picked up
    from src.shared.config import get_settings
    get_settings.cache_clear()

    # Clear discovery config cache too
    from src.discovery.models import load_discovery_config
    load_discovery_config.cache_clear()

    return get_provider_display_config()


@router.post("/config/test")
@no_support_session("POST", "/api/llm/config/test")
async def test_config(request: TestLLMConfigRequest) -> dict:
    """Test an LLM configuration WITHOUT saving it."""
    try:
        provider = get_provider(config_override={
            "provider": request.provider,
            "model": request.model,
            "base_url": request.base_url,
            "timeout": request.timeout,
            "api_key": request.api_key,
        })

        health = await provider.health_check()

        result = {
            "healthy": health.get("healthy", False),
            "model_available": health.get("model_available", False),
            "provider": request.provider,
            "model": request.model,
            "test_response": "",
            "response_time_ms": 0,
            "error": "",
        }

        if health.get("healthy"):
            try:
                resp = await provider.generate(
                    system_prompt="You are a test.",
                    user_prompt="Say hello.",
                    max_tokens=20,
                    json_mode=False,
                )
                result["test_response"] = resp.text[:100]
                result["response_time_ms"] = resp.duration_ms
            except Exception as e:
                result["test_response"] = f"Health OK but generation failed: {e}"
        else:
            result["error"] = health.get("details", "Unknown error")

        return result

    except ValueError as e:
        return {
            "healthy": False,
            "model_available": False,
            "provider": request.provider,
            "model": request.model,
            "test_response": "",
            "response_time_ms": 0,
            "error": str(e),
        }


@router.get("/registry")
async def get_registry() -> list[dict]:
    """Return the full provider registry for the settings UI."""
    return PROVIDER_REGISTRY
