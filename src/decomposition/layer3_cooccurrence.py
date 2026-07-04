"""Layer 3 — entity co-occurrence (D311 + D312 + D313).

Two halves:

1. NER (CP7): per-document proper-noun extraction via
   ``LLMProvider.generate()`` with Instructor structured output
   (``ProperNounMentions`` Pydantic model). Concurrency capped at
   ``config.layer3.ner.concurrency`` via ``asyncio.Semaphore``.
   Per-10K-document wall-clock budget tracked in seconds; soft
   warning at 1.5× budget, hard warning at 2.0×. **Pipeline does not
   abort on budget overrun** — log warnings only (D311/R9).

2. Graph (CP8): document-level PPMI edge weights, python-igraph
   Leiden ×5 with deterministic seeds, mean pairwise ARI stability,
   ``low_stability_flag`` when ARI < ``config.layer3.ari_threshold``
   (default 0.6, D313).
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

import structlog

from src.decomposition.config import DecompositionConfig
from src.decomposition.models import (
    Layer3Decision,
    LeidenSeedRun,
    ProperNounMention,
    ProperNounMentions,
)


log = structlog.get_logger()

_PROMPT_FILE = Path(__file__).parent / "prompts" / "layer3_ner.txt"


class LLMLike(Protocol):
    """Subset of ``LLMProvider`` required by Layer 3 NER.

    Implementations must expose ``generate(system_prompt, user_prompt,
    ...)`` returning either an ``LLMResponse`` (with ``.text``) or any
    object whose string form is parseable as JSON. Tests use mocks
    that satisfy this protocol.
    """

    async def generate(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        ...


def _read_prompt() -> tuple[str, str]:
    raw = _PROMPT_FILE.read_text(encoding="utf-8")
    parts = raw.split("==USER==", 1)
    system = parts[0].replace("==SYSTEM==", "").strip()
    user = parts[1].strip() if len(parts) == 2 else ""
    return system, user


def _normalize_response(resp: Any) -> str:
    if hasattr(resp, "text"):
        return resp.text
    if isinstance(resp, str):
        return resp
    return json.dumps(resp)


def _parse_proper_nouns(text: str) -> ProperNounMentions:
    """Best-effort JSON → ``ProperNounMentions`` parse.

    Tolerates surrounding markdown fences. On parse failure returns
    an empty mentions list rather than aborting the layer.
    """
    payload = text.strip()
    # Strip ```json fences if present.
    if payload.startswith("```"):
        payload = payload.strip("`")
        if payload.lower().startswith("json"):
            payload = payload[4:]
        payload = payload.strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ProperNounMentions(mentions=[])
    if isinstance(data, list):
        data = {"mentions": data}
    try:
        return ProperNounMentions.model_validate(data)
    except Exception:  # noqa: BLE001
        return ProperNounMentions(mentions=[])


async def extract_entities(
    documents: list[dict],
    llm_provider: LLMLike,
    config: DecompositionConfig,
) -> list[ProperNounMentions]:
    """Run NER over ``documents`` and return aligned ``ProperNounMentions``.

    Each item in ``documents`` is a dict with at minimum ``"text"``.
    Empty-text documents short-circuit to an empty mentions list and
    do not call the LLM. Concurrency is bounded at
    ``config.layer3.ner.concurrency`` (default 4).
    """
    system_prompt, user_template = _read_prompt()
    semaphore = asyncio.Semaphore(config.layer3.ner.concurrency)
    n_docs = len(documents)

    # Per-10K-document budget: scale linearly to actual N.
    budget_seconds = config.layer3.ner.per_10k_doc_budget_seconds * (
        n_docs / 10_000
    )
    started_at = time.monotonic()

    async def one(doc: dict) -> ProperNounMentions:
        text = (doc.get("text") or "").strip()
        if not text:
            return ProperNounMentions(mentions=[])
        async with semaphore:
            user_prompt = user_template.replace("{document_text}", text)
            try:
                resp = await llm_provider.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            except TypeError:
                # Protocol-compatible mocks may take a single prompt.
                resp = await llm_provider.generate(user_prompt)
            return _parse_proper_nouns(_normalize_response(resp))

    results: list[ProperNounMentions] = []
    for batch_start in range(0, n_docs, max(1, config.layer3.ner.concurrency * 2)):
        batch = documents[
            batch_start : batch_start + max(1, config.layer3.ner.concurrency * 2)
        ]
        results.extend(await asyncio.gather(*[one(d) for d in batch]))

    elapsed = time.monotonic() - started_at
    if budget_seconds > 0 and elapsed >= 2.0 * budget_seconds:
        log.warning(
            "layer3_ner_budget_hard_warning",
            elapsed=elapsed,
            budget=budget_seconds,
            documents=n_docs,
        )
    elif budget_seconds > 0 and elapsed >= 1.5 * budget_seconds:
        log.warning(
            "layer3_ner_budget_soft_warning",
            elapsed=elapsed,
            budget=budget_seconds,
            documents=n_docs,
        )

    return results


# ---------- CP8 Graph half ----------


_EPSILON = 1e-9


def _document_entity_sets(
    entity_lists: list[ProperNounMentions],
) -> list[set[str]]:
    """For each document, return the unique-entity set (case-folded text)."""
    out: list[set[str]] = []
    for doc in entity_lists:
        out.append({m.text.strip().lower() for m in doc.mentions if m.text.strip()})
    return out


def _ppmi_edges(
    docs: list[set[str]],
) -> tuple[list[str], list[tuple[int, int, float]]]:
    """Compute document-level PPMI edges.

    weight(e1, e2) = max(0, log2((p_joint + ε) / ((p_e1 + ε) · (p_e2 + ε))))
    Edges with weight ≤ 0 are dropped (D312).
    """
    n = max(len(docs), 1)
    counts: dict[str, int] = {}
    for d in docs:
        for e in d:
            counts[e] = counts.get(e, 0) + 1
    entities = sorted(counts.keys())
    index: dict[str, int] = {e: i for i, e in enumerate(entities)}

    pair_counts: dict[tuple[int, int], int] = {}
    for d in docs:
        ents = sorted(index[e] for e in d if e in index)
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                pair_counts[(ents[i], ents[j])] = (
                    pair_counts.get((ents[i], ents[j]), 0) + 1
                )

    edges: list[tuple[int, int, float]] = []
    for (a, b), joint in pair_counts.items():
        p_joint = joint / n
        p_a = counts[entities[a]] / n
        p_b = counts[entities[b]] / n
        weight = math.log2(
            (p_joint + _EPSILON) / ((p_a + _EPSILON) * (p_b + _EPSILON))
        )
        if weight > 0:
            edges.append((a, b, weight))
    return entities, edges


def build_cooccurrence_graph(
    entity_lists: list[ProperNounMentions],
    config: DecompositionConfig,
) -> Layer3Decision:
    """Build PPMI co-occurrence graph and run Leiden ×5 stability protocol."""
    import igraph as ig
    from sklearn.metrics import adjusted_rand_score

    docs = _document_entity_sets(entity_lists)
    entities, edges = _ppmi_edges(docs)

    edge_count = len(edges)
    n_entities = len(entities)

    # If the graph is empty / trivial, short-circuit with stable assignment.
    if n_entities == 0 or edge_count == 0:
        return Layer3Decision(
            document_count=len(docs),
            edge_count=edge_count,
            leiden_runs=[
                LeidenSeedRun(seed=s, modularity=0.0, community_count=0)
                for s in config.layer3.leiden.seeds
            ],
            selected_seed=config.layer3.leiden.seeds[0],
            selected_modularity=0.0,
            mean_pairwise_ari=1.0,
            low_stability_flag=False,
            community_assignments={e: 0 for e in entities},
        )

    g = ig.Graph(n=n_entities, edges=[(a, b) for (a, b, _) in edges])
    g.es["weight"] = [w for (_, _, w) in edges]

    leiden_seeds = config.layer3.leiden.seeds
    seed_runs: list[LeidenSeedRun] = []
    seed_partitions: list[list[int]] = []

    import random

    for seed in leiden_seeds:
        random.seed(seed)
        try:
            ig.set_random_number_generator(random.Random(seed))
        except Exception:  # noqa: BLE001 — older igraph
            pass
        partition = g.community_leiden(
            objective_function="modularity",
            resolution=config.layer3.leiden.resolution,
            beta=config.layer3.leiden.beta,
            n_iterations=config.layer3.leiden.n_iterations,
            weights="weight",
        )
        membership = list(partition.membership)
        modularity = float(g.modularity(membership, weights="weight"))
        seed_runs.append(
            LeidenSeedRun(
                seed=seed,
                modularity=modularity,
                community_count=len(set(membership)),
            )
        )
        seed_partitions.append(membership)

    # Best partition by modularity score.
    best_idx = max(range(len(seed_runs)), key=lambda i: seed_runs[i].modularity)
    selected = seed_runs[best_idx]
    selected_partition = seed_partitions[best_idx]

    # Mean pairwise ARI across all C(5,2)=10 pairs.
    aris: list[float] = []
    for i in range(len(seed_partitions)):
        for j in range(i + 1, len(seed_partitions)):
            aris.append(
                float(
                    adjusted_rand_score(seed_partitions[i], seed_partitions[j])
                )
            )
    mean_ari = sum(aris) / len(aris) if aris else 1.0

    low_stability = mean_ari < config.layer3.ari_threshold

    assignments = {entities[i]: int(selected_partition[i]) for i in range(n_entities)}

    return Layer3Decision(
        document_count=len(docs),
        edge_count=edge_count,
        leiden_runs=seed_runs,
        selected_seed=selected.seed,
        selected_modularity=selected.modularity,
        mean_pairwise_ari=mean_ari,
        low_stability_flag=low_stability,
        community_assignments=assignments,
    )
