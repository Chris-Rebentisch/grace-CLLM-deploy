"""TriageConfig validation + YAML loader tests (Chunk 56 CP1)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from src.ingestion.communications.triage.config import (
    Tier1Config,
    Tier3Config,
    TriageConfig,
    load_triage_config,
)


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "triage_rules.yaml"
    p.write_text(yaml.dump(data, default_flow_style=False))
    return p


def test_load_triage_config_roundtrip(tmp_path: Path):
    """load_triage_config round-trips a valid YAML file."""
    data = {
        "tier1": {
            "rule_order": [
                "duplicate_message_id",
                "auto_reply",
                "newsletter",
                "calendar_invite",
                "bounce",
                "system_notification",
                "empty_body",
            ],
        },
        "tier3": {"threshold": 0.30, "batch_size": 50},
    }
    path = _write_yaml(tmp_path, data)
    cfg = load_triage_config(path)
    assert isinstance(cfg, TriageConfig)
    assert cfg.tier1.rule_order[0] == "duplicate_message_id"
    assert cfg.tier3.threshold == 0.30
    assert cfg.tier3.batch_size == 50


def test_tier1_config_rejects_unknown_rule():
    """Tier1Config raises ValidationError on unknown rule name in rule_order."""
    with pytest.raises(Exception, match="Unknown Tier 1 rule names"):
        Tier1Config(rule_order=["duplicate_message_id", "magic_filter"])


def test_tier3_config_rejects_threshold_above_one():
    """Tier3Config rejects threshold > 1.0."""
    with pytest.raises(Exception):
        Tier3Config(threshold=1.5)


def test_empty_body_min_chars_default():
    """EmptyBodyConfig defaults min_chars_after_html_strip to 20."""
    data = {
        "tier1": {
            "rule_order": ["empty_body"],
        },
    }
    cfg = TriageConfig.model_validate(data)
    assert cfg.tier1.empty_body.min_chars_after_html_strip == 20
