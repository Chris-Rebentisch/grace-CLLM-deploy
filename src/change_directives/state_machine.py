"""Change Directive state-machine registry (D292).

Plain Python module-level constant — no third-party state-machine
library. Five permitted transitions out of 25 ordered ``(from, to)``
pairs; terminal states (``REALIZED``, ``ABANDONED``, ``SUPERSEDED``)
carry an empty frozenset.

The two writers of ``status`` are ``repository.create()`` (sets
``DRAFT`` on INSERT) and ``repository.transition()`` (post-INSERT writer).
``repository.patch_draft_metadata()`` never writes ``status``.
"""

from __future__ import annotations

from .models import DirectiveStatus

STATE_TRANSITIONS: dict[DirectiveStatus, frozenset[DirectiveStatus]] = {
    DirectiveStatus.DRAFT: frozenset(
        {DirectiveStatus.ACTIVE, DirectiveStatus.ABANDONED}
    ),
    DirectiveStatus.ACTIVE: frozenset(
        {
            DirectiveStatus.REALIZED,
            DirectiveStatus.ABANDONED,
            DirectiveStatus.SUPERSEDED,
        }
    ),
    DirectiveStatus.REALIZED: frozenset(),
    DirectiveStatus.ABANDONED: frozenset(),
    DirectiveStatus.SUPERSEDED: frozenset(),
}


def is_transition_allowed(
    from_state: DirectiveStatus, to_state: DirectiveStatus
) -> bool:
    """Return True iff ``from_state -> to_state`` is one of the five
    permitted transitions (D292)."""
    return to_state in STATE_TRANSITIONS[from_state]
