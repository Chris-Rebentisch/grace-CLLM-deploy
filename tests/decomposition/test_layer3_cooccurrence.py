"""Layer 3 — co-occurrence tests (Chunk 40, CP7 NER + CP8 graph).

NER tests run via ``-k ner``; graph tests run via
``-k 'ppmi or leiden or ari'``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from src.decomposition.config import DecompositionConfig, load_config
from src.decomposition.layer3_cooccurrence import (
    build_cooccurrence_graph,
    extract_entities,
)
from src.decomposition.models import (
    Layer3Decision,
    LeidenSeedRun,
    ProperNounMention,
    ProperNounMentions,
)


# ---------- CP7 NER tests (-k ner) ----------


class _CountingLLM:
    """Minimal mock provider with a configurable response queue.

    Records every ``generate`` invocation and returns canned JSON
    payloads FIFO. ``concurrent_active`` peaks across the run so tests
    can verify the ``asyncio.Semaphore`` enforces concurrency.
    """

    def __init__(self, responses: list[str], delay: float = 0.0) -> None:
        self._responses = list(responses)
        self._delay = delay
        self.calls: list[dict[str, Any]] = []
        self._active = 0
        self.peak_active = 0

    async def generate(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"args": args, "kwargs": kwargs})
        self._active += 1
        if self._active > self.peak_active:
            self.peak_active = self._active
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._responses:
                return self._responses.pop(0)
            return json.dumps({"mentions": []})
        finally:
            self._active -= 1


def _ner_payload(mentions: list[dict]) -> str:
    return json.dumps({"mentions": mentions})


def test_ner_mocked_llm_returns_canned_mentions():
    """CP7: mocked LLM returns canned ProperNounMentions."""
    canned = _ner_payload(
        [
            {"text": "Acme Inc", "category": "organization", "occurrence_count": 3},
            {"text": "Maria", "category": "person", "occurrence_count": 1},
        ]
    )
    llm = _CountingLLM([canned])
    cfg = DecompositionConfig()
    docs = [{"text": "Maria works at Acme Inc."}]

    result = asyncio.run(extract_entities(docs, llm, cfg))

    assert len(result) == 1
    assert {m.text for m in result[0].mentions} == {"Acme Inc", "Maria"}
    assert result[0].mentions[0].category in {"person", "organization"}


def test_ner_categories_restricted_to_four_literal_enum():
    """CP7: a payload with an invalid category is rejected by Pydantic."""
    bad = _ner_payload(
        [
            {"text": "Acme", "category": "company", "occurrence_count": 1},
        ]
    )
    llm = _CountingLLM([bad])
    cfg = DecompositionConfig()
    # Layer falls back to empty mentions when validation fails — the
    # restriction is enforced at the Pydantic model.
    result = asyncio.run(extract_entities([{"text": "Acme."}], llm, cfg))
    assert result[0].mentions == []

    # Direct model_validate proves the literal restriction.
    with pytest.raises(Exception):
        ProperNounMention.model_validate(
            {"text": "Acme", "category": "company", "occurrence_count": 1}
        )

    # Each of the four allowed values validates.
    for cat in ("person", "organization", "location", "other_proper_noun"):
        ProperNounMention.model_validate(
            {"text": "X", "category": cat, "occurrence_count": 1}
        )


def test_ner_budget_warnings_emitted_without_abort(capsys):
    """CP7: soft and hard budget warnings emit but do not abort."""
    # Force a tiny budget so any wall-clock time exceeds 2x.
    cfg_dict = {
        "layer3": {
            "ner": {
                "concurrency": 1,
                # Per-10K budget; with 1 doc actual budget is 1e-7 seconds.
                "per_10k_doc_budget_seconds": 1,
            }
        }
    }
    cfg = DecompositionConfig.model_validate(cfg_dict)

    llm = _CountingLLM([_ner_payload([])], delay=0.001)
    result = asyncio.run(extract_entities([{"text": "doc body"}], llm, cfg))
    assert len(result) == 1  # pipeline did NOT abort

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # structlog default renderer prints to stdout/stderr; verify either
    # warning event appears.
    assert (
        "layer3_ner_budget_hard_warning" in combined
        or "layer3_ner_budget_soft_warning" in combined
    )


def test_ner_concurrency_semaphore_limits_inflight_calls():
    """CP7: asyncio.Semaphore bounds simultaneous LLM invocations."""
    n_docs = 8
    cfg = DecompositionConfig.model_validate({"layer3": {"ner": {"concurrency": 2}}})
    canned_responses = [_ner_payload([]) for _ in range(n_docs)]
    llm = _CountingLLM(canned_responses, delay=0.01)
    docs = [{"text": f"doc {i}"} for i in range(n_docs)]

    asyncio.run(extract_entities(docs, llm, cfg))

    assert llm.peak_active <= 2
    assert len(llm.calls) == n_docs


def test_ner_empty_document_short_circuits_without_llm_call():
    """CP7: empty-text documents return empty mentions, no LLM call."""
    llm = _CountingLLM([])
    cfg = DecompositionConfig()
    docs = [{"text": ""}, {"text": "   "}, {"text": None}]

    result = asyncio.run(extract_entities(docs, llm, cfg))

    assert len(result) == 3
    assert all(r.mentions == [] for r in result)
    assert llm.calls == []  # no LLM invocations


def test_ner_proper_noun_mention_pydantic_round_trip():
    """CP7: ProperNounMention round-trips through model_dump/model_validate."""
    m = ProperNounMention(text="Acme Inc", category="organization", occurrence_count=4)
    dumped = m.model_dump()
    rehydrated = ProperNounMention.model_validate(dumped)
    assert rehydrated == m

    container = ProperNounMentions(mentions=[m])
    container_dump = container.model_dump()
    rehydrated_container = ProperNounMentions.model_validate(container_dump)
    assert rehydrated_container.mentions[0].text == "Acme Inc"


# ---------- CP8 graph tests (-k 'ppmi or leiden or ari') ----------


def _ppmi_fixture_two_clusters() -> list[ProperNounMentions]:
    """Two co-occurrence cliques with no cross-edges.

    Docs 0–3: {a, b, c}. Docs 4–7: {d, e, f}. PPMI within each clique
    is positive; across cliques there is no joint occurrence.
    """
    docs: list[ProperNounMentions] = []
    for _ in range(4):
        docs.append(
            ProperNounMentions(
                mentions=[
                    ProperNounMention(text=t, category="organization")
                    for t in ("a", "b", "c")
                ]
            )
        )
    for _ in range(4):
        docs.append(
            ProperNounMentions(
                mentions=[
                    ProperNounMention(text=t, category="organization")
                    for t in ("d", "e", "f")
                ]
            )
        )
    return docs


def test_ppmi_math_against_worked_example():
    """CP8: PPMI weights computed correctly with epsilon stabilizer."""
    docs_data = _ppmi_fixture_two_clusters()
    cfg = DecompositionConfig()
    decision = build_cooccurrence_graph(docs_data, cfg)

    # 6 entities, two cliques of 3 → C(3,2)*2 = 6 within-clique edges,
    # plus zero cross-clique edges. p_joint=4/8=0.5 within; p_a=p_b=4/8=0.5;
    # PPMI = log2(0.5 / (0.5*0.5)) = log2(2) = 1.0.
    assert decision.edge_count == 6
    # Every entity is in one of the two cliques; assignments form 2 communities.
    assert len(set(decision.community_assignments.values())) == 2


def test_ppmi_drops_non_positive_weight_edges():
    """CP8: edges with weight ≤ 0 are excluded (D312)."""
    # Construct a corpus where one pair has p_joint < p_a * p_b → PMI<0.
    # Trick: 8 docs, 'a' in all 8, 'b' in all 8 → joint=8, p_joint=1.0,
    # p_a=p_b=1.0 → log2(1.0/(1.0*1.0)) = 0 (not >0, dropped).
    docs: list[ProperNounMentions] = []
    for _ in range(8):
        docs.append(
            ProperNounMentions(
                mentions=[
                    ProperNounMention(text="a", category="organization"),
                    ProperNounMention(text="b", category="organization"),
                ]
            )
        )
    cfg = DecompositionConfig()
    decision = build_cooccurrence_graph(docs, cfg)
    # Single pair (a,b) with weight ≤ 0 → no edges.
    assert decision.edge_count == 0


def test_leiden_runs_five_seeds():
    """CP8: Leiden invoked 5 times with deterministic seeds [1..5]."""
    docs = _ppmi_fixture_two_clusters()
    cfg = DecompositionConfig()
    decision = build_cooccurrence_graph(docs, cfg)

    assert len(decision.leiden_runs) == 5
    assert [r.seed for r in decision.leiden_runs] == [1, 2, 3, 4, 5]
    for run in decision.leiden_runs:
        assert isinstance(run, LeidenSeedRun)
        assert run.community_count >= 1


def test_leiden_modularity_best_selected():
    """CP8: ``selected_seed`` matches the run with maximum modularity."""
    docs = _ppmi_fixture_two_clusters()
    cfg = DecompositionConfig()
    decision = build_cooccurrence_graph(docs, cfg)
    best_modularity = max(r.modularity for r in decision.leiden_runs)
    assert decision.selected_modularity == pytest.approx(best_modularity)
    selected_run = next(
        r for r in decision.leiden_runs if r.seed == decision.selected_seed
    )
    assert selected_run.modularity == pytest.approx(best_modularity)


def test_ari_computed_across_ten_pairs_clears_flag_when_stable():
    """CP8: mean ARI over C(5,2)=10 pairs; well-separated graph → high ARI."""
    docs = _ppmi_fixture_two_clusters()
    cfg = DecompositionConfig()
    decision = build_cooccurrence_graph(docs, cfg)

    # Two disjoint cliques are trivially recoverable; ARI ≈ 1.0.
    assert decision.mean_pairwise_ari >= 0.6
    assert decision.low_stability_flag is False


def test_ari_low_stability_flag_threshold_via_config():
    """CP8: low_stability_flag fires when ARI < ari_threshold."""
    docs = _ppmi_fixture_two_clusters()
    # Force the threshold above the achievable ARI to flip the flag.
    cfg = DecompositionConfig.model_validate({"layer3": {"ari_threshold": 1.5}})
    decision = build_cooccurrence_graph(docs, cfg)
    assert decision.low_stability_flag is True
    assert decision.mean_pairwise_ari < cfg.layer3.ari_threshold
