"""EC-8..EC-12 ethical constraint tests for the Reconciliation Layer (Chunk 36, D273).

One test per UI framing rule from ``GrACE-Reconciliation-Layer.docx`` §6.1.
The framing rules are:

  1. Mirror not accusation                        → EC-11
  2. Evidence vs perception (not reality vs belief) → EC-8 (forbidden vocab)
  3. Gaps as opportunities                        → EC-12
  4. Evidence one click away                      → EC-10
  5. Cross-executive comparisons require explicit permission → EC-9

Pattern mirrors ``tests/elicitation/test_ec_constraints.py`` (EC-1..EC-7,
Chunk 34 D262): static scans + Pydantic model introspection, no live
LLM calls, deterministic.

Status: STUB. The Reconciliation Layer surfaces land in Chunk 36; tests
that need production targets are marked ``pytest.skip`` with the
unblock condition. Static-scan tests (EC-8, EC-11, EC-12) run today and
will catch drift the moment Reconciliation copy is added to the repo.

The forbidden-vocabulary list (EC-8) is the load-bearing test — it
codifies the "internal-only ERD terminology" decision and prevents
``drift`` / ``mistake`` / ``blind spot`` from leaking into user-facing
surfaces. The ledger of disallowed terms is in ``FORBIDDEN_VOCAB`` below
and is the single source of truth; updates require an explicit
amendment to D273.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# EC-8 — Forbidden-vocabulary scan on user-facing Reconciliation surfaces.
# ---------------------------------------------------------------------------
#
# The internal metric is "Executive Reality Drift" (ERD); the user-facing
# label is "evidence grounding score". The word "drift" never appears in
# any UI surface, alert copy, error message, or Pydantic Field
# description that can render to a user. This test is the CI guardrail.
#
# Surfaces scanned:
#   - frontend/components/reconciliation/**
#   - frontend/app/reconciliation/**
#   - src/api/reconciliation_routes.py (when it exists)
#   - src/reconciliation/** Pydantic Field descriptions and prose strings
#
# Exempted (where the term may legitimately appear):
#   - This test file itself (test code references the rule)
#   - tests/** in general
#   - docs/** (docs explain the rule and use the term)
#   - Internal-only ERD computation code, marked with a
#     ``# noqa: ERD-internal-only`` comment


FORBIDDEN_VOCAB = (
    "drift",
    "blind spot",
    "blindspot",
    "mistake",
    "wrong",
    "bad",
    "fault",
    "lie",
    "lying",
    "error",       # NB: "Error" capitalized in technical exception names is allowed by the regex below
    "delusion",
    "deceive",
    "deception",
    "incorrect",
)


# Allow-list: any path that can legitimately contain forbidden vocab.
# Update with care — broadening this set defeats the purpose of EC-8.
EC8_EXEMPT_PATHS = (
    "tests/",
    "docs/",
    "scripts/",
    "alembic/versions/",
    # Internal ERD computation and analytics code is allowed to use
    # "drift" in metric names (e.g., grace_executive_reality_drift_*)
    # but only inside files that opt-in via a sentinel comment.
)

EC8_INTERNAL_OPT_IN_SENTINEL = "# noqa: ERD-internal-only"


def _ec8_iter_surface_files():
    """Yield (path, text) for every file that EC-8 must scan.

    Frontend Reconciliation routes/components plus backend Reconciliation
    Python modules. Existence-tolerant — pre-Chunk-36, the directories
    do not exist yet, so the iterator is empty and the test passes
    trivially. When Chunk 36 lands the surfaces, the scan starts
    catching drift automatically.
    """
    targets = [
        REPO_ROOT / "frontend" / "components" / "reconciliation",
        REPO_ROOT / "frontend" / "app" / "reconciliation",
        REPO_ROOT / "src" / "reconciliation",
        REPO_ROOT / "src" / "api" / "reconciliation_routes.py",
    ]
    for target in targets:
        if not target.exists():
            continue
        if target.is_file():
            yield target, target.read_text(encoding="utf-8", errors="ignore")
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in (".py", ".tsx", ".ts", ".js", ".jsx"):
                continue
            yield path, path.read_text(encoding="utf-8", errors="ignore")


def test_ec8_forbidden_vocabulary_absent_from_reconciliation_surfaces():
    """EC-8: forbidden vocabulary never appears in user-facing surfaces.

    Static scan across Reconciliation frontend and backend code. Any
    file that contains ``# noqa: ERD-internal-only`` is exempted (these
    are internal computation modules, not user-facing surfaces).

    Pre-Chunk-36 this test passes trivially because no Reconciliation
    surfaces exist yet; the test is the CI guardrail that engages the
    moment Chunk 36 lands.
    """
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in FORBIDDEN_VOCAB) + r")\b",
        flags=re.IGNORECASE,
    )

    violations: list[tuple[str, int, str, str]] = []
    for path, text in _ec8_iter_surface_files():
        if EC8_INTERNAL_OPT_IN_SENTINEL in text:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped_lower = line.strip().lower()
            # Skip documentation comments, code-internal exception names,
            # and import statements where "Error" appears in class names.
            if stripped_lower.startswith(("#", "//", "*", "/*", '"""')):
                continue
            match = pattern.search(line)
            if match is None:
                continue
            # Allow "Error" inside Python exception class names like
            # ``RetrievalStageError``. Lowercase "error" in user copy is
            # what we forbid.
            if match.group(1) == "Error" and re.search(r"[A-Z][A-Za-z]*Error", line):
                continue
            violations.append((str(path.relative_to(REPO_ROOT)), line_no, match.group(0), line.strip()))

    assert not violations, (
        "EC-8 violation: forbidden vocabulary found in Reconciliation user-facing "
        "surfaces. The internal ERD term must never reach the DOM. "
        f"Violations:\n" + "\n".join(f"  {p}:{ln} → '{w}' in: {ctx}" for p, ln, w, ctx in violations)
    )


