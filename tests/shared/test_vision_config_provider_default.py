"""Provider-aware vision defaults + loud config-degradation logging.

Covers two llm_provider fixes:
- read_vision_config_from_yaml no longer hardcodes qwen2.5-vl:32b when the
  main provider is anthropic (Claude models are vision-capable);
- read_llm_config_from_yaml logs an error-level structlog event when
  config/discovery.yaml is missing/unparseable and it falls back to Ollama
  defaults (behavior unchanged, degradation made loud).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import src.shared.llm_provider as llm_provider
from src.shared.llm_provider import (
    read_llm_config_from_yaml,
    read_vision_config_from_yaml,
)


def _with_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "discovery.yaml"
    p.write_text(content)
    return p


def test_vision_defaults_to_main_anthropic_model(tmp_path: Path) -> None:
    p = _with_yaml(
        tmp_path,
        "llm:\n  provider: anthropic\n  model: claude-haiku-4-5-20251001\n",
    )
    with patch.object(llm_provider, "_DISCOVERY_YAML", p):
        cfg = read_vision_config_from_yaml()
    assert cfg["provider"] == "anthropic"
    assert cfg["model"] == "claude-haiku-4-5-20251001"
    assert cfg["enabled"] is True


def test_vision_defaults_to_qwen_vl_when_provider_ollama(tmp_path: Path) -> None:
    p = _with_yaml(tmp_path, "llm:\n  provider: ollama\n  model: qwen2.5:7b\n")
    with patch.object(llm_provider, "_DISCOVERY_YAML", p):
        cfg = read_vision_config_from_yaml()
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen2.5-vl:32b"


def test_explicit_vision_block_wins_over_defaults(tmp_path: Path) -> None:
    p = _with_yaml(
        tmp_path,
        "llm:\n"
        "  provider: anthropic\n"
        "  model: claude-haiku-4-5-20251001\n"
        "  vision:\n"
        "    enabled: false\n"
        "    provider: ollama\n"
        "    model: qwen2.5-vl:7b\n",
    )
    with patch.object(llm_provider, "_DISCOVERY_YAML", p):
        cfg = read_vision_config_from_yaml()
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen2.5-vl:7b"
    assert cfg["enabled"] is False


def test_missing_yaml_vision_falls_back_to_ollama_shape(tmp_path: Path) -> None:
    p = tmp_path / "does-not-exist.yaml"
    with patch.object(llm_provider, "_DISCOVERY_YAML", p):
        cfg = read_vision_config_from_yaml()
    # Unresolvable main config resolves to ollama, so vision keeps the
    # local-VLM default.
    assert cfg["model"] == "qwen2.5-vl:32b"
    assert cfg["enabled"] is True


def test_missing_yaml_logs_error_and_keeps_ollama_fallback(tmp_path: Path) -> None:
    p = tmp_path / "does-not-exist.yaml"
    with patch.object(llm_provider, "_DISCOVERY_YAML", p):
        with patch.object(llm_provider, "logger") as mock_logger:
            cfg = read_llm_config_from_yaml()
    # Fallback behavior unchanged...
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen2.5:7b"
    assert cfg["airgap_mode"] is True
    # ...but the degradation is loud.
    mock_logger.error.assert_called_once()
    event = mock_logger.error.call_args.args[0]
    assert event == "discovery_yaml_missing_falling_back_to_ollama"
    assert mock_logger.error.call_args.kwargs["path"] == str(p)


def test_malformed_yaml_logs_error_and_falls_back(tmp_path: Path) -> None:
    p = _with_yaml(tmp_path, "llm: [unclosed\n  - :::")
    with patch.object(llm_provider, "_DISCOVERY_YAML", p):
        with patch.object(llm_provider, "logger") as mock_logger:
            cfg = read_llm_config_from_yaml()
    assert cfg["provider"] == "ollama"
    mock_logger.error.assert_called_once()


def test_parseable_yaml_does_not_log_error(tmp_path: Path) -> None:
    p = _with_yaml(tmp_path, "llm:\n  provider: anthropic\n  model: m\n")
    with patch.object(llm_provider, "_DISCOVERY_YAML", p):
        with patch.object(llm_provider, "logger") as mock_logger:
            cfg = read_llm_config_from_yaml()
    assert cfg["provider"] == "anthropic"
    mock_logger.error.assert_not_called()
