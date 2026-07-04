"""D252 raw-metric allowlist guard (CP3 / FAIL-gate #8).

Asserts that every metric family in the correlation engine's
``raw_metric_allowlist`` is also in the GOLDEN_NAMES contract — i.e.
detectors can only query Prometheus for metrics the GrACE app actually
emits, not arbitrary external series.
"""

from __future__ import annotations

import yaml

from src.analytics.correlation_engine.config import CorrelationEngineConfig, load_config
from tests.analytics.test_metric_contract import GOLDEN_NAMES


def test_every_allowlist_entry_is_in_golden_names():
    """raw_metric_allowlist ⊆ GOLDEN_NAMES."""
    cfg = load_config()
    missing = [name for name in cfg.raw_metric_allowlist if name not in GOLDEN_NAMES]
    assert not missing, (
        "raw_metric_allowlist contains metrics that are not in the GOLDEN_NAMES "
        f"contract (D173 co-commit violation): {missing}"
    )


def test_default_allowlist_matches_d252():
    """Defaults match the four metrics named in D252."""
    expected = {
        "grace_retrieval_strategy_contributions",
        "grace_retrieval_zero_results",
        "http_server_request_duration_seconds",
        "grace_extraction_triple_confidence",
    }
    cfg = CorrelationEngineConfig()
    assert set(cfg.raw_metric_allowlist) == expected
