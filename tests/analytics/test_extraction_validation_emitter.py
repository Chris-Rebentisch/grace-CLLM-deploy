"""Cardinality-guard test for ``grace_extraction_validation_failures`` (D162/D242).

Synthesizes 100 distinct ``entity_type`` values for a single
``ontology_module`` and asserts the Top-N + ``_other_`` bucket fires.
"""

from __future__ import annotations

from src.analytics.extraction_validation_emitter import (
    TOP_N,
    _bucket_entity_type,
    reset_for_tests,
)


def test_cardinality_guard_buckets_tail_into_other():
    reset_for_tests()
    module = "finance"

    # Push a clear head: each "headN" gets many hits so it dominates.
    for idx in range(TOP_N):
        for _ in range(10):
            _bucket_entity_type(module, f"head{idx}")

    # Now push 100 distinct tail values (one hit each). They should
    # bucket into ``_other_`` once they fall outside the head set.
    bucketed: list[str] = []
    for idx in range(100):
        bucketed.append(_bucket_entity_type(module, f"tail{idx}"))

    # The first emit of each tail value adds it to the counter; until it
    # exceeds TOP_N entries, the value may still be returned verbatim.
    # After the 100 tail emits, the counter has TOP_N + 100 distinct
    # entries — strictly more than TOP_N, so subsequent emits of any
    # non-head value must bucket into ``_other_``.
    again = _bucket_entity_type(module, "tail999")
    assert again == "_other_"

    # Head values still return verbatim (their counts dominate).
    head_again = _bucket_entity_type(module, "head0")
    assert head_again == "head0"
