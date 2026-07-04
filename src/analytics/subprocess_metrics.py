"""F-15: CLI-subprocess metrics transport via prometheus_client multiproc.

Validation-run F-15: every D246 CLI pipeline (image_pipeline,
extraction_bridge, voice_tone, corroboration, ...) records OTel counters into
its OWN in-process meter, which dies with the subprocess — Prometheus scrapes
only uvicorn's /metrics, so the pipeline counters that CLAUDE.md documents
(grace_vision_calls_total, grace_email_extracted_total, ...) were structurally
unobservable.

Chosen transport (needs-decision F-15): **prometheus_client multiprocess
write-through** — least infrastructure of the three options (no OTLP
collector container, no pushgateway). ``prometheus_client`` is already a
dependency and supports mmap-file multiprocess mode natively:

- CLI side: :func:`init_subprocess_metrics` (called by CLI entrypoints,
  no-op unless ``PROMETHEUS_MULTIPROC_DIR`` is set) installs an OTel
  MeterProvider whose exporter mirrors COUNTER data points into
  ``prometheus_client`` counters. With the env var set, prometheus_client
  persists values to per-pid mmap files in the directory.
- Server side: the /metrics route appends the aggregation of those files via
  ``multiprocess.MultiProcessCollector`` (see ``src/api/main.py``).

Scope history: v1 was counters only (the audit-relevant class). F-0034
(validation run) added histogram COUNT/SUM mirroring as exact-named
monotonic series. F-0049/ISS-0040 (validation run, 2026-07-03) added
LAST-VALUE GAUGE mirroring: the signal pipeline's ``grace_signal_*_strength``
and correlation engine's ``grace_correlation_*_strength`` families are OTel
Gauges, which the counters-only v1 design silently dropped — 8 of 13 golden
metric families never reached /metrics. Full histogram buckets remain a
follow-up. Name-collision note: mirrored names are the D246 CLI-only
families, which the uvicorn OTel registry never emits — the concatenated
exposition stays collision-free by construction.

Counter semantics across runs: per-pid mmap files accumulate and
MultiProcessCollector SUMS them — cumulative-total semantics survive
subprocess exits, which is exactly what Prometheus counters want.
"""

from __future__ import annotations

import os
import re

import structlog

logger = structlog.get_logger()

_initialized = False


def multiproc_dir() -> str | None:
    """Return the configured multiproc dir, or None when transport disabled."""
    d = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()
    return d or None


