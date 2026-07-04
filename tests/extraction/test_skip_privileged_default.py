"""F-0047b / ISS-0055 Layer 0 — skip_privileged_extraction defaults to True.

GrACE-Product §8 makes extraction-time exclusion the structural commitment:
privileged content should not enter the graph by default; extracting
privileged email is an explicit operator opt-in. These tests pin the flipped
default at BOTH definition sites (code-level loader + config/discovery.yaml).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import mock_open, patch

import yaml

from src.extraction.extraction_bridge import _load_skip_privileged_config

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_missing_key_defaults_to_true():
    """ingestion section without the key -> True (Layer 0 flipped default)."""
    cfg = yaml.safe_dump({"ingestion": {"sources": []}})
    with patch("builtins.open", mock_open(read_data=cfg)):
        assert _load_skip_privileged_config() is True


def test_missing_ingestion_section_defaults_to_true():
    cfg = yaml.safe_dump({"llm": {"provider": "ollama"}})
    with patch("builtins.open", mock_open(read_data=cfg)):
        assert _load_skip_privileged_config() is True


def test_unreadable_config_fails_safe_to_true():
    """Broken/missing config must not silently start extracting privileged."""
    with patch("builtins.open", side_effect=OSError("boom")):
        assert _load_skip_privileged_config() is True


def test_explicit_opt_in_false_is_honored():
    """Operators can still deliberately opt in to privileged extraction."""
    cfg = yaml.safe_dump({"ingestion": {"skip_privileged_extraction": False}})
    with patch("builtins.open", mock_open(read_data=cfg)):
        assert _load_skip_privileged_config() is False


def test_discovery_yaml_ships_true():
    """config/discovery.yaml carries the flipped default (Layer 0)."""
    cfg = yaml.safe_load((REPO_ROOT / "config" / "discovery.yaml").read_text())
    assert cfg["ingestion"]["skip_privileged_extraction"] is True
