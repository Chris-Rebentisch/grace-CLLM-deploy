"""GrACE metric catalog (OTel Meter API only).

Every custom metric in GrACE is created here via `meter.create_*()`
and imported by instrumentation code. Nothing in GrACE creates
`prometheus_client.Counter`, `Histogram`, or `Gauge` directly — the
PrometheusMetricReader is the one-way bridge (D150).

§5.4 placeholders (`grace_compression_ratio`,
`grace_decompression_faithfulness`, `grace_mine_retention_ratio`)
are registered here but silent in Chunk 24. Observed in Chunks 25/34.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import CallbackOptions, Observation

if TYPE_CHECKING:
    pass


_LLM_DURATION_BUCKETS = [0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300]
_STAGE_DURATION_BUCKETS = [0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120]
_CONFIDENCE_BUCKETS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


_meter = otel_metrics.get_meter("grace.analytics")


llm_call_duration = _meter.create_histogram(
    name="gen_ai_client_operation_duration_seconds",
    unit="s",
    description="Duration of LLM client operations.",
    explicit_bucket_boundaries_advisory=_LLM_DURATION_BUCKETS,
)

llm_token_usage = _meter.create_histogram(
    name="gen_ai_client_token_usage",
    unit="{token}",
    description="Tokens consumed or produced by LLM client operations.",
)

pipeline_stage_duration = _meter.create_histogram(
    name="grace_pipeline_stage_duration_seconds",
    unit="s",
    description="Duration of GrACE pipeline stages.",
    explicit_bucket_boundaries_advisory=_STAGE_DURATION_BUCKETS,
)

pipeline_stage_errors = _meter.create_counter(
    name="grace_pipeline_stage_errors_total",
    unit="{error}",
    description="Total errors raised inside GrACE pipeline stages.",
)

compression_ratio = _meter.create_histogram(
    name="grace_compression_ratio",
    unit="1",
    description=(
        "Ratio of compressed subgraph size to source text size. "
        "Registered in Chunk 24; populated by Chunk 25."
    ),
)

decompression_faithfulness = _meter.create_histogram(
    name="grace_decompression_faithfulness",
    unit="1",
    description=(
        "Faithfulness score for text regenerated from a subgraph. "
        "Observed via DeepEval FaithfulnessMetric inside the eval runner "
        "(D260)."
    ),
    explicit_bucket_boundaries_advisory=_CONFIDENCE_BUCKETS,
)


# D260: MINE retention observable gauge. Chunk 34 wires the callback to
# read from a shared in-process registry mutated by ``MINESampler`` via
# ``src.extraction.mine_emitter.set_mine_retention_observation``. Labels:
# ``ontology_module``, ``schema_version_id`` (D162-capped Top-N+_other_
# is enforced by the emitter, not here). Reading the registry in the
# callback keeps observation lock-free at scrape time.
_MINE_RETENTION_OBSERVATIONS: dict[tuple[str, str], float] = {}


def _mine_retention_callback(options: CallbackOptions) -> Iterable[Observation]:
    """Emit one Observation per (ontology_module, schema_version_id) pair
    populated by the latest MINE sampling run. Empty until the first run.
    """
    snapshot = list(_MINE_RETENTION_OBSERVATIONS.items())
    return [
        Observation(
            value=value,
            attributes={
                "ontology_module": ontology_module,
                "schema_version_id": schema_version_id,
            },
        )
        for (ontology_module, schema_version_id), value in snapshot
    ]


mine_retention_ratio = _meter.create_observable_gauge(
    name="grace_mine_retention_ratio",
    callbacks=[_mine_retention_callback],
    unit="1",
    description=(
        "MINE fact-retention ratio per sample batch. Observed via the "
        "in-process registry mutated by "
        "src.extraction.mine_emitter.set_mine_retention_observation "
        "(D260)."
    ),
)


# Chunk 25 amendments (§5). All OTel Meter API; never prometheus_client direct.

extraction_triple_confidence = _meter.create_histogram(
    name="grace_extraction_triple_confidence",
    unit="1",
    description=(
        "Per-triple confidence at verification time. "
        "Labels: ontology_module, verdict."
    ),
    explicit_bucket_boundaries_advisory=_CONFIDENCE_BUCKETS,
)

ontology_entity_type_count = _meter.create_gauge(
    name="grace_ontology_entity_type_count",
    unit="1",
    description=(
        "Schema type presence per ontology module (D176). Value=1 per "
        "currently-promoted (ontology_module, entity_type) pair for head "
        "types; value=tail count for _other_. This is schema-type PRESENCE, "
        "NOT graph-instance population — do not read this metric as counts "
        "of entities in the graph. Gauge replaced-in-full on schema "
        "promotion; labels for retired types explicitly cleared. Labels: "
        "ontology_module, entity_type (top-N + _other_)."
    ),
)

retrieval_strategy_contributions = _meter.create_counter(
    name="grace_retrieval_strategy_contributions_total",
    unit="{contribution}",
    description=(
        "Per-result increments at RRF fusion, one per contributing "
        "strategy. Labels: strategy (graph/semantic/bm25/temporal), "
        "fused_rank_bucket (top1/top5/top10/tail)."
    ),
)

retrieval_zero_results = _meter.create_counter(
    name="grace_retrieval_zero_results_total",
    unit="{query}",
    description=(
        "Per-query increment when len(results) == 0 at end of retrieval "
        "pipeline. No labels."
    ),
)

graph_node_count = _meter.create_gauge(
    name="grace_graph_node_count",
    unit="1",
    description=(
        "Graph vertex count by entity_type (top-N + _other_). "
        "Published by graph_health_exporter."
    ),
)

graph_edge_count = _meter.create_gauge(
    name="grace_graph_edge_count",
    unit="1",
    description=(
        "Graph edge count by relationship_type (top-N + _other_). "
        "Published by graph_health_exporter."
    ),
)

graph_orphan_node_count = _meter.create_gauge(
    name="grace_graph_orphan_node_count",
    unit="1",
    description="Graph orphan vertex count scalar. Published by graph_health_exporter.",
)

graph_density = _meter.create_gauge(
    name="grace_graph_density",
    unit="1",
    description=(
        "Graph density scalar (total_edges / total_vertices). "
        "Published by graph_health_exporter."
    ),
)

graph_exporter_last_success_seconds = _meter.create_gauge(
    name="grace_graph_exporter_last_success_seconds",
    unit="s",
    description=(
        "Unix timestamp of the last successful graph_health_exporter "
        "tick. Grafana uses it to visibly degrade if the exporter is "
        "stuck. Updated on success only."
    ),
)


# Chunk 32 amendments (D240/D242/D244/D247). All OTel Meter API.

# Per-signal strength gauges (Signals A–F). One per signal, label
# ``ontology_module``. Surfaced by the orchestrator and Grafana
# ``grace-signals-overview`` dashboard.

signal_a_strength = _meter.create_gauge(
    name="grace_signal_a_strength",
    unit="1",
    description=(
        "Signal A (Missing types) strength in [0, 1] per ontology_module. "
        "Computed by signal_pipeline orchestrator from rising "
        "INSUFFICIENT-verdict extraction rate (D241/D245)."
    ),
)

signal_b_strength = _meter.create_gauge(
    name="grace_signal_b_strength",
    unit="1",
    description=(
        "Signal B (Co-occurrence Without Edge) strength in [0, 1] per "
        "ontology_module. Extraction-time co-occurrence proxy (D241)."
    ),
)

signal_c_strength = _meter.create_gauge(
    name="grace_signal_c_strength",
    unit="1",
    description=(
        "Signal C (Type drift) strength in [0, 1] per ontology_module. "
        "Constraint-violation rate spike (D241/D242/D245)."
    ),
)

signal_d_strength = _meter.create_gauge(
    name="grace_signal_d_strength",
    unit="1",
    description=(
        "Signal D (Deprecation) strength in [0, 1] per ontology_module. "
        "Mann-Kendall decreasing trend on entity-type usage (D245)."
    ),
)

signal_e_strength = _meter.create_gauge(
    name="grace_signal_e_strength",
    unit="1",
    description=(
        "Signal E (Domain/Range violation) strength in [0, 1] per "
        "ontology_module. Constraint-violation rate spike (D242/D245)."
    ),
)

signal_f_strength = _meter.create_gauge(
    name="grace_signal_f_strength",
    unit="1",
    description=(
        "Signal F (CQ-driven gaps) strength in [0, 1] per ontology_module. "
        "CQ-test-failure-spike proxy via Mann-Kendall (D243/D245)."
    ),
)

# Shared C/E validation counter. Labels: kind, ontology_module,
# entity_type. Emitted from extraction_pipeline._validate_batch site
# after temporal tagging. ERROR-severity violations only (D242). The
# entity_type label is Top-N + ``_other_`` capped (D162).
extraction_validation_failures = _meter.create_counter(
    name="grace_extraction_validation_failures_total",
    unit="{violation}",
    description=(
        "ERROR-severity constraint violations per claim, labeled by "
        "kind (rule name), ontology_module, entity_type. Counter; "
        "emit suffix ``_total`` is stripped by Prometheus exposition. "
        "Sources Signal C and Signal E (D242)."
    ),
)

# Elicitation §8.4 operational metrics. Registered dormant in Chunk 32
# (D247 plumbing for ``trust_miscalibration``); populated by future
# elicitation instrumentation work. All carry the ``ontology_module``
# label.

time_to_first_candidate_ms = _meter.create_histogram(
    name="grace_time_to_first_candidate_ms",
    unit="ms",
    description=(
        "Elicitation §8.4: latency to first CQ candidate per session, "
        "in milliseconds. Registered dormant in Chunk 32; populated by "
        "elicitation instrumentation in a later chunk."
    ),
)

per_decision_latency_trend = _meter.create_gauge(
    name="grace_per_decision_latency_trend",
    unit="1",
    description=(
        "Elicitation §8.4: per-session decision-latency trend metric. "
        "Registered dormant in Chunk 32."
    ),
)

instrument_swap_rate = _meter.create_gauge(
    name="grace_instrument_swap_rate",
    unit="1",
    description=(
        "Elicitation §8.4: rate of instrument swaps per session. "
        "Registered dormant in Chunk 32."
    ),
)

position_change_rate = _meter.create_gauge(
    name="grace_position_change_rate",
    unit="1",
    description=(
        "Elicitation §8.4: rate of card-sort position changes per "
        "session. Registered dormant in Chunk 32 (D247)."
    ),
)

mid_phase_abandonment_rate = _meter.create_gauge(
    name="grace_mid_phase_abandonment_rate",
    unit="1",
    description=(
        "Elicitation §8.4: rate of mid-phase abandonment per session. "
        "Registered dormant in Chunk 32."
    ),
)

schema_decision_reversal_rate = _meter.create_gauge(
    name="grace_schema_decision_reversal_rate",
    unit="1",
    description=(
        "Elicitation §8.4: rate of schema-decision reversals per "
        "session. Registered dormant in Chunk 32."
    ),
)

session_summary_edit_rate = _meter.create_gauge(
    name="grace_session_summary_edit_rate",
    unit="1",
    description=(
        "Elicitation §8.4: rate of session-summary edits per session. "
        "Registered dormant in Chunk 32."
    ),
)


# Chunk 33 amendments (D248/D250/D253/D162). All OTel Meter API.

# Per-pattern correlation strength gauges (D250). One per pattern,
# label ``ontology_module``. Surfaced by the orchestrator and Grafana
# ``grace-correlations-overview`` dashboard.

correlation_extraction_quality_problem_strength = _meter.create_gauge(
    name="grace_correlation_extraction_quality_problem_strength",
    unit="1",
    description=(
        "extraction_quality_problem correlation strength in [0, 1] per "
        "ontology_module. Hybrid threshold + Mann-Kendall (D251)."
    ),
)

correlation_graph_or_index_problem_strength = _meter.create_gauge(
    name="grace_correlation_graph_or_index_problem_strength",
    unit="1",
    description=(
        "graph_or_index_problem correlation strength in [0, 1] per "
        "ontology_module. Hybrid threshold + Mann-Kendall (D251)."
    ),
)

correlation_schema_drift_per_module_strength = _meter.create_gauge(
    name="grace_correlation_schema_drift_per_module_strength",
    unit="1",
    description=(
        "schema_drift_per_module correlation strength in [0, 1] per "
        "ontology_module. Hybrid threshold + Mann-Kendall (D251)."
    ),
)

correlation_cq_regression_pre_extraction_strength = _meter.create_gauge(
    name="grace_correlation_cq_regression_pre_extraction_strength",
    unit="1",
    description=(
        "cq_regression_pre_extraction correlation strength in [0, 1] per "
        "ontology_module. Hybrid threshold + Mann-Kendall (D251)."
    ),
)

correlation_relationship_gap_propagation_strength = _meter.create_gauge(
    name="grace_correlation_relationship_gap_propagation_strength",
    unit="1",
    description=(
        "relationship_gap_propagation correlation strength in [0, 1] per "
        "ontology_module. Hybrid threshold + Mann-Kendall (D251)."
    ),
)

# D535 (amends D250): sixth correlation pattern. Capture-the-why (D356):
# D250 locked the pattern catalog at five; D535 adds the A4-surfaced
# ontology_constraint_conflict pattern (Signal E + Signal B same module).
correlation_ontology_constraint_conflict_strength = _meter.create_gauge(
    name="grace_correlation_ontology_constraint_conflict_strength",
    unit="1",
    description=(
        "ontology_constraint_conflict correlation strength in [0, 1] per "
        "ontology_module. Pure-DB conjunction of Signal E (domain/range "
        "violation) + Signal B (missing edge) (D535)."
    ),
)

# Alert webhook fires counter (D249/D253/D162). Labels: alertname,
# severity, ontology_module, state. The ``alertname`` label is Top-N +
# ``_other_`` capped (D162) by the webhook handler.
alert_fires = _meter.create_counter(
    name="grace_alert_fires_total",
    unit="{fire}",
    description=(
        "Total alert webhook deliveries from Grafana Unified Alerting "
        "(D249/D253). Labels: alertname (Top-N+_other_, D162), severity, "
        "ontology_module, state."
    ),
)


# Chunk 35a amendments (D264 decay batch observability). All OTel Meter API.

decay_batch_rows_processed = _meter.create_counter(
    name="grace_decay_batch_rows_processed",
    unit="{row}",
    description=(
        "Total entity/edge rows processed by the confidence decay batch. "
        "Labels: verdict (SUPPORTED/INSUFFICIENT/REFUTED), ontology_module. "
        "Emitted by confidence_decay.decay_run() (D264)."
    ),
)

# Chunk 67 amendment (D453 honest mutation counter). Invariant:
# GOLDEN_NAMES 128→130. Authorization: D453.
decay_batch_rows_actually_mutated = _meter.create_counter(
    name="grace_decay_batch_rows_actually_mutated",
    description="Rows where extraction_confidence changed beyond epsilon",
    unit="1",
)

decay_batch_duration = _meter.create_histogram(
    name="grace_decay_batch_duration_seconds",
    unit="s",
    description=(
        "Duration of the confidence decay batch run. "
        "Emitted by confidence_decay.decay_run() (D264)."
    ),
    explicit_bucket_boundaries_advisory=_STAGE_DURATION_BUCKETS,
)


# Chunk 36 amendments (D279 ERD band observability). All OTel Meter API.

recon_erd_band_count = _meter.create_up_down_counter(
    name="grace_recon_erd_band_count",
    unit="1",
    description=(
        "Aggregate count of sessions per ERD band. "
        "Labels: band (high/medium/low). "
        "Emitted by recon_routes.generate_gap_report() (D279)."
    ),
)


# Chunk 37 amendments (D290 Reconciliation cross-executive observability).
# All OTel Meter API. Aggregate-only labels — no per-reviewer dimensions
# (D279 leaderboard ban applies transitively).

recon_divergence_map_generated_total = _meter.create_counter(
    name="grace_recon_divergence_map_generated_total",
    unit="1",
    description=(
        "Cross-Executive Divergence Map generation events. "
        "Labels: outcome (success/error). "
        "Emitted by recon_routes.generate_divergence_map() (D290)."
    ),
)


recon_documented_reality_report_generated_total = _meter.create_counter(
    name="grace_recon_documented_reality_report_generated_total",
    unit="1",
    description=(
        "Documented Reality Report generation events. "
        "Labels: trigger (scheduled/on_demand), outcome (success/error). "
        "Emitted by recon_routes.generate_documented_reality_report() and "
        "documented_reality.run_scheduled_report() (D290)."
    ),
)


# D298 — Chunk 38 Change_Directives counters.
change_directive_created_total = _meter.create_counter(
    name="grace_change_directive_created_total",
    unit="1",
    description=(
        "Change Directive creation events. "
        "Labels: tier (Operational_Adjustment / Strategic_Initiative), "
        "outcome (success/error). Emitted by change_directives.routes.create_directive (D298)."
    ),
)


change_directive_transitioned_total = _meter.create_counter(
    name="grace_change_directive_transitioned_total",
    unit="1",
    description=(
        "Change Directive state-transition events. "
        "Labels: from_state, to_state. Emitted by change_directives.routes.transition_directive (D298)."
    ),
)


change_directive_metadata_edited_total = _meter.create_counter(
    name="grace_change_directive_metadata_edited_total",
    unit="1",
    description=(
        "Chunk 39 D307 — draft metadata PATCH edits (unlabeled counter)."
    ),
)

change_directive_detail_viewed_total = _meter.create_counter(
    name="grace_change_directive_detail_viewed_total",
    unit="1",
    description=(
        "Chunk 39 D308 — change-directive detail page views (unlabeled)."
    ),
)

change_directive_evidence_criterion_compiled_total = _meter.create_counter(
    name="grace_change_directive_evidence_criterion_compiled_total",
    unit="1",
    description=(
        "EvidenceCriterion compile attempts. "
        "Labels: compilation_status (proposed/approved/manually_authored), "
        "outcome (success/error). Emitted by change_directives.routes.create_criterion (D298)."
    ),
)

# Chunk 40, D318 — decomposition pipeline lifecycle counters.
grace_decomposition_runs_started_total = _meter.create_counter(
    name="grace_decomposition_runs_started_total",
    unit="1",
    description=(
        "Decomposition pipeline runs started. "
        "Label dim: archive_root_hash (D318)."
    ),
)

grace_decomposition_runs_completed_total = _meter.create_counter(
    name="grace_decomposition_runs_completed_total",
    unit="1",
    description=(
        "Decomposition pipeline runs that reached the completed "
        "lifecycle state. Label dim: archive_root_hash (D318)."
    ),
)

grace_decomposition_runs_failed_total = _meter.create_counter(
    name="grace_decomposition_runs_failed_total",
    unit="1",
    description=(
        "Decomposition pipeline runs that failed or paused with error. "
        "Label dim: archive_root_hash (D318)."
    ),
)

# Chunk 41, D330 — decomposition Layer 5/6/7 + re-run counters.
grace_decomposition_layer5_decisions_total = _meter.create_counter(
    name="grace_decomposition_layer5_decisions_total",
    unit="1",
    description=(
        "Layer 5 Structured Interview decisions recorded. "
        "Label dim: decision_kind (one of accepted_segmented, "
        "accepted_null, rerun_finer, rerun_coarser, "
        "reject_all_reformulate). D330."
    ),
)

grace_decomposition_segmentation_maps_ratified_total = _meter.create_counter(
    name="grace_decomposition_segmentation_maps_ratified_total",
    unit="1",
    description=(
        "Layer 7 Segmentation Maps ratified. "
        "Label dim: null_hypothesis_accepted (true/false). D330."
    ),
)

grace_decomposition_reruns_total = _meter.create_counter(
    name="grace_decomposition_reruns_total",
    unit="1",
    description=(
        "+/-1.5x resolution re-run successors created. "
        "Label dim: direction (finer/coarser). D330."
    ),
)


def record_decomposition_layer5_decision(*, decision_kind: str) -> None:
    """Increment ``grace_decomposition_layer5_decisions_total``.

    Best-effort; metric path must never fail the route. Pinned to the
    five D320 ``decision_kind`` values; callers that pass an unknown
    value still get the counter incremented under that label (the
    Pydantic Literal at the route layer is the typing fence).
    """
    try:
        grace_decomposition_layer5_decisions_total.add(
            1, {"decision_kind": decision_kind}
        )
    except Exception:  # noqa: BLE001
        pass


def record_decomposition_segmentation_map_ratified(
    *, null_hypothesis_accepted: bool
) -> None:
    """Increment ``grace_decomposition_segmentation_maps_ratified_total``.

    Best-effort; never fails the route.
    """
    try:
        grace_decomposition_segmentation_maps_ratified_total.add(
            1, {"null_hypothesis_accepted": str(bool(null_hypothesis_accepted)).lower()}
        )
    except Exception:  # noqa: BLE001
        pass


def record_decomposition_rerun(*, direction: str) -> None:
    """Increment ``grace_decomposition_reruns_total`` with the direction label."""
    try:
        grace_decomposition_reruns_total.add(1, {"direction": direction})
    except Exception:  # noqa: BLE001
        pass


# Chunk 42, D331/D333/D337 — Permission Matrix counters.
# Counter-event mapping (N2): four EventType literals map to three counters.
# `permission_cluster_decision_recorded` is a sub-event of
# `permission_matrix_ratified` — both increment
# `grace_permission_matrix_ratifications_total`. No fourth counter (mirrors
# the D298 Change_Directives precedent).
grace_permission_matrix_hypotheses_total = _meter.create_counter(
    name="grace_permission_matrix_hypotheses_total",
    unit="1",
    description=(
        "Permission Matrix hypothesis-generation runs that completed. "
        "Cardinality 1 (no labels). D333."
    ),
)

grace_permission_matrix_ratifications_total = _meter.create_counter(
    name="grace_permission_matrix_ratifications_total",
    unit="1",
    description=(
        "Permission Matrix ratifications. Shared by "
        "permission_matrix_ratified AND permission_cluster_decision_recorded "
        "EventType literals (N2 counter-event 4:3 mapping). Cardinality 1 "
        "(no labels). D331/D333."
    ),
)

grace_permission_drift_auto_assignments_total = _meter.create_counter(
    name="grace_permission_drift_auto_assignments_total",
    unit="1",
    description=(
        "Drift-detector high-band auto-assignments. Label dim: drift_band "
        "(one of high, medium, low). D337."
    ),
)


def record_permission_matrix_hypothesis() -> None:
    """Increment ``grace_permission_matrix_hypotheses_total``.

    Best-effort; never fails the route or CLI.
    """
    try:
        grace_permission_matrix_hypotheses_total.add(1, {})
    except Exception:  # noqa: BLE001
        pass


def record_permission_matrix_ratification() -> None:
    """Increment ``grace_permission_matrix_ratifications_total``.

    Called by the ratify route AND by per-cluster decision recording
    (counter-event 4:3 mapping per N2). Best-effort.
    """
    try:
        grace_permission_matrix_ratifications_total.add(1, {})
    except Exception:  # noqa: BLE001
        pass


def record_permission_drift_auto_assignment(*, drift_band: str) -> None:
    """Increment ``grace_permission_drift_auto_assignments_total``.

    Pinned to the three-band kNN classification (high/medium/low). Callers
    that pass an unknown value still get the counter incremented under that
    label (the Pydantic Literal at the route/CLI layer is the typing fence).
    """
    try:
        grace_permission_drift_auto_assignments_total.add(
            1, {"drift_band": drift_band}
        )
    except Exception:  # noqa: BLE001
        pass


# Chunk 43, D344 — Sensitivity Gate Compliance Surface counters/gauges.
# Two new instruments per spec §10 / prompt §CP7. Cardinality: the gauge
# carries a single ``band`` label pinned to {high, medium, low}; the
# counter is unlabeled. GOLDEN_NAMES advances 75 → 78 (gauge contributes
# one entry; counter contributes two via stripped + ``_total`` pairing).
grace_sensitivity_coverage_band_count = _meter.create_gauge(
    name="grace_sensitivity_coverage_band_count",
    unit="1",
    description=(
        "Sensitivity Classification Report coverage band marker. Set to "
        "1 for the active band whenever a report is generated; intent is "
        "band-distribution visibility across active reports. Label dim: "
        "band (one of high, medium, low). D344."
    ),
)

grace_sensitivity_report_generated_total = _meter.create_counter(
    name="grace_sensitivity_report_generated_total",
    unit="1",
    description=(
        "Sensitivity Classification Report generation events. "
        "Cardinality 1 (no labels). Emitted by "
        "src.api.sensitivity_routes.generate_sensitivity_report. D344."
    ),
)


def record_sensitivity_coverage_band(*, band: str) -> None:
    """Set ``grace_sensitivity_coverage_band_count`` for the active band.

    Pinned to the three-band coverage classification (high/medium/low). The
    Pydantic Literal at the report layer is the typing fence; callers that
    pass an unknown value still get the gauge updated under that label.
    Best-effort; never fails the route.
    """
    try:
        grace_sensitivity_coverage_band_count.set(1, {"band": band})
    except Exception:  # noqa: BLE001
        pass


def record_sensitivity_report_generated() -> None:
    """Increment ``grace_sensitivity_report_generated_total``.

    Best-effort; never fails the route.
    """
    try:
        grace_sensitivity_report_generated_total.add(1, {})
    except Exception:  # noqa: BLE001
        pass


# Chunk 44, D369 — MCP write-tool observability counters.
# Two new instruments: one for all write-tool calls, one for agent-scoped
# calls. GOLDEN_NAMES advances 78 → 82 (both stripped and _total forms
# allowlisted, +4 entries). Registered instruments 57 → 59.

grace_mcp_write_tool_calls_total = _meter.create_counter(
    name="grace_mcp_write_tool_calls_total",
    unit="1",
    description=(
        "Total successful MCP write-tool invocations. "
        "Label dim: tool_name. D369."
    ),
)

grace_mcp_agent_scoped_calls_total = _meter.create_counter(
    name="grace_mcp_agent_scoped_calls_total",
    unit="1",
    description=(
        "MCP write-tool calls where the calling principal is "
        "UserActingViaAgent (agent-scoped). "
        "Label dim: tool_name. D369."
    ),
)


def record_mcp_write_tool_call(*, tool_name: str) -> None:
    """Increment ``grace_mcp_write_tool_calls_total``.

    Best-effort; never fails the tool call.
    """
    try:
        grace_mcp_write_tool_calls_total.add(1, {"tool_name": tool_name})
    except Exception:  # noqa: BLE001
        pass


def record_mcp_agent_scoped_call(*, tool_name: str) -> None:
    """Increment ``grace_mcp_agent_scoped_calls_total``.

    Best-effort; never fails the tool call.
    """
    try:
        grace_mcp_agent_scoped_calls_total.add(1, {"tool_name": tool_name})
    except Exception:  # noqa: BLE001
        pass


# ---------- Chunk 45 (D376) Remote Support Observability ----------

grace_remote_support_requests_total = _meter.create_counter(
    name="grace_remote_support_requests_total",
    unit="1",
    description=(
        "Counter incremented on every support-session request, "
        "including 403s from blocked routes. "
        "Label dims: route, admitted. D376."
    ),
)

grace_remote_support_sessions_active = _meter.create_up_down_counter(
    name="grace_remote_support_sessions_active",
    unit="1",
    description=(
        "Active support session count (expected 0 most of the time). "
        "No labels. D376."
    ),
)


def record_remote_support_request(*, route: str, admitted: bool) -> None:
    """Increment ``grace_remote_support_requests_total``.

    Best-effort; never fails the request path.
    """
    try:
        grace_remote_support_requests_total.add(
            1, {"route": route, "admitted": str(admitted).lower()}
        )
    except Exception:  # noqa: BLE001
        pass


# Chunk 47, D387/D389 — Signal→Proposal pipeline counters + gauge.
# Three new instruments; GOLDEN_NAMES 85 → 90; instruments 61 → 64.

grace_proposals_generated_total = _meter.create_counter(
    name="grace_proposals_generated_total",
    unit="1",
    description=(
        "Proposals generated by the signal→proposal pipeline. "
        "Label dims: tier, signal_type. D387."
    ),
)

grace_proposals_decided_total = _meter.create_counter(
    name="grace_proposals_decided_total",
    unit="1",
    description=(
        "Proposals decided by human reviewers. "
        "Label dim: decision. D389."
    ),
)


def _proposal_queue_depth_callback(
    options: CallbackOptions,
) -> Iterable[Observation]:
    """DB-query callback for proposal queue-depth gauge.

    Opens a SQLAlchemy session and queries ``schema_proposals`` directly.
    This is pattern (b) per spec §18.4 — necessary because the proposal
    generator runs out-of-process (D246 CLI-only); in-process observation
    would miss generator-written rows.
    """
    try:
        from src.shared.database import get_session_factory

        session_factory = get_session_factory()
        db = session_factory()
        try:
            from sqlalchemy import text

            rows = db.execute(
                text(
                    "SELECT change_tier, count(*) FROM schema_proposals "
                    "WHERE status = 'pending' GROUP BY change_tier"
                )
            ).fetchall()
            return [
                Observation(
                    value=count,
                    attributes={"tier": str(tier)},
                )
                for tier, count in rows
            ]
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return []


grace_proposal_queue_depth = _meter.create_observable_gauge(
    name="grace_proposal_queue_depth",
    callbacks=[_proposal_queue_depth_callback],
    unit="1",
    description=(
        "Pending proposal count by tier. DB-query callback pattern (b) — "
        "necessary because the generator runs out-of-process (D246). D387."
    ),
)


def record_proposal_generated(*, tier: str, signal_type: str) -> None:
    """Increment ``grace_proposals_generated_total``.

    Best-effort; never fails the generator.
    """
    try:
        grace_proposals_generated_total.add(
            1, {"tier": tier, "signal_type": signal_type}
        )
    except Exception:  # noqa: BLE001
        pass


def record_proposal_decided(*, decision: str) -> None:
    """Increment ``grace_proposals_decided_total``.

    Best-effort; never fails the route.
    """
    try:
        grace_proposals_decided_total.add(1, {"decision": decision})
    except Exception:  # noqa: BLE001
        pass


# Chunk 48, D392/D393 — KGCL Change Executor counters.
# Two new instruments; GOLDEN_NAMES 90 → 93; instruments 64 → 66.
# Invariant carve-out (D356): GOLDEN_NAMES count change authorized by
# chunk-48-spec-v6-FINAL.md §6 CP5.

grace_proposals_executed_total = _meter.create_counter(
    name="grace_proposals_executed_total",
    unit="1",
    description=(
        "Proposals executed by the change executor. "
        "Label dims: tier (1/2/3), outcome (applied/rejected/gate_failed/error). D392."
    ),
)

grace_proposal_execution_duration_seconds = _meter.create_histogram(
    name="grace_proposal_execution_duration_seconds",
    unit="s",
    description=(
        "Wall-clock execution time per proposal. "
        "Label dim: tier (1/2/3). D393."
    ),
)


def record_proposal_executed(*, tier: str, outcome: str) -> None:
    """Increment ``grace_proposals_executed_total``.

    Best-effort; never fails the executor.
    """
    try:
        grace_proposals_executed_total.add(1, {"tier": tier, "outcome": outcome})
    except Exception:  # noqa: BLE001
        pass


def record_proposal_execution_duration(*, tier: str, duration: float) -> None:
    """Record ``grace_proposal_execution_duration_seconds``.

    Best-effort; never fails the executor.
    """
    try:
        grace_proposal_execution_duration_seconds.record(duration, {"tier": tier})
    except Exception:  # noqa: BLE001
        pass


# --- Chunk 49: Earned Autonomy Calibration counters (D394–D396) ---
# Invariant: GOLDEN_NAMES 93→97, instruments 66→68.
# Authorization source: chunk-49-spec-v6-FINAL.md §8.1.

grace_calibration_decisions_recorded_total = _meter.create_counter(
    name="grace_calibration_decisions_recorded_total",
    unit="1",
    description=(
        "Count of calibration decisions recorded via post-decision hook. "
        "Label dims: tier (1/2/3), decision (approved/rejected). D394."
    ),
)

grace_calibration_updater_runs_total = _meter.create_counter(
    name="grace_calibration_updater_runs_total",
    unit="1",
    description=(
        "Count of calibration updater runs completed. "
        "Label dims: tier (1/2/3), outcome (success/regression_detected/error). D394."
    ),
)


def record_calibration_decision(*, tier: str, decision: str) -> None:
    """Increment ``grace_calibration_decisions_recorded_total``.

    Best-effort; never fails the decide route.
    """
    try:
        grace_calibration_decisions_recorded_total.add(1, {"tier": tier, "decision": decision})
    except Exception:  # noqa: BLE001
        pass


def record_calibration_updater_run(*, tier: str, outcome: str) -> None:
    """Increment ``grace_calibration_updater_runs_total``.

    Best-effort; never fails the updater.
    """
    try:
        grace_calibration_updater_runs_total.add(1, {"tier": tier, "outcome": outcome})
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Chunk 50 — Agent Daemon counters (D398, instruments 68→71)
# ---------------------------------------------------------------------------

grace_agent_ticks_total = _meter.create_counter(
    name="grace_agent_ticks_total",
    unit="1",
    description="Count of agent daemon ticks executed. D398.",
)

grace_autonomous_proposals_applied_total = _meter.create_counter(
    name="grace_autonomous_proposals_applied_total",
    unit="1",
    description=(
        "Count of proposals autonomously applied by the daemon. "
        "Label dims: tier (1/2), outcome (cooling_entered/auto_finalized/confirmed/reverted/error). D398."
    ),
)

grace_cooling_periods_finalized_total = _meter.create_counter(
    name="grace_cooling_periods_finalized_total",
    unit="1",
    description=(
        "Count of cooling periods finalized. "
        "Label dims: outcome (confirmed/auto_finalized/reverted). D399."
    ),
)


def record_agent_tick() -> None:
    """Increment ``grace_agent_ticks_total``. Best-effort."""
    try:
        grace_agent_ticks_total.add(1)
    except Exception:  # noqa: BLE001
        pass


def record_autonomous_proposal_applied(*, tier: str, outcome: str) -> None:
    """Increment ``grace_autonomous_proposals_applied_total``. Best-effort."""
    try:
        grace_autonomous_proposals_applied_total.add(1, {"tier": tier, "outcome": outcome})
    except Exception:  # noqa: BLE001
        pass


def record_cooling_period_finalized(*, outcome: str) -> None:
    """Increment ``grace_cooling_periods_finalized_total``. Best-effort."""
    try:
        grace_cooling_periods_finalized_total.add(1, {"outcome": outcome})
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Chunk 51 — Federation Infrastructure counters (D402/D404, instruments 71→73)
# Invariant: GOLDEN_NAMES 103→107, instruments 71→73.
# Authorization source: chunk-51-spec-v4-FINAL.md §8.1.
# ---------------------------------------------------------------------------

grace_federation_namespace_registered_total = _meter.create_counter(
    name="grace_federation_namespace_registered_total",
    unit="1",
    description=(
        "Count of federation namespaces registered. "
        "Label dims: namespace_type (mother/child). D402."
    ),
)

grace_federation_entity_resolved_total = _meter.create_counter(
    name="grace_federation_entity_resolved_total",
    unit="1",
    description=(
        "Count of entity resolution attempts. "
        "Label dims: resolution_method (exact/embedding/llm/unresolved). D404."
    ),
)


def record_federation_namespace_registered(*, namespace_type: str) -> None:
    """Increment ``grace_federation_namespace_registered_total``. Best-effort."""
    try:
        grace_federation_namespace_registered_total.add(
            1, {"namespace_type": namespace_type}
        )
    except Exception:  # noqa: BLE001
        pass


def record_federation_entity_resolved(*, resolution_method: str) -> None:
    """Increment ``grace_federation_entity_resolved_total``. Best-effort."""
    try:
        grace_federation_entity_resolved_total.add(
            1, {"resolution_method": resolution_method}
        )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Chunk 52 — Federation Query Routing counters/histogram (D406–D408)
# Invariant: GOLDEN_NAMES 107→112, instruments 73→76.
# Authorization source: chunk-52-spec-v3-FINAL.md §6 Step 5 / §8.1.
# ---------------------------------------------------------------------------

grace_federation_query_duration_seconds = _meter.create_histogram(
    name="grace_federation_query_duration_seconds",
    unit="s",
    description=(
        "Per-namespace federation sub-query duration. "
        "Label dims: namespace, outcome (success/timeout/error). D406/D408."
    ),
)

grace_federation_queries_total = _meter.create_counter(
    name="grace_federation_queries_total",
    unit="1",
    description=(
        "Count of federated query fan-outs. "
        "Label dims: namespace_count. D406."
    ),
)

grace_federation_result_merge_total = _meter.create_counter(
    name="grace_federation_result_merge_total",
    unit="1",
    description=(
        "Count of cross-namespace result merges. "
        "Label dims: merge_strategy. D407."
    ),
)


def record_federation_query_duration(*, namespace: str, outcome: str, duration: float) -> None:
    """Record ``grace_federation_query_duration_seconds``. Best-effort."""
    try:
        grace_federation_query_duration_seconds.record(
            duration, {"namespace": namespace, "outcome": outcome}
        )
    except Exception:  # noqa: BLE001
        pass


def record_federation_queries(*, namespace_count: str) -> None:
    """Increment ``grace_federation_queries_total``. Best-effort."""
    try:
        grace_federation_queries_total.add(1, {"namespace_count": namespace_count})
    except Exception:  # noqa: BLE001
        pass


def record_federation_result_merge(*, merge_strategy: str) -> None:
    """Increment ``grace_federation_result_merge_total``. Best-effort."""
    try:
        grace_federation_result_merge_total.add(1, {"merge_strategy": merge_strategy})
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Chunk 53 — Connector Framework counters/histogram (D409/D413)
# Invariant: GOLDEN_NAMES 112→115, instruments 76→78.
# Authorization source: chunk-53-prompt-v2-FINAL.md CP9.
# ---------------------------------------------------------------------------

grace_connector_sync_records_total = _meter.create_counter(
    name="grace_connector_sync_records_total",
    unit="1",
    description=(
        "Count of records processed during connector sync. "
        "Label dims: connector_type, outcome (created/updated/bridged/queued). D409."
    ),
)

grace_connector_sync_duration_seconds = _meter.create_histogram(
    name="grace_connector_sync_duration_seconds",
    unit="s",
    description=(
        "Duration of connector sync operations. "
        "Label dims: connector_type, mode (initial/incremental). D413."
    ),
)


def record_connector_sync_record(*, connector_type: str, outcome: str) -> None:
    """Increment ``grace_connector_sync_records_total``. Best-effort."""
    try:
        grace_connector_sync_records_total.add(
            1, {"connector_type": connector_type, "outcome": outcome}
        )
    except Exception:  # noqa: BLE001
        pass


def record_connector_sync_duration(
    *, connector_type: str, mode: str, duration: float
) -> None:
    """Record ``grace_connector_sync_duration_seconds``. Best-effort."""
    try:
        grace_connector_sync_duration_seconds.record(
            duration, {"connector_type": connector_type, "mode": mode}
        )
    except Exception:  # noqa: BLE001
        pass


# Chunk 61, D428 — Communication Ingestion observability.
# 7 new instruments (6 Counters + 1 Histogram). GOLDEN_NAMES 115→128
# (+13 entries). Registered instruments 78→85.

grace_ingestion_runs_started_total = _meter.create_counter(
    name="grace_ingestion_runs_started_total",
    unit="1",
    description="Ingestion pipeline runs started. Label dim: source_type. D428.",
)

grace_ingestion_runs_completed_total = _meter.create_counter(
    name="grace_ingestion_runs_completed_total",
    unit="1",
    description="Ingestion pipeline runs completed successfully. Label dim: source_type. D428.",
)

grace_ingestion_runs_failed_total = _meter.create_counter(
    name="grace_ingestion_runs_failed_total",
    unit="1",
    description="Ingestion pipeline runs failed. Label dim: source_type. D428.",
)

grace_ingestion_emails_processed_total = _meter.create_counter(
    name="grace_ingestion_emails_processed_total",
    unit="1",
    description=(
        "Communication events reaching terminal triage outcome. "
        "Label dims: source_name, tier_outcome (coarse). D428."
    ),
)

grace_ingestion_source_error_total = _meter.create_counter(
    name="grace_ingestion_source_error_total",
    unit="1",
    description=(
        "Adapter-level errors during ingestion. "
        "Label dims: source_name, error_class. D428."
    ),
)

grace_ingestion_triage_duration_seconds = _meter.create_histogram(
    name="grace_ingestion_triage_duration_seconds",
    unit="s",
    description="Per-tier wall-clock triage duration. Label dim: tier. D428.",
)

grace_voice_tone_profiles_generated_total = _meter.create_counter(
    name="grace_voice_tone_profiles_generated_total",
    unit="1",
    description="Voice & Tone profiles successfully generated. D428.",
)

# D506 capture-the-why: Voice Card export audit counter (Chunk 78).
# Invariant: registered-instrument count change 93 → 94. Authorization: D506.
grace_voice_cards_exported_total = _meter.create_counter(
    name="grace_voice_cards_exported_total",
    unit="1",
    description="Voice Card exports completed. D506.",
)


# Coarse tier-outcome mapping for metric labels (D428).
# Fine persisted outcomes → 5 coarse label values.
_COARSE_TIER_OUTCOME_MAP: dict[str, str] = {
    "passed_to_t4": "passed",
    "passed_to_extraction": "passed",
    "filtered_t1_duplicate_message_id": "filtered_t1",
    "filtered_t1_known_automated_sender": "filtered_t1",
    "filtered_t1_marketing_unsubscribe": "filtered_t1",
    "filtered_t1_auto_reply": "filtered_t1",
    "filtered_t1_ndr_bounce": "filtered_t1",
    "filtered_t1_empty_body": "filtered_t1",
    "filtered_t1_oversized": "filtered_t1",
    "filtered_t2_no_known_entity": "filtered_t2",
    "filtered_t3_below_threshold": "filtered_t3",
    "filtered_t4_not_organizationally_relevant": "filtered_t4",
    "filtered_t4_budget_exceeded": "filtered_t4",
}


def coarse_tier_outcome_for_metric(persisted: str) -> str:
    """Map fine ``triage_tier_outcome`` to coarse metric label (D428).

    Returns one of: ``passed``, ``filtered_t1``, ``filtered_t2``,
    ``filtered_t3``, ``filtered_t4``.

    Raises ``ValueError`` on ``pending`` — metrics must never emit for
    non-terminal outcomes.
    """
    if persisted == "pending":
        raise ValueError(
            "Cannot emit metric for 'pending' outcome — "
            "metrics fire only at terminal triage_tier_outcome sites."
        )
    coarse = _COARSE_TIER_OUTCOME_MAP.get(persisted)
    if coarse is None:
        raise ValueError(f"Unknown triage_tier_outcome for metric rollup: {persisted!r}")
    return coarse


def record_ingestion_run_started(*, source_type: str) -> None:
    """Increment ``grace_ingestion_runs_started_total``. Best-effort."""
    try:
        grace_ingestion_runs_started_total.add(1, {"source_type": source_type})
    except Exception:  # noqa: BLE001
        pass


def record_ingestion_run_completed(*, source_type: str) -> None:
    """Increment ``grace_ingestion_runs_completed_total``. Best-effort."""
    try:
        grace_ingestion_runs_completed_total.add(1, {"source_type": source_type})
    except Exception:  # noqa: BLE001
        pass


def record_ingestion_run_failed(*, source_type: str) -> None:
    """Increment ``grace_ingestion_runs_failed_total``. Best-effort."""
    try:
        grace_ingestion_runs_failed_total.add(1, {"source_type": source_type})
    except Exception:  # noqa: BLE001
        pass


def record_ingestion_email_processed(*, source_name: str, tier_outcome: str) -> None:
    """Increment ``grace_ingestion_emails_processed_total``. Best-effort."""
    try:
        grace_ingestion_emails_processed_total.add(
            1, {"source_name": source_name, "tier_outcome": tier_outcome}
        )
    except Exception:  # noqa: BLE001
        pass


def record_ingestion_source_error(*, source_name: str, error_class: str) -> None:
    """Increment ``grace_ingestion_source_error_total``. Best-effort."""
    try:
        grace_ingestion_source_error_total.add(
            1, {"source_name": source_name, "error_class": error_class}
        )
    except Exception:  # noqa: BLE001
        pass


def record_ingestion_triage_duration(*, tier: str, duration_seconds: float) -> None:
    """Record ``grace_ingestion_triage_duration_seconds``. Best-effort."""
    try:
        grace_ingestion_triage_duration_seconds.record(
            duration_seconds, {"tier": tier}
        )
    except Exception:  # noqa: BLE001
        pass


def record_voice_tone_profile_generated() -> None:
    """Increment ``grace_voice_tone_profiles_generated_total``. Best-effort."""
    try:
        grace_voice_tone_profiles_generated_total.add(1, {})
    except Exception:  # noqa: BLE001
        pass


# Chunk 72a (D469): extraction job lifecycle counters.
# Invariant: GOLDEN_NAMES 130→136, instruments 86→89.
grace_extraction_jobs_started_total = _meter.create_counter(
    name="grace_extraction_jobs_started_total",
    description="Extraction jobs spawned via POST /api/extraction/jobs",
    unit="1",
)

grace_extraction_jobs_completed_total = _meter.create_counter(
    name="grace_extraction_jobs_completed_total",
    description="Extraction jobs that completed successfully",
    unit="1",
)

grace_extraction_jobs_failed_total = _meter.create_counter(
    name="grace_extraction_jobs_failed_total",
    description="Extraction jobs that failed",
    unit="1",
)


def record_extraction_job_started(*, job_kind: str) -> None:
    """Increment ``grace_extraction_jobs_started_total``. Best-effort."""
    try:
        grace_extraction_jobs_started_total.add(1, {"job_kind": job_kind})
    except Exception:  # noqa: BLE001
        pass


def record_extraction_job_completed(*, job_kind: str) -> None:
    """Increment ``grace_extraction_jobs_completed_total``. Best-effort."""
    try:
        grace_extraction_jobs_completed_total.add(1, {"job_kind": job_kind})
    except Exception:  # noqa: BLE001
        pass


def record_extraction_job_failed(*, job_kind: str) -> None:
    """Increment ``grace_extraction_jobs_failed_total``. Best-effort."""
    try:
        grace_extraction_jobs_failed_total.add(1, {"job_kind": job_kind})
    except Exception:  # noqa: BLE001
        pass


# Chunk 73, D477 — Legacy ProposalType alias observability.
# Invariant: registered-instrument count change (89 → 90). Authorization: D477.
grace_proposal_type_legacy_alias_resolved_total = _meter.create_counter(
    name="grace_proposal_type_legacy_alias_resolved_total",
    unit="1",
    description=(
        "Legacy ProposalType alias resolved to canonical value. "
        "Label: legacy_value. D477."
    ),
)


# Chunk 77b, D502 — Image / vision pipeline observability.
# Invariant: registered-instrument count change (90 → 93). Authorization: D502.
# GOLDEN_NAMES advance 138 → 143 (+5: two Counters × stripped/_total + Histogram).
grace_image_assets_ingested_total = _meter.create_counter(
    name="grace_image_assets_ingested_total",
    description="Images persisted as Image_Asset vertices in ArcadeDB.",
    unit="1",
)

grace_vision_calls_total = _meter.create_counter(
    name="grace_vision_calls_total",
    description="Vision LLM calls for image analysis. Label: provider.",
    unit="1",
)

grace_vision_call_duration_seconds = _meter.create_histogram(
    name="grace_vision_call_duration_seconds",
    description="Vision LLM call wall time in seconds. Label: provider.",
    unit="s",
)


# Chunk 79 — Email Extraction Bridge counter (D508).
# D356 capture-the-why: invariant = GOLDEN_NAMES count / instrument count;
# carve-out = GOLDEN_NAMES 145→147, instruments 95→96;
# authorization = D508.
grace_email_extracted_total = _meter.create_counter(
    name="grace_email_extracted_total",
    description="Emails processed by the extraction bridge. Label: outcome (success|error|skipped).",
    unit="1",
)


# Chunk 80a, D513 — Thread reconstruction counter.
# D356 capture-the-why: invariant = GOLDEN_NAMES count / instrument count;
# carve-out = GOLDEN_NAMES 147→149, instruments 95→96;
# authorization = D513.
grace_email_thread_reconstructed_total = _meter.create_counter(
    name="grace_email_thread_reconstructed_total",
    description="Email threads reconstructed via RFC 5322 References-chain DAG. D513.",
    unit="1",
)


def record_image_asset_ingested() -> None:
    """Increment ``grace_image_assets_ingested_total``. Best-effort."""
    try:
        grace_image_assets_ingested_total.add(1)
    except Exception:  # noqa: BLE001
        pass


def record_vision_call(*, provider: str, duration_seconds: float) -> None:
    """Record vision call count and duration. Best-effort."""
    try:
        grace_vision_calls_total.add(1, {"provider": provider})
        grace_vision_call_duration_seconds.record(duration_seconds, {"provider": provider})
    except Exception:  # noqa: BLE001
        pass


# Chunk 80b, D517 — Corroboration promotion counter.
# D356 capture-the-why: invariant = GOLDEN_NAMES count / instrument count;
# carve-out = GOLDEN_NAMES 149→151, instruments 96→97;
# authorization = D517.
grace_corroboration_promotions_total = _meter.create_counter(
    name="grace_corroboration_promotions_total",
    description="Corroboration promotion decisions. Label: status (first_class|provisional). D517.",
    unit="1",
)


def record_corroboration_promotion(*, status: str) -> None:
    """Increment ``grace_corroboration_promotions_total``. Best-effort."""
    try:
        grace_corroboration_promotions_total.add(1, {"status": status})
    except Exception:  # noqa: BLE001
        pass


def initialize_placeholder_metrics() -> None:
    """Called by `setup_otel` after provider setup. No-op: instrument
    objects are created at module import; this function exists as an
    explicit hook for the bootstrap layer.
    """
    return None