# ---------------------------------------------------------------------------
# EC-9 — Cross-executive comparison endpoints require explicit permission.
# ---------------------------------------------------------------------------
#
# The Reconciliation Bridge surfaces cross-executive divergence maps.
# Per the Reconciliation Layer spec §6.1 rule 5, the comparison endpoints
# MUST check an explicit-permission flag (not a default-allow ACL) before
# returning cross-executive content.
#
# This test imports the route handlers from src/api/reconciliation_routes.py
# (when they exist) and asserts that handlers tagged ``cross_executive=True``
# in the route metadata also have a permission check that consults the
# Permission Matrix's ``cross_executive_comparison`` capability.


@pytest.mark.skip(reason="Implementation lands in Chunk 36 (D273). Stub registers the contract.")
def test_ec9_cross_executive_endpoints_require_explicit_permission():
    """EC-9: cross-executive comparison endpoints check explicit permission.

    When Chunk 36 lands ``src/api/reconciliation_routes.py``, this test
    introspects each route handler tagged ``cross_executive=True`` and
    verifies it calls into ``check_permission(actor, "cross_executive_comparison")``
    before returning. Failure = explicit permission check missing.

    Until Chunk 36 lands the route module, this test is skipped.
    """
    # Implementation lands at Chunk 36 close. Skeleton:
    #
    #   from src.api import reconciliation_routes
    #   from src.permissions.matrix import check_permission
    #
    #   for route in reconciliation_routes.router.routes:
    #       if route.endpoint.__metadata__.get("cross_executive"):
    #           source = inspect.getsource(route.endpoint)
    #           assert "check_permission" in source
    #           assert '"cross_executive_comparison"' in source
    pass


# ---------------------------------------------------------------------------
# EC-10 — Every Gap Report response carries an evidence-link payload.
# ---------------------------------------------------------------------------
#
# "Evidence one click away" rule. Gap Report responses MUST include a
# field that points at the underlying graph evidence (grace_id list,
# query event id, or both). A response that surfaces a gap without a
# way to inspect the evidence is a violation.
#
# This test introspects the Gap Report Pydantic response model and
# asserts a non-optional ``evidence_links`` field is present.


@pytest.mark.skip(reason="Implementation lands in Chunk 36 (D273). Stub registers the contract.")
def test_ec10_gap_report_response_carries_evidence_link():
    """EC-10: GapReportResponse model has a required evidence-link field.

    When Chunk 36 lands ``src/reconciliation/models.py``, this test
    imports ``GapReportResponse`` and asserts the model has a non-
    Optional ``evidence_links`` field (or equivalently named: the Chunk
    36 spec author may rename, but the contract holds).

    Until Chunk 36 lands the model, this test is skipped.
    """
    # Implementation lands at Chunk 36 close. Skeleton:
    #
    #   from src.reconciliation.models import GapReportResponse
    #
    #   fields = GapReportResponse.model_fields
    #   assert "evidence_links" in fields
    #   assert fields["evidence_links"].is_required()
    pass


