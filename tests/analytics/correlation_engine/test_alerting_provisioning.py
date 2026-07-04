"""Lint test for Grafana Unified Alerting provisioning (Chunk 33, D249/D251)."""

from __future__ import annotations

from pathlib import Path

import yaml

PROVISIONING_DIR = Path("docker/grafana/provisioning/alerting")
SQL_TABLE_ALLOWLIST = {"diagnostic_records", "correlation_runs", "alert_events"}


def test_rules_yaml_valid_and_init_filtered_and_runbook_links_present():
    raw = (PROVISIONING_DIR / "rules.yaml").read_text()
    payload = yaml.safe_load(raw)
    assert payload is not None
    assert payload.get("apiVersion") == 1

    seen_severities = set()
    for group in payload.get("groups", []):
        for rule in group.get("rules", []):
            assert rule.get("title"), "rule missing title"
            ann = rule.get("annotations", {})
            assert ann.get("runbook_url", "").endswith(".md"), (
                f"rule {rule.get('title')} missing runbook_url"
            )
            assert ann.get("summary")
            assert ann.get("description")
            severity = rule.get("labels", {}).get("severity")
            assert severity in {"warning", "critical"}, severity
            seen_severities.add(severity)

            for query in rule.get("data", []):
                model = query.get("model", {}) or {}
                expr = model.get("expr") or model.get("rawSql") or ""
                assert expr, f"rule {rule.get('title')} has empty query"
                if "expr" in model:
                    # PromQL queries: D151 _init filter required.
                    assert 'job="grace_init"' in model["expr"], (
                        f"rule {rule.get('title')} missing _init filter"
                    )
                if "rawSql" in model:
                    sql_lower = model["rawSql"].lower()
                    # Lint: every FROM clause references an allowlisted table.
                    for token in sql_lower.split():
                        if token in SQL_TABLE_ALLOWLIST:
                            break
                    else:
                        raise AssertionError(
                            f"rule {rule.get('title')} SQL does not reference "
                            f"any allowlisted table: {SQL_TABLE_ALLOWLIST}"
                        )

    # Both severity tiers must be present (D253).
    assert seen_severities == {"warning", "critical"}


def test_contact_points_inject_admin_key_header():
    payload = yaml.safe_load(
        (PROVISIONING_DIR / "contact-points.yaml").read_text()
    )
    receivers = payload["contactPoints"][0]["receivers"]
    assert receivers, "no receivers defined"
    settings = receivers[0]["settings"]
    assert settings["httpHeaderName1"] == "X-Admin-Key", (
        "Grafana 11.3 keyed-deployment requirement (D249) — webhook must "
        "inject X-Admin-Key"
    )
    assert "httpHeaderValue1" in settings


def test_notification_policy_groups_by_alertname_severity_module():
    payload = yaml.safe_load(
        (PROVISIONING_DIR / "notification-policies.yaml").read_text()
    )
    policy = payload["policies"][0]
    assert set(policy["group_by"]) == {"alertname", "severity", "ontology_module"}