class _PrometheusMultiprocExporter:
    """OTel MetricExporter mirroring counter deltas into prometheus_client.

    Configured with DELTA temporality for counters so each export cycle's
    data-point value can be fed to ``Counter.inc()`` directly.
    """

    def __init__(self) -> None:
        from opentelemetry.sdk.metrics import (
            Counter as SdkCounter,
            ObservableCounter,
            UpDownCounter,
        )
        from opentelemetry.sdk.metrics.export import AggregationTemporality

        self._preferred_temporality = {
            SdkCounter: AggregationTemporality.DELTA,
            UpDownCounter: AggregationTemporality.CUMULATIVE,
            ObservableCounter: AggregationTemporality.CUMULATIVE,
        }
        self._pc_counters: dict[tuple[str, tuple[str, ...]], object] = {}

    # --- MetricExporter protocol (duck-typed via subclassing at build) ---

    def _get_counter(self, name: str, description: str, labelnames: tuple[str, ...]):
        from prometheus_client import Counter as PcCounter

        key = (name, labelnames)
        if key not in self._pc_counters:
            # prometheus_client appends _total to counters; OTel names may
            # already carry it — strip to avoid grace_x_total_total.
            base = re.sub(r"_total$", "", name)
            self._pc_counters[key] = PcCounter(
                base, description or base, list(labelnames)
            )
        return self._pc_counters[key]

    def _get_series_gauge(self, name: str, description: str, labelnames: tuple[str, ...]):
        """F-0034: monotonic series mirrored under an EXACT exposition name.

        prometheus_client Counters force a ``_total`` suffix, but the OTel
        histogram COUNT series must surface as ``<name>_count`` verbatim
        (Signal A queries ``rate(grace_extraction_triple_confidence_count…)``).
        A multiproc ``livesum`` Gauge keeps the exact name and, because we only
        ever ``inc()`` it, stays monotonic — rate() semantics hold.
        """
        key = (name, labelnames)
        if key not in self._pc_counters:
            from prometheus_client import Gauge as PcGauge

            self._pc_counters[key] = PcGauge(
                name, description or name, list(labelnames), multiprocess_mode="livesum"
            )
        return self._pc_counters[key]

    def _get_lastvalue_gauge(self, name: str, description: str, labelnames: tuple[str, ...]):
        """F-0049/ISS-0040: OTel Gauge mirrored under the EXACT metric name.

        multiprocess_mode choice: ``"mostrecent"`` — the installed
        prometheus_client (0.25.0) supports it, and it is the only mode with
        correct last-value semantics across processes: MultiProcessCollector
        keeps the sample with the newest write timestamp, so a fresh CLI run's
        gauge value replaces a stale one from a dead pid. The alternatives all
        distort last-value data: ``livesum``/``all`` would SUM strengths across
        pids, ``max``/``min`` would pin to historical extremes. (``livesum``
        stays correct for the F-0034 histogram-count series above because
        those are only ever inc()'d — monotonic — never set().)
        """
        key = (name, labelnames)
        if key not in self._pc_counters:
            from prometheus_client import Gauge as PcGauge

            self._pc_counters[key] = PcGauge(
                name,
                description or name,
                list(labelnames),
                multiprocess_mode="mostrecent",
            )
        return self._pc_counters[key]

    def export(self, metrics_data, timeout_millis: float = 10_000, **kwargs):
        from opentelemetry.sdk.metrics.export import MetricExportResult
        from opentelemetry.sdk.metrics.export import Gauge as OtelGauge
        from opentelemetry.sdk.metrics.export import Histogram as OtelHistogram
        from opentelemetry.sdk.metrics.export import Sum

        try:
            for rm in metrics_data.resource_metrics:
                for sm in rm.scope_metrics:
                    for metric in sm.metrics:
                        data = metric.data
                        # F-0034 (validation run, 2026-07-03): the v1
                        # counters-only scope left CLI-subprocess HISTOGRAMS
                        # (grace_extraction_triple_confidence) invisible to
                        # /metrics — Signal A's INSUFFICIENT-verdict substrate
                        # never reached the TSDB for out-of-process extraction
                        # (this deployment runs ALL extraction out-of-process).
                        # Mirror histogram data points' COUNT (and SUM) as
                        # exact-named monotonic series.
                        if isinstance(data, OtelHistogram):
                            for dp in data.data_points:
                                attrs = dict(dp.attributes or {})
                                labelnames = tuple(sorted(attrs))
                                labels = {k: str(attrs[k]) for k in labelnames}
                                cnt = float(dp.count or 0)
                                if cnt <= 0:
                                    continue
                                g = self._get_series_gauge(
                                    f"{metric.name}_count", metric.description or "", labelnames
                                )
                                (g.labels(**labels) if labelnames else g).inc(cnt)
                                if dp.sum is not None:
                                    gs = self._get_series_gauge(
                                        f"{metric.name}_sum", metric.description or "", labelnames
                                    )
                                    (gs.labels(**labels) if labelnames else gs).inc(float(dp.sum))
                            continue
                        # F-0049/ISS-0040 (validation run, 2026-07-03):
                        # the v1 counters-only + F-0034 histogram scope still
                        # dropped OTel GAUGE data points, leaving the signal
                        # (grace_signal_*_strength) and correlation
                        # (grace_correlation_*_strength) families invisible to
                        # /metrics for out-of-process D246 runs. Mirror gauge
                        # data points as last-value ("mostrecent") gauges under
                        # the exact metric name — set(), never inc(), and no
                        # zero-skip: 0.0 is a legitimate last value.
                        if isinstance(data, OtelGauge):
                            for dp in data.data_points:
                                attrs = dict(dp.attributes or {})
                                labelnames = tuple(sorted(attrs))
                                labels = {k: str(attrs[k]) for k in labelnames}
                                g = self._get_lastvalue_gauge(
                                    metric.name, metric.description or "", labelnames
                                )
                                (g.labels(**labels) if labelnames else g).set(
                                    float(dp.value)
                                )
                            continue
                        if not isinstance(data, Sum) or not data.is_monotonic:
                            continue  # counters + histograms (F-0034) + gauges (F-0049)
                        for dp in data.data_points:
                            attrs = dict(dp.attributes or {})
                            labelnames = tuple(sorted(attrs))
                            counter = self._get_counter(
                                metric.name, metric.description or "", labelnames
                            )
                            value = float(dp.value)
                            if value <= 0:
                                continue
                            if labelnames:
                                counter.labels(
                                    **{k: str(attrs[k]) for k in labelnames}
                                ).inc(value)
                            else:
                                counter.inc(value)
            return MetricExportResult.SUCCESS
        except Exception as exc:  # noqa: BLE001 — metrics must never break pipelines
            logger.warning("subprocess_metrics.export_failed", error=str(exc))
            return MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:
        return None


