"""Reconciliation Layer alert-copy registry (Chunk 37, D289 / EC-12).

Every Grafana / dashboard alert that touches the Reconciliation Layer
sources its user-facing strings from :data:`ALERT_COPY_REGISTRY`. The
registry is scanned by:

* ``tests/recon/test_alert_copy_registry.py`` — non-empty + no
  ``_RECON_FORBIDDEN_TOKENS`` substring (case-insensitive).
* ``tests/elicitation/test_ec_constraints.py::test_ec_12`` — same
  forbidden-token discipline.

EVERY string in this module is opportunity-framed. Storage columns,
Prometheus instrument names, and internal docstrings are not under
this guard — only the user-facing ``summary`` and ``recommendation``
strings.
"""

from __future__ import annotations

from typing import TypedDict


class AlertCopy(TypedDict):
    """User-facing alert copy. ``summary`` is the headline; ``recommendation``
    is the next-best-action paragraph an operator reads in Grafana."""

    summary: str
    recommendation: str


ALERT_COPY_REGISTRY: dict[str, AlertCopy] = {
    "evidence_grounding_below_threshold": {
        "summary": (
            "The current ratified schema's evidence-grounding score is "
            "below the configured threshold."
        ),
        "recommendation": (
            "Open the Gap Report for this session to see which choices "
            "would benefit from stronger evidence, and where the corpus "
            "could be enriched."
        ),
    },
    "graph_population_below_floor": {
        "summary": (
            "The graph for this segment holds fewer instances than the "
            "configured population floor."
        ),
        "recommendation": (
            "Adding documents or running an extraction pass is an "
            "opportunity to enrich the next Documented Reality Report."
        ),
    },
    "documented_reality_report_due": {
        "summary": (
            "A Documented Reality Report has not been generated within "
            "the configured cadence."
        ),
        "recommendation": (
            "Open the schedule editor and either trigger an on-demand "
            "report or adjust the cadence to fit the team's review rhythm."
        ),
    },
    "divergence_map_unviewed": {
        "summary": (
            "A new Cross-Executive Divergence Map is available but has "
            "not been viewed."
        ),
        "recommendation": (
            "Reviewing the map together is an opportunity to align "
            "emphasis between the two reviewers and grow the consensus "
            "core for the next session."
        ),
    },
}


__all__ = ["AlertCopy", "ALERT_COPY_REGISTRY"]
