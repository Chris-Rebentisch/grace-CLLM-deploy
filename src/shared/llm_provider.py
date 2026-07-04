"""Abstract LLM provider interface, registry, factory, and config helpers."""

from abc import ABC, abstractmethod
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, Field

from src.shared.config import get_settings

logger = structlog.get_logger()

_DISCOVERY_YAML = Path(__file__).resolve().parent.parent.parent / "config" / "discovery.yaml"
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


class LLMResponse(BaseModel):
    """Unified response from any LLM provider."""

    text: str = Field(description="The generated text content")
    raw_response: dict = Field(default_factory=dict, description="Full provider response for debugging")
    model: str = Field(default="", description="Model that responded")
    input_tokens: int = Field(default=0, description="Input token count")
    output_tokens: int = Field(default=0, description="Output token count")
    duration_ms: int = Field(default=0, description="Wall-clock time in ms")
    provider: str = Field(default="", description="Provider name")
    parsed: BaseModel | None = Field(default=None, description="Validated response model instance")


class LLMProvider(ABC):
    """Abstract interface. All providers implement this."""

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = True,
    ) -> LLMResponse:
        """Generate a completion."""
        ...

    @abstractmethod
    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate with grammar-constrained schema decoding. Returns LLMResponse with .parsed populated."""
        ...

    @abstractmethod
    async def health_check(self) -> dict:
        """Verify provider is reachable and model is available."""
        ...

    @abstractmethod
    async def generate_vision(
        self,
        prompt: str,
        images: list[bytes],
        response_model: type[BaseModel] | None = None,
    ) -> "LLMResponse":
        """Generate a response from images + text prompt.

        D500: 5th abstractmethod on LLMProvider ABC.
        When response_model is supplied, routes through D444 generate_structured
        grammar-constrained path for validated .parsed output.
        D138 airgap-default: local Ollama is the default provider.
        D120/D217: band-only severity on structured models (no float).
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return 'ollama', 'anthropic', or 'openai'."""
        ...


PROVIDER_REGISTRY = [
    {
        "id": "ollama",
        "label": "Local (Ollama)",
        "description": "Runs on your machine. No internet needed. No cost.",
        "requires_api_key": False,
        "requires_base_url": False,
        "default_model": "qwen2.5:7b",
        "default_base_url": "http://localhost:11434",
        "popular_models": [
            "qwen2.5:7b", "qwen2.5:72b", "llama3.3:70b", "mistral:7b", "gemma3:12b",
        ],
    },
    {
        "id": "anthropic",
        "label": "Anthropic (Claude)",
        "description": "Cloud API. Requires API key from console.anthropic.com",
        "requires_api_key": True,
        "requires_base_url": False,
        "default_model": "claude-haiku-4-5-20251001",
        "default_base_url": "https://api.anthropic.com/v1/messages",
        "popular_models": [
            "claude-haiku-4-5-20251001", "claude-sonnet-4-6-20250514",
        ],
    },
    {
        "id": "openai",
        "label": "OpenAI / DeepSeek / Groq",
        "description": "Any OpenAI-compatible API. Change base URL for different providers.",
        "requires_api_key": True,
        "requires_base_url": True,
        "default_model": "gpt-4.1-nano",
        "default_base_url": "https://api.openai.com/v1",
        "popular_models": [
            "gpt-4.1-nano", "gpt-4.1-mini", "deepseek-chat", "llama-3.3-70b-versatile",
        ],
        "preset_endpoints": [
            {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "default_model": "gpt-4.1-nano"},
            {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-chat"},
            {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "default_model": "llama-3.3-70b-versatile"},
            {"label": "Together AI", "base_url": "https://api.together.xyz/v1", "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
        ],
    },
]


# --- Config helpers ---


def _resize_image_if_needed(image_bytes: bytes, max_dim: int = 2576) -> bytes:
    """Resize image to fit within max_dim pixels on longest side (D500).

    Uses Pillow (12.1.1, transitively available — no uv add needed).
    Returns JPEG bytes if resized, original bytes if already within limits.
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    if max(img.size) <= max_dim:
        return image_bytes
    img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def read_vision_config_from_yaml() -> dict:
    """Read the llm.vision section from config/discovery.yaml.

    Returns defaults if missing. D500.

    Provider-aware defaults: when the resolved main ``llm.provider`` is
    ``anthropic``, Claude models are vision-capable, so the vision default
    follows the main Anthropic provider/model instead of hardcoding the
    local ``qwen2.5-vl:32b`` (which only applies to the Ollama provider).
    An explicit ``llm.vision`` block in config/discovery.yaml always wins.
    """
    main = read_llm_config_from_yaml()
    if main["provider"] == "anthropic":
        default_provider = "anthropic"
        default_model = main["model"]
    else:
        default_provider = main["provider"]
        default_model = "qwen2.5-vl:32b"

    try:
        with open(_DISCOVERY_YAML) as f:
            data = yaml.safe_load(f) or {}
        vision = data.get("llm", {}).get("vision", {}) or {}
    except (FileNotFoundError, yaml.YAMLError):
        vision = {}

    # F2-05 (second-validation-run ledger, cf. F-11/F-14): get_provider(config_override=...)
    # bypasses get_llm_config()'s .env merge, so a vision config without api_key
    # can NEVER construct the Anthropic provider (instant ValueError, swallowed as
    # vision_call_failed). Surface the same settings-sourced key here.
    settings = get_settings()
    return {
        "provider": vision.get("provider", default_provider),
        "model": vision.get("model", default_model),
        "enabled": bool(vision.get("enabled", True)),
        "api_key": settings.llm_api_key,
    }


