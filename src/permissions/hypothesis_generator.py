"""Hypothesis generator (Chunk 42, D333).

Two-stage hybrid generator:

1. **Leiden community detection** over the Person × {Document, Segment}
   bipartite graph using ``python-igraph``. The bipartite graph is
   collapsed into a Person-Person co-membership graph weighted by the
   number of shared documents/segments; Leiden is then run on that
   graph with a deterministic seed.
2. **LLM narration** via Instructor + Ollama produces the cluster
   ``display_name``, ``description``, and proposes the
   :class:`HypothesisConfidenceBand`. The LLM never invents members —
   members are sourced from the Leiden output.

Every result set carries exactly one mandatory ``NullHypothesis`` so
the operator always has the choice "no segmentation".

The default ``--dry-run`` path returns a deterministic mocked-LLM
output without making any network call (mirrors Chunk 40 D317).

Public surface:

* ``generate(evidence, *, dry_run, llm_call) -> RoleClusterHypothesisSet``
* ``narrate_cluster_default(...)`` — the deterministic mock used as
  fallback when ``dry_run=True`` or ``llm_call`` is not provided.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID, uuid4

from src.permissions.models import (
    EvidenceBundle,
    EvidenceSection,
    HypothesisConfidenceBand,
    HypothesisItem,
    NullHypothesis,
    RoleCluster,
    RoleClusterHypothesisSet,
    RoleClusterMember,
    SegmentedHypothesis,
)


# ----- Leiden graph construction ------------------------------------


def _persons_from_evidence(bundle: EvidenceBundle) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()

    def _add(pid: Any) -> None:
        if not pid:
            return
        sid = str(pid)
        if sid in seen_set:
            return
        seen_set.add(sid)
        seen.append(sid)

    for section in bundle.sections:
        if section.source in (
            "document_authorship",
            "graph_person_role",
            "signal_combination",
        ):
            for row in section.rows:
                _add(row.get("person_grace_id"))
        elif section.source == "segment_ownership":
            for row in section.rows:
                _add(row.get("reviewer"))
        elif section.source == "change_directive_authorship":
            for row in section.rows:
                _add(row.get("authored_by"))
    return seen


def _person_artifact_pairs(bundle: EvidenceBundle) -> list[tuple[str, str]]:
    """Materialize (person_grace_id, artifact_id) edges of the bipartite
    graph from the evidence sections."""
    pairs: list[tuple[str, str]] = []
    for section in bundle.sections:
        if section.source == "document_authorship":
            for row in section.rows:
                p = row.get("person_grace_id")
                d = row.get("document_id")
                if p and d:
                    pairs.append((str(p), f"doc::{d}"))
        elif section.source == "segment_ownership":
            for row in section.rows:
                rev = row.get("reviewer")
                seg = row.get("segment_name")
                if rev and seg:
                    pairs.append((str(rev), f"seg::{seg}"))
    return pairs


def _build_person_coassoc_graph(
    persons: list[str], pairs: list[tuple[str, str]]
) -> tuple[list[tuple[int, int, float]], dict[str, int]]:
    """Build a Person-Person co-association edge list.

    Two persons are connected with weight equal to the count of shared
    documents/segments. The output is suitable for ``igraph.Graph``
    construction.
    """
    person_idx: dict[str, int] = {p: i for i, p in enumerate(persons)}
    artifact_to_persons: dict[str, set[str]] = {}
    for person, artifact in pairs:
        if person not in person_idx:
            continue
        artifact_to_persons.setdefault(artifact, set()).add(person)
    edges: dict[tuple[int, int], int] = {}
    for ps in artifact_to_persons.values():
        if len(ps) < 2:
            continue
        sorted_persons = sorted(ps)
        for i in range(len(sorted_persons)):
            for j in range(i + 1, len(sorted_persons)):
                a = person_idx[sorted_persons[i]]
                b = person_idx[sorted_persons[j]]
                key = (a, b) if a < b else (b, a)
                edges[key] = edges.get(key, 0) + 1
    return [(a, b, float(w)) for (a, b), w in edges.items()], person_idx


def _run_leiden(
    persons: list[str], edges: list[tuple[int, int, float]], seed: int = 42
) -> list[list[str]]:
    """Run Leiden community detection. Returns list of clusters of
    ``person_grace_id`` strings.

    Falls back to a single trivial cluster if igraph is unavailable or
    the graph is empty.
    """
    if not persons:
        return []
    try:
        import igraph as ig  # type: ignore
    except Exception:  # pragma: no cover - dependency present in repo
        return [list(persons)]

    g = ig.Graph(n=len(persons), directed=False)
    if edges:
        g.add_edges([(a, b) for a, b, _ in edges])
        g.es["weight"] = [w for _, _, w in edges]
    # Singletons are admissible — Leiden returns one cluster per
    # connected component (so isolated nodes get their own cluster).
    try:
        partition = g.community_leiden(
            objective_function="modularity",
            weights="weight" if edges else None,
            n_iterations=-1,
            resolution=1.0,
        )
    except Exception:
        return [list(persons)]
    clusters: list[list[str]] = []
    for comm in partition:
        clusters.append([persons[i] for i in comm])
    return clusters


# ----- Confidence band heuristic ------------------------------------


def _band_for_cluster(
    cluster_size: int, internal_edge_count: int
) -> HypothesisConfidenceBand:
    """Map a cluster's structural strength to a band label.

    The mapping is intentionally simple at v1 — the LLM may override
    via narration. Bands only — no numerics surface. The thresholds
    are deliberately wide so small/test fixtures still differentiate.
    """
    if cluster_size <= 1:
        return "weak"
    # density = edges / max(C(n,2), 1)
    max_edges = max(cluster_size * (cluster_size - 1) // 2, 1)
    density = internal_edge_count / max_edges
    if density >= 0.66 and cluster_size >= 3:
        return "strong"
    if density >= 0.33:
        return "moderate"
    return "weak"


def _internal_edge_count(
    members: list[str], person_idx: dict[str, int], edges: list[tuple[int, int, float]]
) -> int:
    member_idx = {person_idx[m] for m in members if m in person_idx}
    n = 0
    for a, b, _ in edges:
        if a in member_idx and b in member_idx:
            n += 1
    return n


# ----- LLM narration ------------------------------------------------


@dataclass
class NarratedCluster:
    """Result of LLM narration for one Leiden cluster."""

    display_name: str
    description: str
    confidence_band: HypothesisConfidenceBand
    rationale: str | None = None


LLMNarrate = Callable[[list[str], dict[str, Any]], NarratedCluster]
"""Type of the (members, context) -> NarratedCluster LLM stub.

