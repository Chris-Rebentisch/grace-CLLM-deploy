"""CP4 contract test: D503 router-level airgap gate for vision providers.

Verifies that when airgap_mode=true, the router rejects cloud vision profiles
even for non-sensitive images (the blanket airgap gate operates independently
of sensitivity classification).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.extraction.router import select_vision_provider
from src.extraction.router_config import ProviderProfile, RouterConfig


def _make_config(*, include_local: bool = True, include_cloud: bool = True) -> RouterConfig:
    """Build a RouterConfig with vision profiles for testing."""
    profiles = {}
    if include_local:
        profiles["ollama_vision"] = ProviderProfile(
            context_window=131072,
            max_output_tokens=32768,
            pricing_input_per_m=0.0,
            pricing_output_per_m=0.0,
            airgap_eligible=True,
            data_residency="local",
            vision_capable=True,
            vision_model="qwen2.5-vl:32b",
        )
    if include_cloud:
        profiles["haiku_vision"] = ProviderProfile(
            context_window=200000,
            max_output_tokens=8192,
            pricing_input_per_m=1.0,
            pricing_output_per_m=5.0,
            airgap_eligible=False,
            data_residency="us",
            vision_capable=True,
            vision_model="claude-haiku-4-5-20251001",
        )
    return RouterConfig(profiles=profiles)


def test_router_airgap_rejects_cloud_vision_for_nonsensitive_image():
    """D503: airgap_mode=true + non-sensitive image -> only local vision provider selected.

    The blanket airgap gate rejects all non-airgap_eligible vision profiles
    regardless of sensitivity classification. This verifies it works for
    a non-sensitive image (no pii_dense, no privileged tags).
    """
    config = _make_config(include_local=True, include_cloud=True)

    # Airgap ON: must select local provider, ignoring cloud even for non-sensitive
    with patch("src.extraction.router._read_airgap_mode", return_value=True):
        result = select_vision_provider(config, sensitivity_tags="")

    assert result is not None
    profile_name, model = result
    assert profile_name == "ollama_vision", f"Expected local provider, got {profile_name}"
    assert model == "qwen2.5-vl:32b"

    # Airgap OFF + no sensitivity: should pick cloud provider
    with patch("src.extraction.router._read_airgap_mode", return_value=False):
        result = select_vision_provider(config, sensitivity_tags="")

    assert result is not None
    profile_name, model = result
    assert profile_name == "haiku_vision", f"Expected cloud provider when airgap off, got {profile_name}"

    # Airgap OFF + pii_dense: should force local
    with patch("src.extraction.router._read_airgap_mode", return_value=False):
        result = select_vision_provider(config, sensitivity_tags="|pii_dense|")

    assert result is not None
    profile_name, _ = result
    assert profile_name == "ollama_vision", f"Expected local for PII-dense, got {profile_name}"

    # Airgap ON + cloud only (no local) -> RuntimeError
    cloud_only_config = _make_config(include_local=False, include_cloud=True)
    with patch("src.extraction.router._read_airgap_mode", return_value=True):
        with pytest.raises(RuntimeError, match="D503"):
            select_vision_provider(cloud_only_config, sensitivity_tags="")
