"""Progress and SI satisfaction aggregates (Chunk 39, D302)."""

from __future__ import annotations

from typing import Any


def compute_progress_percentage(criteria_results: list[dict[str, Any]]) -> float:
    """Mean of ``satisfied`` cast to {0, 1}. Empty list → 0.0."""
    if not criteria_results:
        return 0.0
    total = 0.0
    for c in criteria_results:
        sat = c.get("satisfied")
        total += 1.0 if bool(sat) else 0.0
    return total / len(criteria_results)


def compute_criteria_all_satisfied(
    criteria_results: list[dict[str, Any]], tier: str
) -> bool | None:
    """SI → bool whether every criterion satisfied; OA → None."""
    if tier != "Strategic_Initiative":
        return None
    if not criteria_results:
        return False
    return all(bool(c.get("satisfied")) for c in criteria_results)