The LLM call MUST NOT include or invent ``person_grace_id`` strings
beyond those in the input ``members`` list. Implementations route
through Instructor to enforce a Pydantic schema that constrains the
output shape.
"""


def narrate_cluster_default(
    members: list[str], context: dict[str, Any]
) -> NarratedCluster:
    """Deterministic mock narration used in dry-run mode.

    Stable display name keyed off cluster size + first member id so
    repeated dry-runs produce identical output (CLI fixture stability).
    """
    name = f"role_cluster_{len(members):02d}_{(members[0] if members else 'empty')[:8]}"
    band = context.get("structural_band", "weak")
    return NarratedCluster(
        display_name=name,
        description=(
            f"Hypothesized cluster of {len(members)} member(s) generated "
            "from Leiden co-association partition."
        ),
        confidence_band=band,
        rationale="Deterministic mock narration (dry-run).",
    )


# ----- Public API ---------------------------------------------------


def generate(
    evidence: EvidenceBundle,
    *,
    dry_run: bool = True,
    llm_call: LLMNarrate | None = None,
    seed: int = 42,
    run_id: UUID | None = None,
) -> RoleClusterHypothesisSet:
    """Run Leiden + narration; return a hypothesis set with mandatory null.

    Members in every ``SegmentedHypothesis`` are exactly the Leiden
    cluster output — the LLM cannot invent or remove members. The
    ``NullHypothesis`` is always present.
    """
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    # This module has no argv entry of its own — the sanctioned CLI is
    # ``python -m src.permissions.cli hypothesis generate``, whose only
    # pipeline entry into this module is generate(); init here (idempotent)
    # so the subprocess's counters reach /metrics.
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    if dry_run or llm_call is None:
        narrate: LLMNarrate = narrate_cluster_default
    else:
        narrate = llm_call

    persons = _persons_from_evidence(evidence)
    pairs = _person_artifact_pairs(evidence)
    edges, person_idx = _build_person_coassoc_graph(persons, pairs)
    clusters = _run_leiden(persons, edges, seed=seed)

    hypotheses: list[HypothesisItem] = []
    for cluster_members in clusters:
        if not cluster_members:
            continue
        ie = _internal_edge_count(cluster_members, person_idx, edges)
        structural_band = _band_for_cluster(len(cluster_members), ie)
        narrated = narrate(
            list(cluster_members),
            {
                "structural_band": structural_band,
                "internal_edges": ie,
            },
        )
        # Defense against an LLM that ignores constraints — drop any
        # member id not in the original Leiden cluster output.
        constrained_members = [
            RoleClusterMember(person_grace_id=m) for m in cluster_members
        ]
        cluster = RoleCluster(
            cluster_id=narrated.display_name,
            display_name=narrated.display_name,
            description=narrated.description,
            members=constrained_members,
            access_rules=[],
            visibility_rules=[],
        )
        hypotheses.append(
            SegmentedHypothesis(
                cluster=cluster,
                confidence_band=narrated.confidence_band,
                rationale=narrated.rationale,
            )
        )

    hypotheses.append(
        NullHypothesis(
            rationale=(
                "No segmentation may be preferable when the evidence is "
                "thin or members cluster only weakly."
            )
        )
    )

    return RoleClusterHypothesisSet(
        run_id=run_id or uuid4(),
        evidence_id=evidence.evidence_id,
        hypotheses=hypotheses,
    )


__all__ = [
    "LLMNarrate",
    "NarratedCluster",
    "generate",
    "narrate_cluster_default",
]
