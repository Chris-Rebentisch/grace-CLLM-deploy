"""Thin async client for Prometheus instant + range queries (D138 airgapped).

Usage:
    reader = PrometheusReader(base_url="http://127.0.0.1:9090")
    result = await reader.query_instant("rate(grace_signal_a_strength[5m])")

The two result types correspond to Prometheus's ``vector`` and ``matrix``
``resultType`` envelopes. Non-200 responses or ``status != 'success'``
raise ``PrometheusQueryError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import structlog

log = structlog.get_logger()


class PrometheusQueryError(Exception):
    """Raised when Prometheus returns a non-success response."""


@dataclass
class PromVectorEntry:
    metric: dict[str, str]
    value_at: float
    value: float


@dataclass
class PromMatrixEntry:
    metric: dict[str, str]
    values: list[tuple[float, float]]


@dataclass
class PromVectorResult:
    """Prometheus instant-query response (``resultType="vector"``)."""

    entries: list[PromVectorEntry] = field(default_factory=list)


@dataclass
class PromMatrixResult:
    """Prometheus range-query response (``resultType="matrix"``)."""

    entries: list[PromMatrixEntry] = field(default_factory=list)


def _parse_value(pair: list[Any]) -> tuple[float, float]:
    # Prometheus value pair: [<unix_ts:float>, "<string-typed-number>"].
    return float(pair[0]), float(pair[1])


def _parse_vector(payload: dict) -> PromVectorResult:
    if payload.get("status") != "success":
        raise PrometheusQueryError(f"Prometheus query failed: {payload!r}")
    data = payload.get("data") or {}
    if data.get("resultType") != "vector":
        raise PrometheusQueryError(
            f"Expected resultType='vector', got {data.get('resultType')!r}"
        )
    entries: list[PromVectorEntry] = []
    for item in data.get("result", []):
        ts, val = _parse_value(item["value"])
        entries.append(
            PromVectorEntry(metric=item.get("metric", {}), value_at=ts, value=val)
        )
    return PromVectorResult(entries=entries)


def _parse_matrix(payload: dict) -> PromMatrixResult:
    if payload.get("status") != "success":
        raise PrometheusQueryError(f"Prometheus query failed: {payload!r}")
    data = payload.get("data") or {}
    if data.get("resultType") != "matrix":
        raise PrometheusQueryError(
            f"Expected resultType='matrix', got {data.get('resultType')!r}"
        )
    entries: list[PromMatrixEntry] = []
    for item in data.get("result", []):
        values = [_parse_value(v) for v in item.get("values", [])]
        entries.append(
            PromMatrixEntry(metric=item.get("metric", {}), values=values)
        )
    return PromMatrixResult(entries=entries)


class PrometheusReader:
    """Async Prometheus HTTP client.

    Default URL ``http://127.0.0.1:9090`` matches the airgapped stack at
    ``docker/docker-compose.observability.yml`` (D138).
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9090",
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout
        self._owns_client = client is None

    async def __aenter__(self) -> "PrometheusReader":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self._timeout
            )
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self._timeout
            )
        return self._client

    async def query_instant(
        self, promql: str, at: datetime | None = None
    ) -> PromVectorResult:
        """Run an instant query against ``/api/v1/query``."""
        client = self._ensure_client()
        params: dict[str, str] = {"query": promql}
        if at is not None:
            params["time"] = f"{at.timestamp():.3f}"
        try:
            resp = await client.get("/api/v1/query", params=params)
        except httpx.HTTPError as exc:
            raise PrometheusQueryError(f"Prometheus HTTP error: {exc}") from exc
        if resp.status_code != 200:
            raise PrometheusQueryError(
                f"Prometheus returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return _parse_vector(resp.json())

    async def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str,
    ) -> PromMatrixResult:
        """Run a range query against ``/api/v1/query_range``."""
        client = self._ensure_client()
        params = {
            "query": promql,
            "start": f"{start.timestamp():.3f}",
            "end": f"{end.timestamp():.3f}",
            "step": step,
        }
        try:
            resp = await client.get("/api/v1/query_range", params=params)
        except httpx.HTTPError as exc:
            raise PrometheusQueryError(f"Prometheus HTTP error: {exc}") from exc
        if resp.status_code != 200:
            raise PrometheusQueryError(
                f"Prometheus returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return _parse_matrix(resp.json())
