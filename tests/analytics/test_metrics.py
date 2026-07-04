"""Tests for `src.analytics.metrics` instrument registration."""

from __future__ import annotations

from opentelemetry.metrics import CallbackOptions

from src.analytics import metrics


def test_all_instruments_are_registered_and_record_without_error():
    """Every instrument in the catalog should exist and accept one observation."""
    metrics.llm_call_duration.record(
        1.0,
        attributes={
            "gen_ai_system": "ollama",
            "gen_ai_request_model": "qwen2.5:7b",
            "gen_ai_operation_name": "chat",
            "grace_module": "extraction",
            "grace_operation": "extract",
        },
    )
    metrics.llm_token_usage.record(
        42,
        attributes={
            "gen_ai_system": "ollama",
            "gen_ai_request_model": "qwen2.5:7b",
            "gen_ai_token_type": "input",
            "grace_module": "extraction",
        },
    )
    metrics.pipeline_stage_duration.record(
        0.5,
        attributes={"pipeline": "extraction", "stage": "chunk", "status": "ok"},
    )
    metrics.pipeline_stage_errors.add(
        1,
        attributes={
            "pipeline": "extraction",
            "stage": "chunk",
            "error_type": "RuntimeError",
        },
    )

    assert metrics.compression_ratio is not None
    assert metrics.decompression_faithfulness is not None
    assert metrics.mine_retention_ratio is not None


def test_mine_retention_callback_returns_empty_iterable():
    """The placeholder gauge callback yields nothing until Chunk 34 fills it in."""
    observations = list(metrics._mine_retention_callback(CallbackOptions()))
    assert observations == []


def test_chunk25_amendment_instruments_are_registered():
    """Chunk 25 §5 amendments: 6 instrument objects covering 9 metric families.

    Graph-health gauges are five separate objects; counters/histogram/gauge
    for the other four are module-level names on ``metrics``.
    """
    assert metrics.extraction_triple_confidence is not None
    assert metrics.ontology_entity_type_count is not None
    assert metrics.retrieval_strategy_contributions is not None
    assert metrics.retrieval_zero_results is not None
    assert metrics.graph_node_count is not None
    assert metrics.graph_edge_count is not None
    assert metrics.graph_orphan_node_count is not None
    assert metrics.graph_density is not None
    assert metrics.graph_exporter_last_success_seconds is not None

    metrics.extraction_triple_confidence.record(
        0.72, attributes={"ontology_module": "corporate", "verdict": "supported"}
    )
    metrics.ontology_entity_type_count.set(
        5, attributes={"ontology_module": "corporate", "entity_type": "Company"}
    )
    metrics.retrieval_strategy_contributions.add(
        1, attributes={"strategy": "graph", "fused_rank_bucket": "top5"}
    )
    metrics.retrieval_zero_results.add(1)
    metrics.graph_node_count.set(
        100, attributes={"entity_type": "Company"}
    )
    metrics.graph_edge_count.set(
        50, attributes={"relationship_type": "OWNS"}
    )
    metrics.graph_orphan_node_count.set(3)
    metrics.graph_density.set(0.42)
    metrics.graph_exporter_last_success_seconds.set(1_700_000_000.0)
