"""Sensitivity Classification Report generator (Chunk 43, CP3 / D344).

Pure-function ``generate(matrix, *, scope_filter=None)`` that walks the
active ``PermissionMatrix`` and produces a render-only
``SensitivityClassificationReport``:

* ``tag_inventory`` — one row per distinct tag, with rule_count and
  cluster_count rollups.
* ``coverage_breakdown`` — one row per (resource_kind, action) cell
  with total/tagged rule counts.
* ``untagged_rules`` — capped at ``UNTAGGED_RULES_CAP`` (1000) entries,
  with ``truncated=True`` when the cap is hit.
* ``tag_hygiene_findings`` — Levenshtein <= 2 near-duplicate tag names
  surfaced for operator deduplication.
* ``coverage_band`` — three-band label derived from ``coverage_score``;
  ``coverage_score`` is server-side only and MUST NOT appear in any
  API response body (D120/D217 — read paths exclude the field).

Hard invariants (D270 / D343 — load-bearing):

* No DB I/O. Pure function over the in-memory matrix object.
* No admission logic. The Sensitivity Gate does not decide; it merely
  re-renders coverage statistics over rules already authored on the
  Chunk 42 matrix.
* No import of ``src.permissions.enforcer``.

Below-floor carve-out (mirrors Chunk 37 DR pattern): when the matrix
carries zero ``SensitivityTag`` entries anywhere, ``coverage_band`` and
``coverage_score`` are ``None`` and ``corpus_below_floor=True`` — the
report still persists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from src.permissions.models import (
    CoverageBand,
    CoverageBreakdownEntry,
    FrameworkMapping,
    PermissionMatrix,
    SensitivityClassificationReport,
    TagHygieneFinding,
    TagInventoryEntry,
    UntaggedRuleEntry,
)


UNTAGGED_RULES_CAP = 1000
"""Maximum number of ``UntaggedRuleEntry`` rows on a single report.
``truncated=True`` when the underlying matrix exceeds the cap."""

LEVENSHTEIN_HYGIENE_THRESHOLD = 2
"""Tag-name pairs with Levenshtein distance <= this threshold are
surfaced as ``TagHygieneFinding`` rows."""

_HIGH_BAND_THRESHOLD = 0.80
_MEDIUM_BAND_THRESHOLD = 0.50


def classify_band(score: float | None) -> CoverageBand | None:
    """Map a numeric coverage score onto its band label.

    Returns ``None`` when the input is ``None`` (below-floor case).
    """
    if score is None:
        return None
    if score >= _HIGH_BAND_THRESHOLD:
        return "high"
    if score >= _MEDIUM_BAND_THRESHOLD:
        return "medium"
    return "low"


def _levenshtein(a: str, b: str) -> int:
    """Standard Wagner-Fischer Levenshtein distance.

    Pure-Python; the tag-name vocabulary is small (a few dozen names per
    matrix in practice) so the O(len_a * len_b) cost is negligible.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                curr[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + cost,
            )
        prev = curr
    return prev[-1]


def _detect_tag_hygiene(
    tag_names: Iterable[str],
) -> list[TagHygieneFinding]:
    """Pairwise Levenshtein <= ``LEVENSHTEIN_HYGIENE_THRESHOLD`` finder.

    Returns a deterministically-ordered list (sorted by ``(tag_name,
    similar_to)``) so callers can hash or diff the report payload
    safely. Each unordered pair is reported once: the lexicographically
    smaller name is ``tag_name`` and the larger is ``similar_to``.
    """
    names = sorted({n for n in tag_names if n})
    findings: list[TagHygieneFinding] = []
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            distance = _levenshtein(left, right)
            if 0 < distance <= LEVENSHTEIN_HYGIENE_THRESHOLD:
                findings.append(
                    TagHygieneFinding(
                        tag_name=left,
                        similar_to=right,
                        distance=distance,
                    )
                )
    return findings


def _matches_scope_filter(
    rule_resource_kind: str,
    rule_resource_label: str,
    scope_filter: dict[str, str] | None,
) -> bool:
    """Apply an optional scope filter to a (resource_kind, resource_label) pair."""
    if scope_filter is None:
        return True
    rk = scope_filter.get("resource_kind")
    if rk is not None and rk != rule_resource_kind:
        return False
    rl = scope_filter.get("resource_label")
    if rl is not None and rl != rule_resource_label:
        return False
    return True


