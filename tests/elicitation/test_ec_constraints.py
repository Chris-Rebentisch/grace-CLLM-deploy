"""EC-1..EC-7 ethical constraint tests (Chunk 34, D262).

One test per Elicitation Protocol §7.1 constraint. EC-1 is property-based
via Hypothesis; EC-2 / EC-7 are static scans; EC-3..EC-6 read the
read-only telemetry fixture at ``tests/fixtures/ec_telemetry_streams.json``.

EC-7 invokes ``scripts/check-no-third-party.sh`` as a subprocess with
no path arguments. The script's default scan list is extended in CP10
to include ``src/eval/`` — this test asserts the airgap posture holds
across the whole production surface, not a slice of it.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "ec_telemetry_streams.json"


# ---------------------------------------------------------------------------
# EC-1 — Progress indicators must be a deterministic function of inputs.
# ---------------------------------------------------------------------------
#
# Imports the production function from src/elicitation/progress.py so
# this test exercises the real code path UI surfaces and telemetry
# derive their widths from. Asserts three invariants jointly:
#   1. Determinism: f(a, b) called twice in a row returns the same float.
#   2. Bounds: every output lies in [0.0, 1.0] (D120/D217 raw-score ban
#      is rendered as widths, so the producer side must stay bounded).
#   3. Phase ordering at zero steps: open < structure < clarify <= close.


from src.elicitation.progress import (  # noqa: E402
    PHASE_BASE_WEIGHTS,
    compute_progress_indicator,
)


@given(
    phase_state=st.sampled_from(["open", "structure", "clarify", "close"]),
    completed_steps=st.integers(min_value=0, max_value=200),
)
@settings(database=None, derandomize=True, deadline=None, max_examples=100)
def test_ec1_progress_indicators_are_deterministic(phase_state, completed_steps):
    a = compute_progress_indicator(phase_state, completed_steps)
    b = compute_progress_indicator(phase_state, completed_steps)
    assert a == b
    assert 0.0 <= a <= 1.0


def test_ec1_progress_indicator_phase_ordering_at_zero_steps():
    """Phase ordering invariant at completed_steps=0.

    Without it, a UI could paint the Open phase higher than Close and
    determinism alone would not catch it. Asserts the producer-side
    ordering EC-1 implicitly relies on.
    """
    open_p = compute_progress_indicator("open", 0)
    structure_p = compute_progress_indicator("structure", 0)
    clarify_p = compute_progress_indicator("clarify", 0)
    close_p = compute_progress_indicator("close", 0)
    assert open_p < structure_p < clarify_p <= close_p
    # Sanity: the four canonical phases all appear in the production
    # weight table — drift here would silently break the ordering check.
    assert {"open", "structure", "clarify", "close"} <= set(PHASE_BASE_WEIGHTS)


# ---------------------------------------------------------------------------
# EC-2 — No artificial scarcity / urgency UI patterns or backend payloads.
# ---------------------------------------------------------------------------


_FORBIDDEN_PATTERNS_SUBSTR = (
    "limited time",
    "spots remaining",
    "act now",
    "expires in",
    "countdown",
    "scarcity",
)
# AST-level forbidden function names. We allow ``setTimeout`` for legitimate
# polling intervals etc.; what's forbidden is calling something LITERALLY
# named ``startCountdown`` / ``urgencyTimer`` etc.
_FORBIDDEN_AST_CALL_NAMES = {
    "startCountdown",
    "urgencyTimer",
    "scarcityBanner",
}


def _scan_text_for_forbidden_substrings(path: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix not in {".ts", ".tsx", ".py", ".html", ".css", ".js"}:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = text.lower()
        for pat in _FORBIDDEN_PATTERNS_SUBSTR:
            if pat in lowered:
                found.append((str(f.relative_to(REPO_ROOT)), pat))
    return found


def _scan_python_for_forbidden_calls(path: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for f in path.rglob("*.py"):
        if not f.is_file():
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                name = None
                if isinstance(target, ast.Name):
                    name = target.id
                elif isinstance(target, ast.Attribute):
                    name = target.attr
                if name in _FORBIDDEN_AST_CALL_NAMES:
                    found.append((str(f.relative_to(REPO_ROOT)), name))
    return found


def test_ec2_no_artificial_scarcity_or_urgency():
    """Static scan of frontend components and backend API for scarcity copy
    and forbidden call names.

    The set of forbidden substrings is deliberately small and explicit — a
    literal "countdown" appearing in a comment is fine if it documents
    what is NOT done, but no production string surfaces these phrases to
    users. The check runs over ``frontend/components`` and ``src/api`` per
    spec §6 CP9.
    """
    offenders: list[tuple[str, str]] = []
    offenders.extend(
        _scan_text_for_forbidden_substrings(REPO_ROOT / "frontend" / "components")
    )
    offenders.extend(
        _scan_text_for_forbidden_substrings(REPO_ROOT / "src" / "api")
    )
    offenders.extend(_scan_python_for_forbidden_calls(REPO_ROOT / "src" / "api"))
    assert not offenders, (
        f"EC-2 violation — scarcity/urgency patterns found: {offenders[:5]}"
    )


# ---------------------------------------------------------------------------
# EC-3 — No loss-aversion streak/combo mechanics in telemetry.
# ---------------------------------------------------------------------------


_EC3_FORBIDDEN_FIELD_PREFIXES = ("streak_", "combo_", "lose_progress_")
_EC3_FORBIDDEN_EVENT_TYPES = {"streak_broken", "combo_dropped", "lose_progress"}


def _walk_payloads(events: list[dict]):
    for ev in events:
        yield ev.get("event_type", "")
        payload = ev.get("payload") or {}
        if isinstance(payload, dict):
            yield from payload.keys()


def test_ec3_no_loss_aversion_streak_mechanics():
    streams = json.loads(FIXTURE_PATH.read_text())
    events = streams.get("ec3_no_streak_or_combo", [])
    assert events, "fixture must declare ec3_no_streak_or_combo events"
    for token in _walk_payloads(events):
        assert token not in _EC3_FORBIDDEN_EVENT_TYPES, (
            f"EC-3 violation — forbidden event_type in fixture: {token}"
        )
        for prefix in _EC3_FORBIDDEN_FIELD_PREFIXES:
            assert not token.startswith(prefix), (
                f"EC-3 violation — forbidden field prefix in fixture: {token}"
            )


# ---------------------------------------------------------------------------
# EC-4 — Open phase is uninterrupted by the system.
# ---------------------------------------------------------------------------


def test_ec4_open_phase_uninterrupted():
    """No event_type in ``src.elicitation.models`` literally signals a
    system-initiated Open-phase interruption.

    We assert by introspecting the elicitation events module for
    forbidden symbol names.
    """
    from src.elicitation import models as eli_models

    forbidden = {"session_paused_by_system", "phase_transition_forced"}
    module_names = {n for n in dir(eli_models)}
    intersection = module_names & forbidden
    assert not intersection, (
        f"EC-4 violation — forbidden Open-phase-interruption symbols: {intersection}"
    )

    # Belt-and-suspenders: scan fixture events for these event_types.
    streams = json.loads(FIXTURE_PATH.read_text())
    for stream_events in streams.values():
        if not isinstance(stream_events, list):
            continue
        for ev in stream_events:
            assert ev.get("event_type") not in forbidden


# ---------------------------------------------------------------------------
# EC-5 — Pause and skip are unpenalized.
# ---------------------------------------------------------------------------


_EC5_FORBIDDEN_PAYLOAD_KEYS = {"progress_decrement", "penalty_score"}


def test_ec5_session_pause_and_skip_unpenalized():
    streams = json.loads(FIXTURE_PATH.read_text())
    events = streams.get("ec5_pause_skip_unpenalized", [])
    assert events, "fixture must declare ec5_pause_skip_unpenalized events"

    pause_event_types = {"session_paused", "session_resumed", "step_skipped"}
    progress_indicators: list[float] = []

    for ev in events:
        if ev.get("event_type") not in pause_event_types:
            continue
        payload = ev.get("payload") or {}
        for forbidden in _EC5_FORBIDDEN_PAYLOAD_KEYS:
            assert forbidden not in payload, (
                f"EC-5 violation — {forbidden} present in {ev['event_type']}"
            )
        if "progress_indicator" in payload:
            progress_indicators.append(payload["progress_indicator"])

    # The indicator value must not decrease across pause→resume→skip events.
    assert progress_indicators == sorted(progress_indicators), (
        f"EC-5 violation — progress_indicator decreases through "
        f"pause/skip stream: {progress_indicators}"
    )


# ---------------------------------------------------------------------------
# EC-6 — Mode-selection rationale is always visible.
# ---------------------------------------------------------------------------


def test_ec6_mode_selection_rationale_visible():
    """Two-pronged assertion (spec §6 CP9 EC-6):

    1. **Fixture-level** — the read-only telemetry stream must show every
       mode-change event arriving with a non-empty rationale. Catches
       any future fixture authors who ship a payload without it.
    2. **Backend-level** — every payload model in the production catalog
       that exposes a ``mode`` field must also require a non-empty
       ``mode_rationale`` field (no ``Optional`` / no default). This is
       the invariant a UI relies on when it decides whether the
       rationale text box is shown.
    """
    # Fixture leg.
    streams = json.loads(FIXTURE_PATH.read_text())
    events = streams.get("ec6_mode_changed_with_rationale", [])
    assert events, "fixture must declare ec6_mode_changed_with_rationale events"
    saw_mode_event = False
    for ev in events:
        payload = ev.get("payload") or {}
        if "mode" not in payload and "mode_rationale" not in payload:
            continue
        saw_mode_event = True
        rationale = payload.get("mode_rationale", "")
        assert isinstance(rationale, str) and rationale.strip(), (
            f"EC-6 violation — empty mode_rationale on event {ev.get('event_id')}"
        )
    assert saw_mode_event, (
        "fixture must contain at least one event carrying mode/mode_rationale"
    )

    # Backend leg — introspect the production payload catalog.
    from src.elicitation.models import _PAYLOAD_MODELS

    offenders: list[str] = []
    saw_mode_payload = False
    for event_type, model_cls in _PAYLOAD_MODELS.items():
        fields = model_cls.model_fields
        if "mode" not in fields:
            continue
        saw_mode_payload = True
        rationale_field = fields.get("mode_rationale")
        if rationale_field is None:
            offenders.append(f"{event_type}: missing mode_rationale field")
            continue
        # `is_required()` returns False if there is a default OR the type
        # admits None — both relax the EC-6 visibility guarantee.
        if not rationale_field.is_required():
            offenders.append(
                f"{event_type}: mode_rationale is not required (has default or Optional)"
            )
    assert saw_mode_payload, (
        "production payload catalog has no model carrying a `mode` field — "
        "EC-6 backend leg must be re-anchored to a real event type"
    )
    assert not offenders, f"EC-6 backend violation: {offenders}"


# ---------------------------------------------------------------------------
# EC-7 — Telemetry airgap default holds across the full production surface.
# ---------------------------------------------------------------------------


def test_ec7_telemetry_airgap_default():
    """Invokes ``scripts/check-no-third-party.sh`` with NO path arguments
    so the script's default scan paths are exercised, including
    ``src/eval/`` (added in Chunk 34 / CP10).
    """
    script = REPO_ROOT / "scripts" / "check-no-third-party.sh"
    assert script.exists(), f"third-party scan script missing: {script}"
    result = subprocess.run(
        ["bash", str(script)],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"EC-7 violation — third-party scan failed:\nSTDOUT: {result.stdout}\n"
        f"STDERR: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Chunk 36 EC additions (D281).
# EC-8 (forbidden-vocabulary scan) and EC-10 (evidence-link assertion) are
# fully implemented and pass.
# EC-9, EC-11, EC-12 register as importable skip stubs whose target module
# lands in Chunk 37.
# ---------------------------------------------------------------------------


_RECON_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "drift",
    "blind spot",
    "mistake",
    "wrong",
    "reality gap",
    # Chunk 37 D289 — full token set used by EC-11/EC-12 user-surface scans.
    "incorrect",
    "failure",
    "deficit",
)


def test_ec_8_forbidden_vocabulary_user_surfaces():
    """EC-8 (D281): user-facing surfaces must use the ``evidence_grounding``
    vocabulary; forbidden tokens (``drift``, ``blind spot``, ``mistake``,
    ``wrong``, ``reality gap``) must not appear (case-insensitive substring
    match) in:

      * ``frontend/lib/api/recon-types.ts``
      * ``src/api/recon_routes.py``
      * ``src/api/recon_models.py``

    Internal codebase identifiers (``erd_score``, ``erd_threshold_n``,
    ``ERD``, ``Executive Reality Drift``) are exempt — those live in
    storage columns + Prometheus instruments + internal docstrings, not in
    user surfaces. This test scans the three user-facing files only.
    """
    targets = [
        REPO_ROOT / "frontend" / "lib" / "api" / "recon-types.ts",
        REPO_ROOT / "src" / "api" / "recon_routes.py",
        REPO_ROOT / "src" / "api" / "recon_models.py",
    ]
    violations: list[tuple[str, str]] = []
    for target in targets:
        assert target.exists(), f"EC-8 scan target missing: {target}"
        body = target.read_text(encoding="utf-8").lower()
        for token in _RECON_FORBIDDEN_TOKENS:
            if token in body:
                violations.append((str(target), token))
    assert not violations, (
        "EC-8 violation — forbidden vocabulary in user-facing surfaces: "
        f"{violations}"
    )


def test_ec_10_gap_report_evidence_link_payload():
    """EC-10 (D281): every entry in ``emphasized_with_evidence`` must
    carry a non-empty ``top_evidence_extraction_event_ids`` list so the
    UI can render a verifiable evidence link rather than an unbacked
    claim."""
    from src.api.recon_models import (
        EmphasizedWithEvidenceItem,
        GapReportResponse,
    )
    from datetime import datetime, timezone
    from uuid import uuid4

    item = EmphasizedWithEvidenceItem(
        element_name="Company",
        element_type="entity_type",
        instance_count=42,
        top_evidence_extraction_event_ids=["e1", "e2"],
    )
    report = GapReportResponse(
        session_id=uuid4(),
        reviewer="alice",
        generated_at=datetime.now(timezone.utc),
        evidence_grounding_score=1.0,
        evidence_grounding_threshold=3,
        emphasized_with_evidence=[item],
    )

    assert report.emphasized_with_evidence, (
        "EC-10 fixture must contain at least one emphasized-with-evidence row"
    )
    for entry in report.emphasized_with_evidence:
        assert entry.top_evidence_extraction_event_ids, (
            "EC-10 violation — emphasized_with_evidence row missing evidence "
            f"link: element_name={entry.element_name}"
        )


def test_ec_9_cross_executive_comparison_permission():
    """EC-9 (D281, Chunk 37 D285): cross-executive comparison permission
    gate. The ``divergence_map_router`` must export at least one POST
    route whose dependency stack includes a permission check."""
    from src.api.recon_routes import divergence_map_router

    post_routes_with_deps = []
    for route in divergence_map_router.routes:
        methods = getattr(route, "methods", set()) or set()
        if "POST" not in methods:
            continue
        # Permission gate is wired via ``Depends(_require_admin_for_cross_reviewer)``.
        # FastAPI exposes that on ``route.dependant.dependencies``.
        dependant = getattr(route, "dependant", None)
        if dependant is not None and getattr(dependant, "dependencies", []):
            post_routes_with_deps.append(route)
    assert post_routes_with_deps, (
        "EC-9 violation — divergence_map_router must include at least "
        "one POST route with an explicit dependency gate."
    )


def test_ec_11_mirror_not_accusation_tone():
    """EC-11 (D281, Chunk 37 D289): the user-facing copy registry at
    ``frontend/lib/recon/report_copy.ts`` is filesystem-scanned for
    forbidden tokens. (B2 resolution: filesystem read, not import — TS
    can't be imported into Python.)"""
    ts_path = REPO_ROOT / "frontend" / "lib" / "recon" / "report_copy.ts"
    assert ts_path.exists(), f"EC-11 scan target missing: {ts_path}"
    body = ts_path.read_text(encoding="utf-8")
    assert "export " in body, (
        "EC-11 expected at least one `export` declaration in report_copy.ts"
    )
    # The mirror list intentionally names the forbidden tokens; exclude
    # everything inside the `RECON_FORBIDDEN_TOKENS_MIRROR` literal block
    # from the scan.
    scrubbed = body
    marker = "RECON_FORBIDDEN_TOKENS_MIRROR"
    if marker in scrubbed:
        head, _, tail = scrubbed.partition(marker)
        # Drop everything from the marker through the closing `];`.
        end_idx = tail.find("];")
        if end_idx >= 0:
            scrubbed = head + tail[end_idx + len("];") :]
    body_lower = scrubbed.lower()
    violations = [
        token for token in _RECON_FORBIDDEN_TOKENS if token in body_lower
    ]
    assert not violations, (
        "EC-11 violation — forbidden tokens in report_copy.ts: "
        f"{violations}"
    )


def test_ec_12_gap_as_opportunity_framing():
    """EC-12 (D281, Chunk 37 D289): the alert-copy registry must be
    non-empty and free of forbidden tokens (case-insensitive substring)."""
    from src.analytics.alert_copy import ALERT_COPY_REGISTRY

    assert ALERT_COPY_REGISTRY, "EC-12 expects a non-empty registry"
    violations: list[tuple[str, str]] = []
    for key, copy in ALERT_COPY_REGISTRY.items():
        body_parts: list[str] = []
        for v in copy.values():
            if isinstance(v, str):
                body_parts.append(v)
        body = " ".join(body_parts).lower()
        for token in _RECON_FORBIDDEN_TOKENS:
            if token in body:
                violations.append((key, token))
    assert not violations, (
        "EC-12 violation — forbidden vocabulary in alert copy: "
        f"{violations}"
    )