def read_llm_config_from_yaml() -> dict:
    """Read the llm section from config/discovery.yaml. Returns defaults if missing.

    D232 (Chunk 30): also surfaces the top-level ``airgap_mode`` key so the
    settings UI can render a first-class toggle alongside provider/model.
    Default is ``True`` (airgapped by default per CLAUDE.md Critical Rules).
    """
    try:
        with open(_DISCOVERY_YAML) as f:
            data = yaml.safe_load(f) or {}
        llm = data.get("llm", {})
        return {
            "provider": llm.get("provider", "ollama"),
            "model": llm.get("model", "qwen2.5:7b"),
            "base_url": llm.get("base_url", "http://localhost:11434"),
            "timeout": llm.get("timeout", 300),
            "num_ctx": llm.get("num_ctx", 8192),
            "airgap_mode": bool(data.get("airgap_mode", True)),
            "structured_output": llm.get("structured_output", "auto"),
        }
    except (FileNotFoundError, yaml.YAMLError) as exc:
        # Loud degradation: this deployment is configured for Anthropic in
        # config/discovery.yaml. Falling back to Ollama defaults means the
        # config file is missing or unparseable — that is an operator-visible
        # error, not a silent default.
        logger.error(
            "discovery_yaml_missing_falling_back_to_ollama",
            path=str(_DISCOVERY_YAML),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return {
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434",
            "timeout": 300,
            "num_ctx": 8192,
            "airgap_mode": True,
            "structured_output": "auto",
        }


def write_llm_config_to_yaml(config: dict) -> None:
    """Update ONLY the llm section of config/discovery.yaml.

    D232 (Chunk 30): if ``airgap_mode`` is present in ``config``, the
    top-level key is updated alongside the ``llm:`` block. When the key
    is omitted from ``config`` the existing value is preserved (no
    accidental flip during a provider-only edit).
    """
    with open(_DISCOVERY_YAML) as f:
        data = yaml.safe_load(f) or {}

    # Preserve existing structured_output when rewriting llm block (D444)
    existing_structured_output = data.get("llm", {}).get("structured_output")
    # Preserve nested/adjacent llm keys the UI editor does not manage
    # (vision sub-block for D500, num_ctx) — previously silently dropped
    # on every config save.
    existing_vision = data.get("llm", {}).get("vision")
    existing_num_ctx = data.get("llm", {}).get("num_ctx")

    data["llm"] = {
        "provider": config["provider"],
        "model": config["model"],
        "base_url": config.get("base_url", ""),
        "timeout": config.get("timeout", 300),
    }

    # Carry structured_output from config or preserve on-disk value
    if "structured_output" in config:
        data["llm"]["structured_output"] = config["structured_output"]
    elif existing_structured_output is not None:
        data["llm"]["structured_output"] = existing_structured_output

    if existing_num_ctx is not None:
        data["llm"]["num_ctx"] = existing_num_ctx
    if existing_vision is not None:
        data["llm"]["vision"] = existing_vision

    if "airgap_mode" in config:
        data["airgap_mode"] = bool(config["airgap_mode"])

    with open(_DISCOVERY_YAML, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def update_env_api_key(api_key: str) -> None:
    """Update LLM_API_KEY in .env file."""
    if _ENV_PATH.exists():
        content = _ENV_PATH.read_text()
    else:
        content = ""

    lines = content.split("\n")
    found = False
    for i, line in enumerate(lines):
        if line.startswith("LLM_API_KEY="):
            lines[i] = f"LLM_API_KEY={api_key}"
            found = True
            break

    if not found:
        lines.append(f"LLM_API_KEY={api_key}")

    _ENV_PATH.write_text("\n".join(lines))


def _mask_api_key(key: str) -> str:
    """Mask API key: first 4 chars + '...'.

    Observation 3 ratification (2026-04-23): shortened from 8 to 4 chars
    to reduce fingerprint-leak surface area while still letting the UI
    confirm the right key class is loaded (e.g. "sk-a..." = Anthropic).
    """
    if not key:
        return ""
    if len(key) <= 4:
        return key + "..."
    return key[:4] + "..."


def get_llm_config() -> dict:
    """Read LLM config from discovery.yaml + .env LLM_API_KEY."""
    yaml_config = read_llm_config_from_yaml()
    settings = get_settings()
    yaml_config["api_key"] = settings.llm_api_key
    return yaml_config


def get_provider_display_config() -> dict:
    """Return current config for display. Masks API key."""
    config = get_llm_config()
    api_key = config.pop("api_key", "")
    config["api_key_set"] = bool(api_key)
    config["api_key_preview"] = _mask_api_key(api_key)
    return config


def _is_private_network_url(url: str) -> bool:
    """Check if URL points to localhost or private network."""
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    if hostname.startswith("192.168.") or hostname.startswith("10."):
        return True
    if hostname.startswith("172."):
        parts = hostname.split(".")
        if len(parts) >= 2:
            try:
                second_octet = int(parts[1])
                if 16 <= second_octet <= 31:
                    return True
            except ValueError:
                pass
    return False


def get_provider(config_override: dict | None = None) -> "LLMProvider":
    """Factory. Returns the right provider based on config.

    If config_override is provided, uses those values instead of reading from files.
    """
    if config_override:
        config = config_override
    else:
        config = get_llm_config()

    provider_name = config.get("provider", "ollama")
    model = config.get("model", "qwen2.5:7b")
    base_url = config.get("base_url", "")
    timeout = config.get("timeout", 300)
    api_key = config.get("api_key", "")
    num_ctx = config.get("num_ctx", 8192)

    if provider_name == "ollama":
        from src.discovery.ollama_client import OllamaProvider

        return OllamaProvider(
            model=model,
            base_url=base_url or "http://localhost:11434",
            timeout=timeout,
            num_ctx=num_ctx,
        )

    elif provider_name == "anthropic":
        if not api_key:
            raise ValueError(
                "Anthropic provider requires LLM_API_KEY in .env. "
                "Get your key at console.anthropic.com"
            )
        from src.shared.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=api_key, model=model, timeout=timeout)

    elif provider_name == "openai":
        if not api_key and not _is_private_network_url(base_url):
            raise ValueError(
                "OpenAI-compatible provider requires LLM_API_KEY in .env."
            )
        if not api_key:
            api_key = "no-key"  # Local servers require header but don't validate value
        from src.shared.openai_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=api_key,
            model=model,
            base_url=base_url or "https://api.openai.com/v1",
            timeout=timeout,
            structured_output=config.get("structured_output", "auto"),
        )

    else:
        raise ValueError(
            f"Unknown provider: '{provider_name}'. Must be one of: ollama, anthropic, openai"
        )
