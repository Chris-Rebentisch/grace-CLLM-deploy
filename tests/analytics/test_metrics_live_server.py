"""Live-server integration test for `/metrics/` scrape.

Spins up the FastAPI app under a real uvicorn subprocess and scrapes
`/metrics/` with `urllib`. This is the test that catches Defect 1 ---
`fastapi.testclient.TestClient` in-process dispatch differs from
uvicorn's ASGI lifecycle in exactly one way that matters for
`FastAPIInstrumentor.instrument_app()`: the middleware stack is built
on the first ASGI event (which, under uvicorn, is the lifespan
startup), so any OTel patching that happens inside `lifespan()` is
too late. The contract test at `test_metric_contract.py` calls
`setup_otel()` before entering the TestClient context and therefore
cannot see this bug.

We poll for readiness on `/metrics/` directly (the scrape is what we
care about) and keep the test scope narrow: asserting the scrape
returns bytes with the OTel-emitted HTTP server metric families. That
implies the FastAPIInstrumentor hook actually ran against the app
instance uvicorn is serving.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _scrape(url: str, timeout: float = 5.0) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body, resp.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", e.headers.get("content-type", "") if e.headers else ""


def _wait_for(
    check,
    *,
    deadline_s: float,
    interval_s: float = 0.25,
):
    """Poll `check` (a callable returning truthy on success) until deadline."""
    end = time.monotonic() + deadline_s
    last_err: Exception | None = None
    while time.monotonic() < end:
        try:
            if check():
                return True
        except Exception as exc:  # noqa: BLE001 --- diagnostics only
            last_err = exc
        time.sleep(interval_s)
    if last_err is not None:
        raise last_err
    return False


@pytest.fixture
def live_uvicorn():
    """Start uvicorn in a subprocess, yield its base URL, then terminate."""
    port = _find_free_port()
    env = os.environ.copy()
    # Ensure airgap guard is satisfied so the reranker doesn't try to
    # reach Hugging Face during app import (Defect 6).
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    # Silence the ConsoleSpanExporter; otherwise every request dumps a
    # JSON span into test stdout and makes failures hard to read.
    env["OTEL_TRACES_EXPORTER"] = "none"
    # Lock HTTP semconv so the metric names match GOLDEN_NAMES.
    env.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f"http://127.0.0.1:{port}"

    def _ready() -> bool:
        if proc.poll() is not None:
            raise RuntimeError(
                f"uvicorn exited early with code {proc.returncode}; "
                f"stdout/stderr tail:\n{(proc.stdout.read() if proc.stdout else '') or '<no output>'}"
            )
        status, _, _ = _scrape(f"{base_url}/openapi.json", timeout=1.5)
        return status == 200

    try:
        _wait_for(_ready, deadline_s=30.0)
        yield base_url
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        # Drain to avoid ResourceWarning on PIPE.
        if proc.stdout is not None:
            try:
                proc.stdout.read()
            except Exception:  # noqa: BLE001
                pass


def test_metrics_live_scrape_returns_nonempty_body(live_uvicorn):
    """`/metrics/` under live uvicorn must return a non-empty Prometheus body.

    Defect 1 regression guard: TestClient's in-process dispatch let
    `setup_otel()` inside `lifespan()` patch `build_middleware_stack`
    before any request, so HTTP metrics materialized. Under real
    uvicorn, the lifespan startup is itself an ASGI event that builds
    the middleware stack first --- so a lifespan-scoped setup is too
    late. This test asserts the live scrape has bytes.
    """
    # Generate at least one instrumented request so the HTTP histogram
    # has a data point to emit. Any GET on a FastAPI route will do.
    status, _, _ = _scrape(f"{live_uvicorn}/openapi.json")
    assert status == 200, "openapi.json must be reachable before scraping /metrics/"

    status, body, content_type = _scrape(f"{live_uvicorn}/metrics/")
    assert status == 200, f"/metrics/ returned {status}"
    assert content_type.startswith("text/plain"), (
        f"unexpected content-type: {content_type!r}"
    )
    assert len(body) > 0, (
        "/metrics/ returned HTTP 200 with 0 bytes --- "
        "Defect 1 regression. FastAPIInstrumentor likely did not patch "
        "app.build_middleware_stack before uvicorn built the cached stack."
    )


def test_metrics_live_scrape_contains_expected_families(live_uvicorn):
    """Golden-name subset check against the live scrape.

    Mirrors `GOLDEN_NAMES` in `test_metric_contract.py` but only for
    the families that should be emitted by a warm-started uvicorn:
    HTTP server instrumentation (FastAPIInstrumentor) and the
    `target_info` resource marker (PrometheusMetricReader). Custom
    GrACE families (pipeline, LLM, graph-health) require pipeline
    traffic we don't exercise here; the contract test covers those.
    """
    _scrape(f"{live_uvicorn}/openapi.json")
    _scrape(f"{live_uvicorn}/api/regeneration/config")

    status, body, _ = _scrape(f"{live_uvicorn}/metrics/")
    assert status == 200
    text = body.decode("utf-8", errors="replace")

    type_lines = [ln for ln in text.splitlines() if ln.startswith("# TYPE ")]
    names = {ln.split()[2] for ln in type_lines}

    required = {
        "target_info",
        "http_server_request_duration_seconds",
    }
    missing = required - names
    assert not missing, (
        f"live /metrics/ missing expected families: {sorted(missing)}; "
        f"present: {sorted(names)}"
    )
