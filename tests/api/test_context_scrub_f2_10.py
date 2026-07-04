"""F2-10 regression tests: serialized_context must not leak dropped vertices.

Validation-run evidence: a restricted principal's results correctly omitted the
privileged Calloway & Pruett vertex, but the LLM-facing serialized_context still
carried `Entity: Legal_Entity "Calloway & Pruett LLP" (...)` verbatim.
"""

from __future__ import annotations

from src.api.retrieval_routes import _scrub_serialized_context
from src.retrieval.retrieval_models import RankedResult


def _result(grace_id: str, name: str) -> RankedResult:
    return RankedResult(
        grace_id=grace_id,
        entity_type="Legal_Entity",
        name=name,
        rerank_score=0.9,
        rrf_score=0.5,
        contributing_strategies=["semantic"],
    )


_CONTEXT = (
    'Entity: Legal_Entity "Calloway & Pruett LLP" (entity_kind=law_firm)\n'
    'Entity: Legal_Entity "Sablewood Holdings LLC" (entity_kind=llc)\n'
    'Edge: Calloway & Pruett LLP -[retained_by]-> Sablewood Holdings LLC\n'
    'Entity: Person "Nathaniel Ferro"\n'
)


def test_dropped_vertex_name_scrubbed_from_context():
    dropped = [_result("c87402bd-0000-0000-0000-000000000000", "Calloway & Pruett LLP")]
    scrubbed = _scrub_serialized_context(_CONTEXT, dropped)
    assert "Calloway" not in scrubbed
    # Edge lines referencing the dropped entity go too.
    assert "retained_by" not in scrubbed
    # Unrelated lines survive.
    assert "Sablewood Holdings LLC" in scrubbed
    assert "Nathaniel Ferro" in scrubbed


def test_grace_id_mentions_scrubbed():
    ctx = _CONTEXT + "Provenance: c87402bd-0000-0000-0000-000000000000 via doc-1\n"
    dropped = [_result("c87402bd-0000-0000-0000-000000000000", "Calloway & Pruett LLP")]
    scrubbed = _scrub_serialized_context(ctx, dropped)
    assert "c87402bd" not in scrubbed


def test_no_drops_context_unchanged():
    assert _scrub_serialized_context(_CONTEXT, []) == _CONTEXT


def test_short_names_do_not_over_scrub():
    """A degenerate 1-2 char name must not wipe unrelated lines."""
    dropped = [_result("11111111-0000-0000-0000-000000000000", "A")]
    scrubbed = _scrub_serialized_context(_CONTEXT, dropped)
    # Name too short to match; grace_id not present — context unchanged.
    assert scrubbed == _CONTEXT


def test_both_post_filters_wire_the_scrub():
    import inspect

    import src.api.retrieval_routes as rr

    std = inspect.getsource(rr._apply_permission_post_filter)
    fed = inspect.getsource(rr._apply_permission_post_filter_federated)
    for src in (std, fed):
        assert "_scrub_serialized_context" in src
        assert "dropped.append(result)" in src
