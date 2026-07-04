"""Strict label-cardinality guard (spec §10.6, Q24-10, D148).

If `http_route` ever contains a raw UUID or un-templated path, this
test fails the build. No warn mode.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app
from prometheus_client.parser import text_string_to_metric_families

from src.analytics import otel_setup

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _build_templated_app() -> FastAPI:
    """A minimal app with a templated route and a mounted /metrics."""
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def _handler(item_id: str):
        return {"item_id": item_id}

    if not otel_setup._initialized:
        otel_setup.setup_otel(FastAPI())

    FastAPIInstrumentor.instrument_app(app)
    app.mount("/metrics", make_asgi_app())
    return app


def test_http_route_label_stays_templated():
    app = _build_templated_app()

    with TestClient(app) as client:
        resp = client.get(f"/items/{uuid4()}")
        assert resp.status_code == 200

        metrics_resp = client.get("/metrics")
        assert metrics_resp.status_code == 200

        families = list(text_string_to_metric_families(metrics_resp.text))
        duration_family = next(
            (f for f in families if f.name == "http_server_request_duration_seconds"),
            None,
        )
        assert duration_family is not None, "HTTP duration family missing from /metrics"

        # Every series for our route must carry the templated http_route.
        matching_series = [
            s for s in duration_family.samples if s.labels.get("http_route") == "/items/{item_id}"
        ]
        assert matching_series, (
            "No series found with http_route='/items/{item_id}'. "
            f"Labels seen: {[s.labels.get('http_route') for s in duration_family.samples]}"
        )

        for family in families:
            for sample in family.samples:
                for label_value in sample.labels.values():
                    assert not UUID_RE.search(label_value), (
                        f"UUID leaked into a label value: "
                        f"metric={family.name}, labels={sample.labels}"
                    )
