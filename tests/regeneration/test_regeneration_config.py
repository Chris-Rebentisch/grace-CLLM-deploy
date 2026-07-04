"""Tests for regeneration_config.RegenSettings and get_regen_settings()."""

from __future__ import annotations

import pytest

from src.regeneration.regeneration_config import (
    RegenSettings,
    get_regen_settings,
    reset_regen_settings,
)


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    reset_regen_settings()
    yield
    reset_regen_settings()


def test_defaults_load_with_no_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("REGENERATION_"):
            monkeypatch.delenv(key, raising=False)
    # Hermetic: ignore the ambient .env FILE too (delenv only clears process env vars,
    # not pydantic-settings' env_file load). An operator REGENERATION_TOTAL_INPUT_BUDGET_TOKENS
    # in .env (e.g. the Claude-path budget bump) must not contaminate this code-default test.
    settings = RegenSettings(_env_file=None)
    assert settings.regeneration_model == "qwen2.5:7b"
    assert settings.regeneration_temperature == 0.2
    assert settings.total_input_budget_tokens == 3000
    assert settings.chars_per_token == 4
    assert settings.enable_claim_span_detection is True
    assert settings.span_detector_mode == "sentence_fallback"
    assert settings.debug_log_prompts is False


def test_env_var_overrides_regeneration_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGENERATION_REGENERATION_MODEL", "llama3:8b")
    settings = RegenSettings()
    assert settings.regeneration_model == "llama3:8b"


def test_env_var_total_input_budget_tokens_is_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGENERATION_TOTAL_INPUT_BUDGET_TOKENS", "4500")
    settings = RegenSettings()
    assert settings.total_input_budget_tokens == 4500
    assert isinstance(settings.total_input_budget_tokens, int)


def test_all_six_phase_style_fields_exist() -> None:
    settings = RegenSettings()
    for phase in ("prepare", "open", "structure", "clarify", "close", "none"):
        attr = f"phase_style_{phase}"
        assert hasattr(settings, attr), f"missing {attr}"
        # Must be a string (even if empty)
        assert isinstance(getattr(settings, attr), str)


def test_get_regen_settings_returns_singleton() -> None:
    a = get_regen_settings()
    b = get_regen_settings()
    assert a is b