# ---------------------------------------------------------------------------
# EC-11 — Mirror-not-accusation tone on report copy.
# ---------------------------------------------------------------------------
#
# Reconciliation reports use second-person ("here is what your evidence
# shows") rather than third-person accusatory ("the executive is wrong").
# This test scans report copy templates for second-person framing and
# rejects accusatory-tone patterns.
#
# Pattern check: report templates must not contain ``the executive``
# in subject position followed by a negative-attribution verb (was wrong,
# is incorrect, fails to, lacks, missed). Scans templates as they land.


def test_ec11_report_copy_avoids_accusatory_third_person():
    """EC-11: Reconciliation report copy uses mirror, not accusation.

    Static regex scan of every ``*.template.md`` and ``*.j2`` file under
    ``src/reconciliation/templates/``. Pre-Chunk-36 the directory does
    not exist; test passes trivially. When templates land, the scan
    catches accusatory-tone patterns automatically.
    """
    template_dir = REPO_ROOT / "src" / "reconciliation" / "templates"
    if not template_dir.exists():
        # Pre-Chunk-36; nothing to scan.
        return

    accusatory_pattern = re.compile(
        r"\bthe (executive|leader|reviewer|user)\s+(is|was|has|fails|misses|missed|lacks|lacked|got\s+\w+\s+wrong)\b",
        flags=re.IGNORECASE,
    )

    violations: list[tuple[str, int, str]] = []
    for path in template_dir.rglob("*"):
        if path.suffix not in (".md", ".j2", ".jinja2", ".tmpl"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = accusatory_pattern.search(line)
            if match:
                violations.append((str(path.relative_to(REPO_ROOT)), line_no, match.group(0)))

    assert not violations, (
        "EC-11 violation: accusatory-tone pattern found in Reconciliation report "
        "copy. Use second-person framing ('here is what your evidence shows') "
        "rather than third-person accusatory framing.\n"
        + "\n".join(f"  {p}:{ln} → '{m}'" for p, ln, m in violations)
    )


# ---------------------------------------------------------------------------
# EC-12 — Gaps-as-opportunities framing in alerting copy.
# ---------------------------------------------------------------------------
#
# Alert payloads emitted by the correlation engine for Reconciliation-
# pattern alerts must frame gaps as opportunities rather than
# deficiencies. Concretely: alert summary text must not contain
# "deficiency", "shortcoming", "failure", or "gap" without a paired
# "opportunity" or "to investigate" framing.
#
# Implementation: scans alert templates in
# ``docker/grafana/provisioning/alerting/`` and any Python alert-payload
# constants in ``src/analytics/correlation_engine/alerts/``.


def test_ec12_alert_copy_frames_gaps_as_opportunities():
    """EC-12: alert copy for Reconciliation-pattern alerts uses opportunity framing.

    Static regex scan of Grafana alerting provisioning files plus the
    correlation_engine alerts module (when Reconciliation-pattern alerts
    are added). Catches deficiency-framed copy that lacks a paired
    opportunity framing.
    """
    targets = [
        REPO_ROOT / "docker" / "grafana" / "provisioning" / "alerting",
        REPO_ROOT / "src" / "analytics" / "correlation_engine" / "alerts",
    ]

    deficiency_pattern = re.compile(
        r"\b(deficiency|shortcoming|failure|missing\s+evidence)\b",
        flags=re.IGNORECASE,
    )
    opportunity_pattern = re.compile(
        r"\b(opportunity|to\s+investigate|to\s+explore|reconcile|surface)\b",
        flags=re.IGNORECASE,
    )

    violations: list[tuple[str, int, str]] = []
    for target in targets:
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in (".yaml", ".yml", ".py", ".json", ".tmpl"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            # Skip files that don't reference the Reconciliation pattern set.
            # EC-12 scope is limited to Reconciliation alerts; other alert
            # families (extraction-quality, etc.) have their own framing.
            if "reconciliation" not in text.lower():
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if deficiency_pattern.search(line) and not opportunity_pattern.search(line):
                    violations.append((str(path.relative_to(REPO_ROOT)), line_no, line.strip()))

    assert not violations, (
        "EC-12 violation: alert copy frames gaps as deficiencies without a "
        "paired opportunity framing.\n"
        + "\n".join(f"  {p}:{ln} → {ctx}" for p, ln, ctx in violations)
    )
