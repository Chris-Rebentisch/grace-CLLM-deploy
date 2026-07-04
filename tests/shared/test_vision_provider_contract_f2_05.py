"""F2-05 regression tests: the vision override path must construct a working
Anthropic provider — non-mocked config path.

Validation-run evidence: `get_provider(config_override=read_vision_config_from_yaml())`
bypassed the .env merge, the override carried no api_key, and provider
construction raised instantly — swallowed as `vision_call_failed`, so image
jobs "completed" in 0.9s with empty vision_description_json. Second silent
regression of the vision path (cf. F-11, F-54): the mocked suite could never
catch it, so this test exercises the REAL yaml→settings→provider chain.
"""

from __future__ import annotations

from unittest.mock import patch

from src.shared.llm_provider import get_provider, read_vision_config_from_yaml


def _fake_settings(key: str):
    class _S:
        llm_api_key = key

    return _S()


def test_vision_config_carries_settings_api_key():
    """The override dict must surface the settings-sourced key (F2-05)."""
    with patch(
        "src.shared.llm_provider.get_settings",
        return_value=_fake_settings("sk-ant-test-key-not-real"),
    ):
        cfg = read_vision_config_from_yaml()
    assert cfg.get("api_key") == "sk-ant-test-key-not-real"
    assert "provider" in cfg and "model" in cfg


def test_anthropic_vision_provider_constructs_from_yaml_config():
    """END-TO-END construction contract: yaml → override → provider, no
    ValueError, and the provider actually exposes generate_vision."""
    with patch(
        "src.shared.llm_provider.get_settings",
        return_value=_fake_settings("sk-ant-test-key-not-real"),
    ):
        cfg = read_vision_config_from_yaml()
        cfg["provider"] = "anthropic"  # pin the failure-mode provider
        provider = get_provider(config_override=cfg)  # raised ValueError pre-fix

    assert type(provider).__name__ == "AnthropicProvider"
    assert callable(getattr(provider, "generate_vision", None))
    # F-54 companion assertion: provider_name is a property, not a method.
    assert provider.provider_name == "anthropic"


def test_missing_key_still_raises_loudly():
    """With NO key anywhere, construction must still fail loudly (never a
    silent no-vision job) — the F2-05 fix must not mask a real key absence."""
    import pytest

    with patch(
        "src.shared.llm_provider.get_settings",
        return_value=_fake_settings(""),
    ):
        cfg = read_vision_config_from_yaml()
        cfg["provider"] = "anthropic"
        with pytest.raises(ValueError):
            get_provider(config_override=cfg)
