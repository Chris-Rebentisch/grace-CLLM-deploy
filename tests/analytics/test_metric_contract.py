"""Golden-file contract test for `/metrics` output (spec §5.5, §5.6).

Every series the app emits must either be in the golden list or the
ignore list. If a new OTel version starts emitting metadata series we
did not anticipate, update the ignore-list in the spec and here — do
NOT loosen the assertion.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

# These instrumentation helpers land in CP4. Skip the whole module if
# they are not yet importable; CP4 un-skips automatically.
pytest.importorskip("src.analytics.llm_instrumentation")
pytest.importorskip("src.analytics.pipeline_instrumentation")

from src.analytics import otel_setup  # noqa: E402
from src.analytics.llm_instrumentation import record_llm_call  # noqa: E402
from src.analytics.pipeline_instrumentation import record_pipeline_stage  # noqa: E402
from src.api.main import app  # noqa: E402


GOLDEN_NAMES = {
    # §5.1 HTTP server (FastAPIInstrumentor, OTEL_SEMCONV_STABILITY_OPT_IN=http)
    "http_server_request_duration_seconds",
    "http_server_active_requests",
    "http_server_request_body_size_bytes",
    "http_server_response_body_size_bytes",
    # §5.2 LLM call
    "gen_ai_client_operation_duration_seconds",
    "gen_ai_client_token_usage",
    # §5.3 Pipeline stages
    "grace_pipeline_stage_duration_seconds",
    "grace_pipeline_stage_errors",  # OTel strips the _total suffix from the metric family name
    # §5.4 Placeholders
    "grace_compression_ratio",
    "grace_decompression_faithfulness",
    "grace_mine_retention_ratio",
    # Chunk 25 §5.2 — extraction triple confidence histogram
    "grace_extraction_triple_confidence",
    # Chunk 25 §5.3 — ontology entity-type distribution gauge
    "grace_ontology_entity_type_count",
    # Chunk 25 §5.4 — retrieval strategy contributions (counter, _total stripped)
    "grace_retrieval_strategy_contributions",
    # Chunk 25 §5.5 — retrieval zero-result rate (counter, _total stripped)
    "grace_retrieval_zero_results",
    # Chunk 25 §5.6 — graph-health exporter family
    "grace_graph_node_count",
    "grace_graph_edge_count",
    "grace_graph_orphan_node_count",
    "grace_graph_density",
    "grace_graph_exporter_last_success_seconds",
    # Chunk 32 — Signal A–F per-signal strength gauges (D240/D245)
    "grace_signal_a_strength",
    "grace_signal_b_strength",
    "grace_signal_c_strength",
    "grace_signal_d_strength",
    "grace_signal_e_strength",
    "grace_signal_f_strength",
    # Chunk 32 — extraction validation failures counter (_total stripped, D242)
    "grace_extraction_validation_failures",
    # Chunk 32 — Elicitation §8.4 operational metrics (registered dormant, D247)
    "grace_time_to_first_candidate_ms",
    "grace_per_decision_latency_trend",
    "grace_instrument_swap_rate",
    "grace_position_change_rate",
    "grace_mid_phase_abandonment_rate",
    "grace_schema_decision_reversal_rate",
    "grace_session_summary_edit_rate",
    # Chunk 33 — Per-pattern correlation strength gauges (D248/D250)
    "grace_correlation_extraction_quality_problem_strength",
    "grace_correlation_graph_or_index_problem_strength",
    "grace_correlation_schema_drift_per_module_strength",
    "grace_correlation_cq_regression_pre_extraction_strength",
    "grace_correlation_relationship_gap_propagation_strength",
    # D535 (amends D250): sixth correlation pattern gauge (A4 probe).
    # Invariant: GOLDEN_NAMES count change. Authorization: D535.
    "grace_correlation_ontology_constraint_conflict_strength",
    # Chunk 33 — Alert webhook fires counter (_total stripped, D249/D253)
    "grace_alert_fires",
    # Chunk 35a — Decay batch instruments (D264)
    "grace_decay_batch_rows_processed",
    "grace_decay_batch_duration_seconds",
    # Chunk 36 — ERD aggregate band gauge (D279)
    "grace_recon_erd_band_count",
    # Chunk 37 — Reconciliation cross-executive event counters (D290).
    # OTel Counter instruments may surface in /metrics with the
    # _total suffix; both forms are allowlisted.
    "grace_recon_divergence_map_generated",
    "grace_recon_divergence_map_generated_total",
    "grace_recon_documented_reality_report_generated",
    "grace_recon_documented_reality_report_generated_total",
    # Chunk 38 — Change_Directives counters (D298). OTel may surface
    # both stripped and _total forms; allowlist both.
    "grace_change_directive_created",
    "grace_change_directive_created_total",
    "grace_change_directive_transitioned",
    "grace_change_directive_transitioned_total",
    "grace_change_directive_evidence_criterion_compiled",
    "grace_change_directive_evidence_criterion_compiled_total",
    # Chunk 39 — realization telemetry counters (D307/D308).
    "grace_change_directive_metadata_edited",
    "grace_change_directive_metadata_edited_total",
    "grace_change_directive_detail_viewed",
    "grace_change_directive_detail_viewed_total",
    # Chunk 40 — Decomposition pipeline lifecycle counters (D318).
    # OTel may surface both stripped and _total forms; allowlist both.
    "grace_decomposition_runs_started",
    "grace_decomposition_runs_started_total",
    "grace_decomposition_runs_completed",
    "grace_decomposition_runs_completed_total",
    "grace_decomposition_runs_failed",
    "grace_decomposition_runs_failed_total",
    # Chunk 41 — Decomposition Layer 5/6/7 + re-run counters (D330).
    # Both stripped and _total forms allowlisted (+6 entries).
    "grace_decomposition_layer5_decisions",
    "grace_decomposition_layer5_decisions_total",
    "grace_decomposition_segmentation_maps_ratified",
    "grace_decomposition_segmentation_maps_ratified_total",
    "grace_decomposition_reruns",
    "grace_decomposition_reruns_total",
    # Chunk 42 — Permission Matrix counters (D331/D333/D337).
    # Counter-event 4:3 mapping (N2): four EventType literals, three counters.
    # `permission_cluster_decision_recorded` shares
    # `grace_permission_matrix_ratifications_total` with
    # `permission_matrix_ratified`. Both stripped and `_total` forms
    # allowlisted (+6 entries; 69 → 75).
    "grace_permission_matrix_hypotheses",
    "grace_permission_matrix_hypotheses_total",
    "grace_permission_matrix_ratifications",
    "grace_permission_matrix_ratifications_total",
    "grace_permission_drift_auto_assignments",
    "grace_permission_drift_auto_assignments_total",
    # Chunk 43 — Sensitivity Gate Compliance Surface (D344).
    # Gauge contributes 1 entry; counter contributes 2 via stripped +
    # `_total` pairing (+3 entries; 75 → 78). Registered instruments
    # 53 → 55.
    "grace_sensitivity_coverage_band_count",
    "grace_sensitivity_report_generated",
    "grace_sensitivity_report_generated_total",
    # Chunk 44 — MCP write-tool observability counters (D369).
    # Both stripped and _total forms allowlisted (+4 entries; 78 → 82).
    # Registered instruments 57 → 59.
    "grace_mcp_write_tool_calls",
    "grace_mcp_write_tool_calls_total",
    "grace_mcp_agent_scoped_calls",
    "grace_mcp_agent_scoped_calls_total",
    # Chunk 45 — Remote Support Session observability (D375).
    # Counter (stripped + _total) + UpDownCounter (no _total variant for
    # gauges/up-down-counters). +3 entries; 82 → 85.
    # Registered instruments 59 → 61.
    "grace_remote_support_requests",
    "grace_remote_support_requests_total",
    "grace_remote_support_sessions_active",
    # Chunk 47 — Signal→Proposal pipeline counters + gauge (D387/D389).
    # Counters: both stripped and _total forms (+4 entries).
    # ObservableGauge: 1 entry (no _total variant). +5 entries; 85 → 90.
    # Registered instruments 61 → 64.
    "grace_proposals_generated",
    "grace_proposals_generated_total",
    "grace_proposals_decided",
    "grace_proposals_decided_total",
    "grace_proposal_queue_depth",
    # Chunk 48 — KGCL Change Executor counters (D392/D393).
    # Counter: stripped + _total (+2 entries). Histogram: 1 entry.
    # +3 entries; 90 → 93. Registered instruments 64 → 66.
    "grace_proposals_executed",
    "grace_proposals_executed_total",
    "grace_proposal_execution_duration_seconds",
    # Chunk 49 — Earned Autonomy Calibration counters (D394–D396).
    # Two Counters: stripped + _total forms (+4 entries; 93 → 97).
    # Registered instruments 66 → 68.
    "grace_calibration_decisions_recorded",
    "grace_calibration_decisions_recorded_total",
    "grace_calibration_updater_runs",
    "grace_calibration_updater_runs_total",
    # Chunk 50 — Agent Daemon counters (D398, instruments 68→71).
    "grace_agent_ticks",
    "grace_agent_ticks_total",
    "grace_autonomous_proposals_applied",
    "grace_autonomous_proposals_applied_total",
    "grace_cooling_periods_finalized",
    "grace_cooling_periods_finalized_total",
    # Chunk 51 — Federation Infrastructure counters (D402/D404).
    # Two Counters: stripped + _total forms (+4 entries; 103 → 107).
    # Registered instruments 71 → 73.
    "grace_federation_namespace_registered",
    "grace_federation_namespace_registered_total",
    "grace_federation_entity_resolved",
    "grace_federation_entity_resolved_total",
    # Chunk 52, D406–D408 — Federation Query Routing counters/histogram.
    # One Histogram (1 entry, no _total variant) + two Counters (stripped
    # + _total forms, +4 entries) = +5 entries; 107 → 112.
    # Registered instruments 73 → 76.
    "grace_federation_query_duration_seconds",
    "grace_federation_queries",
    "grace_federation_queries_total",
    "grace_federation_result_merge",
    "grace_federation_result_merge_total",
    # Chunk 53 — Connector Framework counters/histogram (D409/D413).
    # One Counter (stripped + _total, +2 entries) + one Histogram (1 entry,
    # no _total variant) = +3 entries; 112 → 115.
    # Registered instruments 76 → 78.
    "grace_connector_sync_records",
    "grace_connector_sync_records_total",
    "grace_connector_sync_duration_seconds",
    # Chunk 61 — Communication Ingestion observability (D428).
    # Six Counters (stripped + _total forms, +12 entries) + one Histogram
    # (1 entry, no _total variant) = +13 entries; 115 → 128.
    # Chunk 67 adds +2 entries (D453); 128 → 130.
    # Registered instruments 78 → 85.
    "grace_ingestion_runs_started",
    "grace_ingestion_runs_started_total",
    "grace_ingestion_runs_completed",
    "grace_ingestion_runs_completed_total",
    "grace_ingestion_runs_failed",
    "grace_ingestion_runs_failed_total",
    "grace_ingestion_emails_processed",
    "grace_ingestion_emails_processed_total",
    "grace_ingestion_source_error",
    "grace_ingestion_source_error_total",
    "grace_voice_tone_profiles_generated",
    "grace_voice_tone_profiles_generated_total",
    "grace_ingestion_triage_duration_seconds",
    # Chunk 67 (D453 honest mutation counter). GOLDEN_NAMES 128 → 130.
    # Invariant: GOLDEN_NAMES count change. Authorization: D453.
    "grace_decay_batch_rows_actually_mutated",
    "grace_decay_batch_rows_actually_mutated_total",
    # Chunk 72a (D469 extraction job lifecycle). GOLDEN_NAMES 130 → 136.
    # Invariant: GOLDEN_NAMES count change. Authorization: D469.
    "grace_extraction_jobs_started",
    "grace_extraction_jobs_started_total",
    "grace_extraction_jobs_completed",
    "grace_extraction_jobs_completed_total",
    "grace_extraction_jobs_failed",
    "grace_extraction_jobs_failed_total",
    # Chunk 73 (D477 legacy enum tolerance counter). GOLDEN_NAMES 136 → 138.
    # Invariant: GOLDEN_NAMES count change. Authorization: D477.
    "grace_proposal_type_legacy_alias_resolved",
    "grace_proposal_type_legacy_alias_resolved_total",
    # Chunk 77b (D502 image/vision observability). GOLDEN_NAMES 138 → 143.
    # Invariant: GOLDEN_NAMES count change. Authorization: D502.
    "grace_image_assets_ingested",
    "grace_image_assets_ingested_total",
    "grace_vision_calls",
    "grace_vision_calls_total",
    "grace_vision_call_duration_seconds",
    # Chunk 78 (D506 Voice Card export counter). GOLDEN_NAMES 143 → 145.
    # Invariant: GOLDEN_NAMES count change. Authorization: D506.
    "grace_voice_cards_exported",
    "grace_voice_cards_exported_total",
    # Chunk 79 (D508 Email Extraction Bridge counter). GOLDEN_NAMES 145 → 147.
    # Invariant: GOLDEN_NAMES count change. Authorization: D508.
    "grace_email_extracted",
    "grace_email_extracted_total",
    # Chunk 80a (D513 Thread Reconstruction counter). GOLDEN_NAMES 147 → 149.
    # Invariant: GOLDEN_NAMES count change. Authorization: D513.
    "grace_email_thread_reconstructed",
    "grace_email_thread_reconstructed_total",
    # Chunk 80b (D517 Corroboration promotion counter). GOLDEN_NAMES 149 → 151.
    # Invariant: GOLDEN_NAMES count change. Authorization: D517.
    "grace_corroboration_promotions",
    "grace_corroboration_promotions_total",
}

# Exact names that are allowed but not part of the GrACE contract.
IGNORE_EXACT = {"target_info", "up"}

# Prefixes that are allowed.
# `otel_sdk_` covers `otel_sdk_span_started`/`_live` emitted automatically by
# the OTel Python SDK starting in ~1.41; these are internal span-lifecycle
# counters, not part of the GrACE metric contract.
IGNORE_PREFIXES = ("otel_scope_", "scrape_", "otel_sdk_")

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _is_ignored(name: str) -> bool:
    if name in IGNORE_EXACT:
        return True
    # F-0034 (validation run, 2026-07-03): the subprocess-metrics mirror
    # re-exposes CLI-subprocess HISTOGRAM families as exact-named monotonic
    # series `<base>_count` / `<base>_sum` via the multiproc collector. When
    # the base family is already golden, its mirrored forms are contract-clean
    # — anything with an unknown base still fails the contract.
    for suffix in ("_count", "_sum"):
        if name.endswith(suffix) and name[: -len(suffix)] in GOLDEN_NAMES:
            return True
    return any(name.startswith(p) for p in IGNORE_PREFIXES)


def _scrape_metrics_names(client: TestClient) -> set[str]:
    response = client.get("/metrics")
    assert response.status_code == 200
    families = text_string_to_metric_families(response.text)
    return {fam.name for fam in families}


@pytest.fixture
def client():
    otel_setup._initialized = False
    otel_setup.setup_otel(app)
    with TestClient(app) as c:
        yield c


@pytest.mark.asyncio
async def test_metric_contract_golden_list_match(client):
    """Metric names emitted must be a subset of (golden ∪ ignore)."""
    # HTTP metrics: hit any GET endpoint that FastAPIInstrumentor sees.
    client.get("/openapi.json")

    # LLM metric: one mocked call through record_llm_call.
    async with record_llm_call(
        system="ollama",
        model="qwen2.5:7b",
        grace_module="extraction",
        grace_operation="extract",
    ) as ctx:
        ctx.set_input_tokens(10)
        ctx.set_output_tokens(20)

    # Pipeline stage metric: one mocked stage.
    async with record_pipeline_stage(pipeline="extraction", stage="chunk"):
        pass

    names = _scrape_metrics_names(client)

    # No raw UUIDs or untemplated paths leak into metric names.
    for name in names:
        assert not UUID_RE.search(name), f"UUID leaked into metric name: {name}"

    unknown = {n for n in names if n not in GOLDEN_NAMES and not _is_ignored(n)}
    assert not unknown, (
        f"Unknown metric names in /metrics output (not golden, not ignored): "
        f"{sorted(unknown)}"
    )

    # Everything we just exercised should be present.
    exercised = {
        "http_server_request_duration_seconds",
        "gen_ai_client_operation_duration_seconds",
        "gen_ai_client_token_usage",
        "grace_pipeline_stage_duration_seconds",
    }
    missing = exercised - names
    assert not missing, f"Exercised metrics missing from /metrics: {sorted(missing)}"


def test_placeholder_registration_surface(client):
    """Placeholder metrics are silent until a consumer records to them.

    Histograms (`grace_compression_ratio`, `grace_decompression_faithfulness`)
    lazy-materialize on first `.record()`. Observable gauges
    (`grace_mine_retention_ratio`) require a non-empty callback observation.

    Chunk 34 (D260) introduces the first in-tree consumer of
    `grace_decompression_faithfulness` (the DeepEval runner) and the
    first set-callback for `grace_mine_retention_ratio` (the MINE
    emitter). When the metric_contract module is imported in isolation
    (no prior runner / emitter call in the same process), all three
    are silent. When the same process has previously exercised
    EvalRunner or the MINE emitter (e.g. via `tests/eval/`), the
    histogram / gauge will be materialized — that is expected and
    GOLDEN_NAMES (still 40) keeps them allowlisted.

    This test guards the silent-on-fresh-process posture: only
    `grace_compression_ratio` is asserted unconditionally because no
    in-tree code records to it. The other two placeholders are
    deliberately not asserted-absent post-CP4 (D260).
    """
    names = _scrape_metrics_names(client)

    # Histograms lazy-materialize on first .record(); no in-tree consumer
    # currently records to grace_compression_ratio (compression-side stays
    # in legacy code per D147 — Chunk 34 only wires the decompression side).
    assert "grace_compression_ratio" not in names
