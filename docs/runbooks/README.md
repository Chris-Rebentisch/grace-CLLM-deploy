# GrACE Runbooks

This directory holds operator runbooks linked from Grafana Unified Alerting
rules (Chunk 33, D249/D253). Each rule's `annotations.runbook_url` points
to a per-rule markdown file in this directory.

## Status

Stub — runbook bodies land in a future chunk. Until then, alert payloads
arriving at `POST /api/analytics/alerts/_internal` are persisted to
`alert_events` and the on-call human is expected to consult the dashboard
(`grace-correlations-overview`) and the offending pattern's
`diagnostic_records` row directly.

## Filename convention

`<alertname>.md` (lowercase). One file per Grafana alert rule. Each file
documents:

1. What the rule fires on (PromQL / SQL).
2. Likely root causes by `suspected_root_cause_module`.
3. Recommended diagnostic queries (read-only).
4. Recovery actions and rollback paths.
5. Severity escalation criteria.

## Currently planned runbooks

(All currently TODO; populated by a future docs chunk.)

- `mineretentiondrop.md`
- `unclassifiedentityratehigh.md`
- `retrievalzeroresultsspike.md`
- `correlationextractionqualityproblemhigh.md`
- `correlationgraphorindexproblemhigh.md`
- `correlationschemadriftpermodulehigh.md`
- `correlationcqregressionpreextractionhigh.md`
- `correlationrelationshipgappropagationhigh.md`
