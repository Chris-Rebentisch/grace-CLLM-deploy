"""Layer 6 sample-CQ generation adapter (Chunk 41, D324).

Calls Discovery's :func:`src.discovery.cq_generator.run_single_pass`
with ``pass_name='top_down'`` to produce a small bundle of sample CQs
per :class:`ProposedSegment`.

Critical contract (D324):

* Sample CQs are **transient** — held in
  ``decomposition_runs.layer6_validation`` JSONB only.
* **Never persisted to the ``competency_questions`` table.** This
  module performs no database writes against ``competency_questions``;
  the only persistence call sites are in ``cq_generator``'s
  ``run_generation_pipeline``, which we do not call.
* Discovery's ``cq_generator.py`` is **imported, not modified.** A
  pinned regression test on the call shape (``test_layer6_sample_cq``)
  fails fast against future Discovery refactors.

The adapter constructs a synthetic :class:`DomainBatch` from a
:class:`ProposedSegment` plus a representative document text snippet,
then translates ``GeneratedCQ`` outputs into
:class:`GeneratedCQSnapshot` (the schema persisted in
``layer6_validation``).
"""

from __future__ import annotations

from typing import Any

from src.decomposition.config import load_config
from src.decomposition.models import ProposedSegment
from src.decomposition.segmentation_map_models import GeneratedCQSnapshot
from src.discovery.cq_generator import PassResult, run_single_pass
from src.discovery.domain_batcher import DomainBatch


def _build_domain_batch_for_segment(
    segment: ProposedSegment,
    document_excerpts: list[str],
) -> DomainBatch:
    """Assemble a :class:`DomainBatch` from a Layer 4 ProposedSegment.

    The DomainBatch is synthetic: the ``domain`` is the segment name,
    the ``context_digest`` is the segment description, and ``key_terms``
    + ``sample_excerpts`` come from the segment's representative entities
    + the supplied document text. ``document_ids`` is empty because no
    real documents are tied to this transient generation.
    """
    key_terms: list[str] = []
    if segment.representative_keywords:
        key_terms.extend(segment.representative_keywords)
    if segment.representative_entities:
        key_terms.extend(segment.representative_entities)

    return DomainBatch(
        domain=segment.name,
        document_ids=[],
        document_count=len(document_excerpts),
        total_words=sum(len(s.split()) for s in document_excerpts),
        context_digest=segment.description,
        key_terms=key_terms or [segment.name],
        sample_excerpts=document_excerpts[:5],
    )


async def generate_sample_cqs(
    segment: ProposedSegment,
    document_excerpts: list[str],
    *,
    n: int | None = None,
    provider: Any = None,
) -> list[GeneratedCQSnapshot]:
    """Generate up to ``n`` sample CQs for ``segment`` (D324).

    ``n`` defaults to ``layer6.sample_cqs.n`` from
    ``config/decomposition.yaml`` (default 5). The underlying
    ``run_single_pass`` call decides how many CQs to emit; we trim to
    ``n`` here. Returns transient :class:`GeneratedCQSnapshot` objects;
    **no database writes**.
    """
    if n is None:
        cfg = load_config()
        n = cfg.layer6.sample_cqs.n

    batch = _build_domain_batch_for_segment(segment, document_excerpts)
    document_text = "\n\n".join(document_excerpts)

    result: PassResult = await run_single_pass(
        pass_name="top_down",
        batch=batch,
        document_text=document_text,
        provider=provider,
    )

    if not result.success:
        return []

    snapshots: list[GeneratedCQSnapshot] = []
    for cq in result.cqs[:n]:
        snapshots.append(
            GeneratedCQSnapshot(
                text=cq.question,
                cq_type=cq.cq_type or "UNCLASSIFIED",
                provenance={
                    "segment_name": segment.name,
                    "pass_name": "top_down",
                    "model": result.model,
                    "rationale": cq.rationale,
                    "priority": cq.priority,
                },
            )
        )
    return snapshots