def generate(
    matrix: PermissionMatrix,
    *,
    permission_matrix_id: UUID,
    generated_at: datetime | None = None,
    scope_filter: dict[str, str] | None = None,
) -> SensitivityClassificationReport:
    """Walk ``matrix`` and produce a ``SensitivityClassificationReport``.

    Args:
        matrix: The active ``PermissionMatrix``. Treated as immutable.
        permission_matrix_id: PK of the persisted matrix row this
            report belongs to (FK target on
            ``sensitivity_classification_reports``).
        generated_at: Override for the report timestamp. Defaults to
            ``datetime.now(tz=timezone.utc)``; tests pass an explicit
            value for determinism.
        scope_filter: Optional ``{resource_kind, resource_label}``
            narrowing. Rules failing the filter are excluded from every
            section (inventory, breakdown, untagged).

    Returns:
        A populated ``SensitivityClassificationReport``. Below-floor
        case (no tags anywhere) returns ``coverage_band=None``,
        ``coverage_score=None``, ``corpus_below_floor=True`` — the
        report still persists.
    """
    when = generated_at or datetime.now(tz=timezone.utc)

    # tag_name → (rule_count, set(cluster_id), {framework_mapping_key:
    # FrameworkMapping}). Framework mappings are deduplicated by
    # (framework, code) tuple so the inventory row reflects the
    # canonical set.
    inventory: dict[
        str,
        dict,
    ] = {}

    # (resource_kind, action) → [total, tagged]
    breakdown: dict[tuple[str, str], list[int]] = {}

    untagged: list[UntaggedRuleEntry] = []
    truncated = False
    total_rules = 0
    tagged_rules = 0

    for cluster in matrix.role_clusters:
        for rule in cluster.access_rules:
            if not _matches_scope_filter(
                rule.resource_kind,
                rule.resource_label,
                scope_filter,
            ):
                continue
            total_rules += 1
            cell_key = (rule.resource_kind, rule.action)
            cell = breakdown.setdefault(cell_key, [0, 0])
            cell[0] += 1

            if rule.sensitivity_tags:
                tagged_rules += 1
                cell[1] += 1
                for tag in rule.sensitivity_tags:
                    entry = inventory.setdefault(
                        tag.name,
                        {
                            "rule_count": 0,
                            "clusters": set(),
                            "framework_codes": {},
                        },
                    )
                    entry["rule_count"] += 1
                    entry["clusters"].add(cluster.cluster_id)
                    for fm in tag.framework_mappings:
                        key = (fm.framework, fm.code)
                        entry["framework_codes"].setdefault(
                            key,
                            FrameworkMapping(framework=fm.framework, code=fm.code),
                        )
            else:
                if len(untagged) < UNTAGGED_RULES_CAP:
                    untagged.append(
                        UntaggedRuleEntry(
                            cluster_id=cluster.cluster_id,
                            cluster_display_name=cluster.display_name,
                            resource_kind=rule.resource_kind,
                            resource_label=rule.resource_label,
                            action=rule.action,
                        )
                    )
                else:
                    truncated = True

    tag_inventory_rows = [
        TagInventoryEntry(
            tag_name=name,
            rule_count=info["rule_count"],
            cluster_count=len(info["clusters"]),
            framework_codes=sorted(
                info["framework_codes"].values(),
                key=lambda fm: (fm.framework, fm.code),
            ),
        )
        for name, info in sorted(inventory.items(), key=lambda kv: kv[0])
    ]

    coverage_breakdown_rows = [
        CoverageBreakdownEntry(
            resource_kind=rk,  # type: ignore[arg-type]
            action=ac,  # type: ignore[arg-type]
            total_rule_count=cell[0],
            tagged_rule_count=cell[1],
        )
        for (rk, ac), cell in sorted(breakdown.items(), key=lambda kv: kv[0])
    ]

    if total_rules == 0 or tagged_rules == 0:
        # Below-floor carve-out: report persists, but bands/score are
        # null. ``corpus_below_floor`` is True when the matrix carries
        # no tags at all; an empty matrix (no rules) is also below-floor
        # because there is nothing to classify.
        return SensitivityClassificationReport(
            permission_matrix_id=permission_matrix_id,
            generated_at=when,
            tag_inventory=tag_inventory_rows,
            coverage_breakdown=coverage_breakdown_rows,
            untagged_rules=untagged,
            truncated=truncated,
            coverage_band=None,
            coverage_score=None,
            corpus_below_floor=True,
            tag_hygiene_findings=_detect_tag_hygiene(inventory.keys()),
        )

    coverage_score = tagged_rules / total_rules
    coverage_band = classify_band(coverage_score)

    return SensitivityClassificationReport(
        permission_matrix_id=permission_matrix_id,
        generated_at=when,
        tag_inventory=tag_inventory_rows,
        coverage_breakdown=coverage_breakdown_rows,
        untagged_rules=untagged,
        truncated=truncated,
        coverage_band=coverage_band,
        coverage_score=coverage_score,
        corpus_below_floor=False,
        tag_hygiene_findings=_detect_tag_hygiene(inventory.keys()),
    )


__all__ = [
    "classify_band",
    "generate",
    "LEVENSHTEIN_HYGIENE_THRESHOLD",
    "UNTAGGED_RULES_CAP",
]