def init_subprocess_metrics() -> bool:
    """Install the multiproc write-through for a CLI subprocess (F-15).

    Call once at CLI entrypoint start, BEFORE pipeline work. No-op (returns
    False) when ``PROMETHEUS_MULTIPROC_DIR`` is unset — the operator opts in
    by setting it in ``.env`` (spawned subprocesses inherit it) and the
    uvicorn /metrics route aggregates the directory.

    Returns True when the transport was installed.
    """
    global _initialized
    if _initialized:
        return True
    d = multiproc_dir()
    if not d:
        return False

    try:
        os.makedirs(d, exist_ok=True)

        from opentelemetry import metrics as otel_metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            MetricExporter,
            PeriodicExportingMetricReader,
        )

        exporter_impl = _PrometheusMultiprocExporter()

        # Bind the duck-typed impl into a real MetricExporter subclass so the
        # SDK accepts it (temporality preferences flow via constructor).
        class _Exporter(MetricExporter):
            def __init__(self) -> None:
                super().__init__(
                    preferred_temporality=exporter_impl._preferred_temporality
                )

            def export(self, metrics_data, timeout_millis: float = 10_000, **kw):
                return exporter_impl.export(metrics_data, timeout_millis, **kw)

            def force_flush(self, timeout_millis: float = 10_000) -> bool:
                return exporter_impl.force_flush(timeout_millis)

            def shutdown(self, timeout_millis: float = 30_000, **kw) -> None:
                return exporter_impl.shutdown(timeout_millis, **kw)

        reader = PeriodicExportingMetricReader(
            _Exporter(), export_interval_millis=15_000
        )
        # shutdown_on_exit=True (default): the provider flushes at process
        # exit via atexit, so short-lived CLI runs still land their counters.
        provider = MeterProvider(metric_readers=[reader])
        otel_metrics.set_meter_provider(provider)
        _initialized = True
        logger.info("subprocess_metrics.initialized", multiproc_dir=d)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("subprocess_metrics.init_failed", error=str(exc))
        return False


def multiproc_exposition() -> bytes:
    """Server-side helper: aggregate multiproc files to exposition text.

    Returns b"" when the transport is disabled or the directory is empty.
    Used by the /metrics route to append CLI-subprocess counter families.
    """
    d = multiproc_dir()
    if not d or not os.path.isdir(d):
        return b""
    try:
        if not any(name.endswith(".db") for name in os.listdir(d)):
            return b""
        from prometheus_client import CollectorRegistry, generate_latest
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry, path=d)
        return generate_latest(registry)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subprocess_metrics.exposition_failed", error=str(exc))
        return b""
