"""Phase progress indicator (Chunk 34, EC-1 / D262).

A pure, deterministic function of ``(phase_state, completed_steps)``
that maps an Elicitation Protocol phase + completed-step count into a
unit-interval progress indicator. Production callers (UI surfaces and
telemetry) MUST derive their progress widths from this function so the
EC-1 determinism property is enforceable end-to-end.

D120 / D217 forbid surfacing raw scores in the UI; this function returns
a float in ``[0, 1]`` intended for width-style rendering only.
"""

from __future__ import annotations

from typing import Final, Mapping


PHASE_BASE_WEIGHTS: Final[Mapping[str, float]] = {
    "open": 0.10,
    "structure": 0.40,
    "clarify": 0.70,
    "close": 1.00,
}

_STEP_INCREMENT: Final[float] = 0.005


def compute_progress_indicator(phase_state: str, completed_steps: int) -> float:
    """Return a deterministic progress indicator in ``[0, 1]``.

    Args:
        phase_state: One of ``open|structure|clarify|close``. Unknown
            values yield a base weight of ``0.0`` (the function never
            raises on enum drift; the caller decides whether to gate).
        completed_steps: Non-negative count of completed laddering /
            card-sort / teach-back steps within the current phase.
            Negative values are clamped to 0.

    Returns:
        A float in ``[0.0, 1.0]``. Same inputs always return the same
        output (EC-1 invariant).
    """
    base = PHASE_BASE_WEIGHTS.get(phase_state, 0.0)
    steps = max(0, int(completed_steps))
    return min(1.0, base + _STEP_INCREMENT * steps)
