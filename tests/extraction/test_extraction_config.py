"""Tests for ExtractionSettings configuration."""

import os
from unittest.mock import patch

import pytest

from src.extraction.extraction_config import ExtractionSettings


class TestExtractionConfigDefaults:
    """Test default values load correctly."""

    def test_default_max_retries(self, extraction_settings):
        assert extraction_settings.max_retries == 3

    def test_default_temperature(self, extraction_settings):
        assert extraction_settings.temperature == 0.0

    def test_default_providers_none(self, extraction_settings):
        """Provider fields default to None (fall back to global)."""
        assert extraction_settings.extraction_provider is None
        assert extraction_settings.extraction_model is None
        assert extraction_settings.verification_provider is None
        assert extraction_settings.verification_model is None

    def test_default_chunk_settings(self, extraction_settings):
        assert extraction_settings.chunk_token_cap == 3000
        assert extraction_settings.chunk_overlap_tokens == 200

    def test_default_confidence_thresholds(self, extraction_settings):
        assert extraction_settings.confidence_threshold_supported == 0.8
        assert extraction_settings.confidence_threshold_insufficient == 0.5
        assert extraction_settings.confidence_threshold_refuted == 0.05

    def test_default_er_thresholds(self, extraction_settings):
        assert extraction_settings.er_default_merge == 0.85
        assert extraction_settings.er_default_review == 0.70
        assert extraction_settings.er_candidate_limit == 10


class TestExtractionConfigEnvOverride:
    """Test env_prefix works."""

    def test_env_prefix_override(self):
        """Setting EXTRACTION_MAX_RETRIES overrides default."""
        with patch.dict(os.environ, {"EXTRACTION_MAX_RETRIES": "5"}):
            settings = ExtractionSettings()
            assert settings.max_retries == 5

    def test_provider_accepts_string(self):
        """Provider fields accept string values."""
        with patch.dict(os.environ, {"EXTRACTION_EXTRACTION_PROVIDER": "anthropic"}):
            settings = ExtractionSettings()
            assert settings.extraction_provider == "anthropic"

    def test_temperature_is_float(self):
        """Temperature accepts float from env."""
        with patch.dict(os.environ, {"EXTRACTION_TEMPERATURE": "0.7"}):
            settings = ExtractionSettings()
            assert settings.temperature == 0.7

    def test_extra_ignore(self):
        """Unknown env vars with EXTRACTION_ prefix don't cause errors."""
        with patch.dict(os.environ, {"EXTRACTION_UNKNOWN_FIELD": "whatever"}):
            settings = ExtractionSettings()
            assert settings.max_retries == 3  # defaults still work


class TestF0015ModelEnvAlias:
    """F-0015(b) / ISS-0031: EXTRACTION_MODEL friendly alias."""

    def test_friendly_alias_works(self):
        """EXTRACTION_MODEL (single prefix) sets extraction_model."""
        with patch.dict(os.environ, {"EXTRACTION_MODEL": "claude-sonnet-5"}):
            settings = ExtractionSettings()
            assert settings.extraction_model == "claude-sonnet-5"

    def test_legacy_double_prefix_still_works(self):
        """EXTRACTION_EXTRACTION_MODEL (legacy double prefix) still works."""
        with patch.dict(
            os.environ, {"EXTRACTION_EXTRACTION_MODEL": "qwen2.5:7b"}
        ):
            settings = ExtractionSettings()
            assert settings.extraction_model == "qwen2.5:7b"

    def test_friendly_alias_wins_when_both_set(self):
        """AliasChoices order: the friendly alias takes precedence."""
        with patch.dict(
            os.environ,
            {
                "EXTRACTION_MODEL": "claude-sonnet-5",
                "EXTRACTION_EXTRACTION_MODEL": "qwen2.5:7b",
            },
        ):
            settings = ExtractionSettings()
            assert settings.extraction_model == "claude-sonnet-5"

    def test_constructor_kwarg_still_works(self):
        """populate_by_name keeps ExtractionSettings(extraction_model=...) working
        (used by eval_checkpoint.py --model)."""
        settings = ExtractionSettings(extraction_model="gpt-4.1-nano")
        assert settings.extraction_model == "gpt-4.1-nano"


class TestF0015OptionalTemperature:
    """F-0015(a) / ISS-0031: temperature is Optional; None = omit from request."""

    def test_default_stays_zero(self):
        settings = ExtractionSettings()
        assert settings.temperature == 0.0
        assert settings.verification_temperature == 0.0
        assert settings.er_temperature == 0.0

    def test_none_accepted(self):
        settings = ExtractionSettings(
            temperature=None, verification_temperature=None, er_temperature=None
        )
        assert settings.temperature is None
        assert settings.verification_temperature is None
        assert settings.er_temperature is None


class TestF0015ProviderAwareMaxOutputTokens:
    """F-0015(c) / ISS-0031: provider-aware output-token ceiling."""

    def test_default_is_none(self):
        """max_output_tokens defaults to None (= provider-aware)."""
        settings = ExtractionSettings()
        assert settings.max_output_tokens is None

    def test_ollama_keeps_4096(self):
        settings = ExtractionSettings()
        assert settings.effective_max_output_tokens("ollama") == 4096

    def test_anthropic_gets_8192(self):
        settings = ExtractionSettings()
        assert settings.effective_max_output_tokens("anthropic") == 8192

    def test_openai_gets_8192(self):
        settings = ExtractionSettings()
        assert settings.effective_max_output_tokens("openai") == 8192

    def test_explicit_value_wins_everywhere(self):
        """An explicitly configured cap overrides the provider default."""
        settings = ExtractionSettings(max_output_tokens=2048)
        assert settings.effective_max_output_tokens("ollama") == 2048
        assert settings.effective_max_output_tokens("anthropic") == 2048

    def test_env_override(self):
        with patch.dict(os.environ, {"EXTRACTION_MAX_OUTPUT_TOKENS": "16384"}):
            settings = ExtractionSettings()
            assert settings.effective_max_output_tokens("anthropic") == 16384
