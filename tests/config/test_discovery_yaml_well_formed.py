"""D478: Regression test — config/discovery.yaml is well-formed.

Parses ``config/discovery.yaml`` via ``yaml.safe_load()`` and asserts
``domain_categories`` key is present and is a list.
"""

from __future__ import annotations

from pathlib import Path

import yaml


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "discovery.yaml"


def test_discovery_yaml_has_domain_categories():
    """D478 regression: domain_categories must be present and be a list."""
    assert _CONFIG_PATH.exists(), f"config/discovery.yaml not found at {_CONFIG_PATH}"
    data = yaml.safe_load(_CONFIG_PATH.read_text())
    assert isinstance(data, dict), "discovery.yaml root must be a dict"
    assert "domain_categories" in data, "domain_categories key missing from discovery.yaml"
    cats = data["domain_categories"]
    assert isinstance(cats, list), f"domain_categories must be a list, got {type(cats).__name__}"
    assert len(cats) > 0, "domain_categories must not be empty"
