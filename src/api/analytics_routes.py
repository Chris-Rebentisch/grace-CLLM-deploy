"""Analytics routes (Chunk 33, D248/D249/D253/D254).

Single endpoint: ``POST /api/analytics/alerts/_internal``.

Receives Grafana Unified Alerting webhook payloads, persists each alert
into ``alert_events``, and increments ``grace_alert_fires_total``.

Security posture:
- The endpoint is loopback-by-default — only ``127.0.0.1`` / ``::1`` /
  the Docker bridge ``172.16.0.0/12`` (defense-in-depth). Any other
  source returns 403.
- The route requires ``X-Admin-Key`` (D249). Auth middleware enforces
  this; the route handler itself does not re-check.
- Idempotency: identical webhook payloads delivered within 60s collapse
  to a single ``alert_events`` row.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Counter as TypingCounter
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, text

from src.analytics import metrics as grace_metrics
from src.shared.config import get_settings

log = structlog.get_logger()

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

DOCKER_BRIDGE_CIDR = ipaddress.ip_network("172.16.0.0/12")
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "testclient"}
IDEMPOTENCY_WINDOW = timedelta(seconds=60)

# D162 cardinality guard: alertname Top-N + ``_other_``. Counts are
# process-local; a process restart resets the cap (acceptable — D162's
# guarantee is bounded label cardinality, not bounded across restarts).
_ALERTNAME_CAP = 20
_alertname_seen: dict[str, int] = {}


def _capped_alertname(alertname: str) -> str:
    """D162 guard: Top-N alertnames are passed through; the rest fold to ``_other_``."""
    if alertname in _alertname_seen:
        return alertname
    if len(_alertname_seen) < _ALERTNAME_CAP:
        _alertname_seen[alertname] = 1
        return alertname
    return "_other_"


def _resolve_client_host(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


def _is_admissible_source(host: str | None) -> bool:
    if host is None:
        return False
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host) in DOCKER_BRIDGE_CIDR
    except ValueError:
        return False


def _payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class GrafanaAlert(BaseModel):
    """One alert entry in Grafana 11.3 webhook payload (extra=ignore)."""

    model_config = ConfigDict(extra="ignore")

    status: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: str | None = None
    endsAt: str | None = None


class GrafanaAlertWebhookPayload(BaseModel):
    """Top-level Grafana Unified Alerting webhook envelope (extra=ignore)."""

    model_config = ConfigDict(extra="ignore")

    alerts: list[GrafanaAlert] = Field(default_factory=list)


def _engine_factory():
    """Lazily build a SQLAlchemy engine. Lives at module scope so tests
    can monkeypatch the factory without import-time DB churn.
    """
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


_engine_cache: Any = None


def _get_engine():
    global _engine_cache
    if _engine_cache is None:
        _engine_cache = _engine_factory()
    return _engine_cache


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    # Grafana sends RFC3339 with Z suffix for UTC.
    cleaned = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@router.post("/alerts/_internal", status_code=status.HTTP_200_OK)
async def receive_grafana_webhook(request: Request) -> dict[str, Any]:
    host = _resolve_client_host(request)
    if not _is_admissible_source(host):
        log.warning("alert_webhook.rejected_source", host=host)
        raise HTTPException(
            status_code=403,
            detail="forbidden source",
        )

    raw = await request.body()
    try:
        payload_dict = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="invalid JSON payload")

    payload = GrafanaAlertWebhookPayload.model_validate(payload_dict)
    payload_hash = _payload_hash(payload_dict)
    received_at = datetime.now(UTC)

    engine = _get_engine()
    with engine.begin() as conn:
        # Idempotency: lookup recent identical hash.
        recent = conn.execute(
            text(
                """
                SELECT 1 FROM alert_events
                WHERE webhook_payload_hash = :h
                  AND received_at >= :cutoff
                LIMIT 1
                """
            ),
            {"h": payload_hash, "cutoff": received_at - IDEMPOTENCY_WINDOW},
        ).first()
        if recent is not None:
            log.info("alert_webhook.duplicate_ignored", hash=payload_hash)
            return {"status": "duplicate_ignored"}

        written = 0
        for alert in payload.alerts:
            labels = alert.labels or {}
            annotations = alert.annotations or {}
            alertname = str(labels.get("alertname", "_unknown_"))
            severity = str(labels.get("severity", "info"))
            ontology_module = labels.get("ontology_module")
            state = (alert.status or labels.get("state") or "firing").lower()
            if state not in {"firing", "resolved"}:
                state = "firing"

            fired_at = _parse_iso(alert.startsAt) or received_at
            resolved_at = (
                _parse_iso(alert.endsAt) if state == "resolved" else None
            )

            conn.execute(
                text(
                    """
                    INSERT INTO alert_events (
                        id, alertname, severity, ontology_module, state,
                        fired_at, resolved_at, labels, annotations,
                        webhook_payload_hash, received_at
                    ) VALUES (
                        :id, :alertname, :severity, :module, :state,
                        :fired_at, :resolved_at,
                        CAST(:labels AS jsonb), CAST(:annotations AS jsonb),
                        :hash, :received_at
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "alertname": alertname,
                    "severity": severity,
                    "module": ontology_module if ontology_module else None,
                    "state": state,
                    "fired_at": fired_at,
                    "resolved_at": resolved_at,
                    "labels": json.dumps(labels, default=str),
                    "annotations": json.dumps(annotations, default=str),
                    "hash": payload_hash,
                    "received_at": received_at,
                },
            )
            written += 1

            if state == "firing":
                attrs = {
                    "alertname": _capped_alertname(alertname),
                    "severity": severity,
                    "ontology_module": str(ontology_module or "__global__"),
                    "state": state,
                }
                grace_metrics.alert_fires.add(1, attributes=attrs)

    return {"status": "ok", "written": written}
