"""D292 — Change Directive state-machine matrix tests."""

from __future__ import annotations

import pytest

from src.change_directives.models import DirectiveStatus
from src.change_directives.state_machine import (
    STATE_TRANSITIONS,
    is_transition_allowed,
)


_ALLOWED_PAIRS = {
    (DirectiveStatus.DRAFT, DirectiveStatus.ACTIVE),
    (DirectiveStatus.DRAFT, DirectiveStatus.ABANDONED),
    (DirectiveStatus.ACTIVE, DirectiveStatus.REALIZED),
    (DirectiveStatus.ACTIVE, DirectiveStatus.ABANDONED),
    (DirectiveStatus.ACTIVE, DirectiveStatus.SUPERSEDED),
}


def test_allowed_pairs_match_registry_exactly() -> None:
    """Exactly five allowed transitions out of 25 ordered pairs."""
    actual = {
        (src, dst)
        for src, targets in STATE_TRANSITIONS.items()
        for dst in targets
    }
    assert actual == _ALLOWED_PAIRS
    assert len(actual) == 5


@pytest.mark.parametrize("from_state", list(DirectiveStatus))
@pytest.mark.parametrize("to_state", list(DirectiveStatus))
def test_full_25_pair_matrix(
    from_state: DirectiveStatus, to_state: DirectiveStatus
) -> None:
    """Full 25-pair grid: 5 allowed True, 20 disallowed False."""
    expected = (from_state, to_state) in _ALLOWED_PAIRS
    assert is_transition_allowed(from_state, to_state) is expected


def test_no_self_loops() -> None:
    for state in DirectiveStatus:
        assert not is_transition_allowed(state, state)


def test_terminal_states_have_no_outgoing() -> None:
    for terminal in (
        DirectiveStatus.REALIZED,
        DirectiveStatus.ABANDONED,
        DirectiveStatus.SUPERSEDED,
    ):
        assert STATE_TRANSITIONS[terminal] == frozenset()
        for target in DirectiveStatus:
            assert not is_transition_allowed(terminal, target)


def test_disallowed_count_is_twenty() -> None:
    disallowed = [
        (a, b)
        for a in DirectiveStatus
        for b in DirectiveStatus
        if not is_transition_allowed(a, b)
    ]
    assert len(disallowed) == 20
