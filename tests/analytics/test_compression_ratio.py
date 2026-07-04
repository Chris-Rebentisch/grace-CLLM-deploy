"""Tests for ``src.analytics.compression_ratio`` (spec §10.1)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.analytics import compression_ratio as cr


def test_happy_path_records_single_observation():
    """1000 source_tokens / (5+10) entities+relationships = 66.67."""
    mock_hist = MagicMock()
    with patch("src.analytics.compression_ratio.grace_metrics") as m:
        m.compression_ratio = mock_hist
        cr.record_compression_ratio(
            source_tokens=1000,
            entities=5,
            relationships=10,
            ontology_module="corporate",
            event_id="evt-1",
            document_id="doc-1",
        )
    mock_hist.record.assert_called_once()
    value, kwargs = mock_hist.record.call_args[0][0], mock_hist.record.call_args[1]
    assert abs(value - (1000 / 15)) < 1e-6
    assert kwargs["attributes"] == {"ontology_module": "corporate"}


def test_zero_denominator_skipped_and_logged():
    """entities=0, relationships=0 => no observation, structlog info emitted."""
    mock_hist = MagicMock()
    mock_log = MagicMock()
    with patch("src.analytics.compression_ratio.grace_metrics") as m, \
         patch("src.analytics.compression_ratio.log", mock_log):
        m.compression_ratio = mock_hist
        cr.record_compression_ratio(
            source_tokens=500,
            entities=0,
            relationships=0,
            ontology_module="corporate",
            event_id="evt-2",
            document_id="doc-2",
        )
    mock_hist.record.assert_not_called()
    mock_log.info.assert_called_once()
    assert mock_log.info.call_args[0][0] == "compression_ratio.skipped_zero_denominator"


def test_ontology_module_none_maps_to_unknown():
    """ontology_module=None => label value is 'unknown', never '_init'."""
    mock_hist = MagicMock()
    with patch("src.analytics.compression_ratio.grace_metrics") as m:
        m.compression_ratio = mock_hist
        cr.record_compression_ratio(
            source_tokens=800,
            entities=2,
            relationships=2,
            ontology_module=None,
        )
    mock_hist.record.assert_called_once()
    attrs = mock_hist.record.call_args[1]["attributes"]
    assert attrs["ontology_module"] == "unknown"
    assert attrs["ontology_module"] != "_init"
