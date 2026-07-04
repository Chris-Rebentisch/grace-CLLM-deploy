"""D471 router config model tests (Chunk 72b CP1)."""

from pathlib import Path

from src.extraction.router_config import (
    ExtractionShard,
    ProviderProfile,
    RouterConfig,
    load_router_config,
)


def test_provider_profile_round_trip():
    """Pydantic model serializes and deserializes with all Layer A+B+E fields."""
    data = {
        "context_window": 131072,
        "max_output_tokens": 32768,
        "pricing_input_per_m": 1.0,
        "pricing_output_per_m": 5.0,
        "batch_discount": 0.5,
        "cache_hit_price": 0.1,
        "airgap_eligible": True,
        "data_residency": "us",
        "typical_throughput_tok_s": 100.0,
    }
    profile = ProviderProfile.model_validate(data)
    dumped = profile.model_dump()
    restored = ProviderProfile.model_validate(dumped)
    assert restored.context_window == 131072
    assert restored.max_output_tokens == 32768
    assert restored.pricing_input_per_m == 1.0
    assert restored.pricing_output_per_m == 5.0
    assert restored.batch_discount == 0.5
    assert restored.cache_hit_price == 0.1
    assert restored.airgap_eligible is True
    assert restored.data_residency == "us"
    assert restored.typical_throughput_tok_s == 100.0


def test_router_config_weight_defaults():
    """Weight knobs default to spec values."""
    config = RouterConfig(profiles={})
    assert config.alpha_cost == 1.0
    assert config.beta_time == 0.0
    assert config.gamma_quality == 0.0
    assert config.exploration_epsilon == 0.0


def test_load_router_config_yaml():
    """Loads the on-disk YAML and confirms 5 profiles with correct keys."""
    config = load_router_config()
    assert len(config.profiles) == 8
    expected_keys = {"ollama_72b", "haiku_4_5", "deepseek_v3", "sonnet_4_6", "opus_4_7", "ollama_vision", "haiku_4_5_vision", "gpt4o_vision"}
    assert set(config.profiles.keys()) == expected_keys
    # Verify Ollama is airgap eligible
    assert config.profiles["ollama_72b"].airgap_eligible is True
    # Verify cloud providers are not airgap eligible
    assert config.profiles["haiku_4_5"].airgap_eligible is False
    assert config.profiles["deepseek_v3"].airgap_eligible is False


def test_optional_layer_c_fields():
    """typical_throughput_tok_s is None by default and accepts a float."""
    # Default is None
    profile = ProviderProfile(
        context_window=100000,
        max_output_tokens=8192,
        pricing_input_per_m=0.0,
        pricing_output_per_m=0.0,
        airgap_eligible=True,
    )
    assert profile.typical_throughput_tok_s is None

    # Accepts a float
    profile_with = ProviderProfile(
        context_window=100000,
        max_output_tokens=8192,
        pricing_input_per_m=0.0,
        pricing_output_per_m=0.0,
        airgap_eligible=True,
        typical_throughput_tok_s=42.5,
    )
    assert profile_with.typical_throughput_tok_s == 42.5
