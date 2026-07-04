"""Federation query router — fans out retrieval queries across namespaces (D384 CF3 relaxation).

Invariant carve-out: this file is the sole new addition under src/retrieval/
for Chunk 52. It is exempt from the CF3 retrieval-freeze lock per D384
(authorized by phase-6-entry-research.md). The router NEVER imports
pipeline.py — query capability is received via NamespaceQueryFn injection.
Authorization source: chunk-52-spec-v3-FINAL.md §6 Step 2.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.analytics import metrics as grace_metrics
from src.federation.models import FederationConfig
from src.federation.rules_engine import filter_properties_for_federation, resolve_namespace
from src.retrieval.fusion import reciprocal_rank_fusion
from src.retrieval.retrieval_models import (
    FusedCandidate,
    RankedResult,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResponse,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Types and models
# ---------------------------------------------------------------------------


@dataclass
class QueryRoutingConfig:
    """Configuration for federation query routing (loaded from config/federation.yaml query_routing section)."""

    per_namespace_timeout_seconds: float = 5.0
    # D498 (2026-05-28): default changed from "fail" (D408) to "degrade". On a
    # mother-namespace failure (timeout OR unreachable), "degrade" serves
    # whatever other namespaces returned (or an empty result) with a warning
    # instead of failing the whole query with 504. "fail" preserves the strict
    # D408 504 behavior for deployments that contractually require the mother.
    mother_timeout_posture: str = "degrade"
    # D498: per-namespace circuit-breaker cooldown. After a namespace fails, it
    # is skipped for this many seconds so an unreachable namespace does not make
    # every subsequent query re-pay the timeout. Set 0 to disable.
    circuit_breaker_cooldown_seconds: float = 60.0


@dataclass
class NamespaceTarget:
    """A single namespace that the router will fan out to."""

    name: str
    namespace_type: str  # "mother" or "child"
    label_prefix: str | None
    ontology_module: str | None
    prefixed_types: list[str] = field(default_factory=list)


# Type alias — the router receives its query capability via this callable,
# constructed in retrieval_routes.py (outside CF3). The router NEVER
# imports pipeline.py.
NamespaceQueryFn = Callable[[RetrievalQuery, NamespaceTarget], Awaitable[RetrievalResponse]]


# ---------------------------------------------------------------------------
# Per-namespace circuit breaker (D498, 2026-05-28)
#
# When a namespace query fails (timeout OR unreachable), the namespace is marked
# "open" (down) for a cooldown window. Subsequent queries SKIP an open namespace
# instead of re-attempting and re-paying the timeout — so a missing/unreachable
# mother (or child) does not degrade every future query. Single-process state
# (D287: one uvicorn worker per environment).
# ---------------------------------------------------------------------------
_namespace_circuit: dict[str, float] = {}  # namespace name -> cooldown-until monotonic ts


def _circuit_is_open(name: str) -> bool:
    """True when *name* is in its post-failure cooldown window."""
    until = _namespace_circuit.get(name)
    if until is None:
        return False
    if time.monotonic() >= until:
        _namespace_circuit.pop(name, None)  # cooldown elapsed — allow a probe.
        return False
    return True


def _circuit_trip(name: str, cooldown_seconds: float) -> None:
    """Mark *name* down for *cooldown_seconds* (no-op when cooldown <= 0)."""
    if cooldown_seconds > 0:
        _namespace_circuit[name] = time.monotonic() + cooldown_seconds


def _circuit_reset(name: str) -> None:
    """Clear *name*'s breaker after a successful query."""
    _namespace_circuit.pop(name, None)


def reset_federation_circuit() -> None:
    """Clear all breaker state (test/operator hook)."""
    _namespace_circuit.clear()


class NamespaceWarning(BaseModel):
    """Warning surfaced when a child namespace times out or errors."""

    namespace: str
    reason: str


class FederatedRetrievalResponse(BaseModel):
    """Router-local response model for federated queries.

    NOT a subclass of the frozen RetrievalResponse (CF3 intent).
    Duplicates the field set explicitly for backward compatibility.
    """

    query: str
    results: list[RankedResult] = Field(default_factory=list)
    serialized_context: str = ""
    serialization_format: str = "template"
    total_candidates: int = 0
    strategy_contributions: dict[str, int] = Field(default_factory=dict)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    retrieval_mode: str = "single_round"
    query_intents: list[str] = Field(default_factory=list)
    properties_omitted_count: int = 0
    multi_hop_proxy_score: float = 0.0
    latency_p95_by_mode_ms: dict[str, float] = Field(default_factory=dict)
    query_event_id: Any = None

    # Federation-specific fields
    source_namespaces: list[str] = Field(default_factory=list)
    namespace_warnings: list[NamespaceWarning] = Field(default_factory=list)
    result_source_namespaces: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Namespace resolution
# ---------------------------------------------------------------------------


def resolve_target_namespaces(
    db: Session,
    principal: Any,
) -> list[NamespaceTarget]:
    """Query graph_namespaces for all accessible federation namespaces.

    Mother namespace is always included. Child namespaces are intersected
    with the principal's permitted ontology modules (via effective_scope
    ScopeEntry rows with resource_kind="ontology_module").

    D406: broadcast-all strategy for v1.
    """
    from src.graph.namespace_database import GraphNamespaceRow
    from src.ontology.database import get_active_version
    from src.permissions.principal_context import effective_scope

    rows = (
        db.query(GraphNamespaceRow)
        .filter(GraphNamespaceRow.namespace_type.in_(["mother", "child"]))
        # F-49 readiness gate: a namespace participates in query routing only
        # when the operator has marked it ready. Not-ready children previously
        # entered the fan-out with no indexes -> global empty results.
        .filter(GraphNamespaceRow.is_ready.is_(True))
        .all()
    )

    if not rows:
        return []

    # Derive principal's allowed ontology modules from effective_scope.
    scope = effective_scope(principal)
    allowed_modules: set[str] | None = None
    module_entries = [
        e for e in scope.allows if e.resource_kind == "ontology_module"
    ]
    if module_entries:
        allowed_modules = {e.resource_label for e in module_entries}

    # Get active ontology version for type-set population.
    active_version = get_active_version(db)
    schema_modules: dict = {}
    if active_version is not None:
        schema_modules = active_version.schema_modules or {}

    targets: list[NamespaceTarget] = []
    for row in rows:
        ns_type = row.namespace_type or "child"
        ontology_module = row.ontology_module

        # Child namespaces are filtered by principal's allowed modules.
        if ns_type == "child" and allowed_modules is not None:
            if ontology_module and ontology_module not in allowed_modules:
                continue

        # Populate prefixed_types from schema_modules.
        prefixed_types: list[str] = []
        if ontology_module and ontology_module in schema_modules:
            module_content = schema_modules[ontology_module]
            entity_types = sorted(
                (module_content or {}).get("entity_types", {}).keys()
            )
            label_prefix = row.label_prefix
            if label_prefix and ns_type == "child":
                prefixed_types = [f"{label_prefix}_{t}" for t in entity_types]
            else:
                # Mother namespace types are unprefixed.
                prefixed_types = list(entity_types)
        elif ns_type == "mother":
            # Mother without a specific module: collect all types.
            for mod_name, mod_content in schema_modules.items():
                types = sorted(
                    (mod_content or {}).get("entity_types", {}).keys()
                )
                prefixed_types.extend(types)

        targets.append(
            NamespaceTarget(
                name=row.database_name,
                namespace_type=ns_type,
                label_prefix=row.label_prefix,
                ontology_module=ontology_module,
                prefixed_types=prefixed_types,
            )
        )

    return targets


# ---------------------------------------------------------------------------
# Internal adapters
# ---------------------------------------------------------------------------


def _ranked_to_candidate(
    result: RankedResult,
    namespace_name: str,
    position: int,
) -> RetrievalCandidate:
    """Map RankedResult -> RetrievalCandidate for RRF input."""
    return RetrievalCandidate(
        grace_id=result.grace_id,
        entity_type=result.entity_type,
        name=result.name,
        properties=result.properties,
        score=result.rrf_score,
        strategy=namespace_name,
        rank=position,
        hop_distance=result.hop_distance,
    )


def _fused_to_ranked(fused: FusedCandidate) -> RankedResult:
    """Map FusedCandidate -> RankedResult for backward-compatible response shape."""
    return RankedResult(
        grace_id=fused.grace_id,
        entity_type=fused.entity_type,
        name=fused.name,
        properties=fused.properties,
        rerank_score=fused.rrf_score,
        rrf_score=fused.rrf_score,
        contributing_strategies=fused.contributing_strategies,
    )


def _strip_label_prefix(
    type_name: str,
    registered_prefixes: list[str],
) -> str:
    """Strip namespace label prefix from a type name.

    Uses resolve_namespace() from src/federation/rules_engine.py:71
    for prefix discovery. If prefix found: strip it. If no prefix:
    return unchanged (mother-namespace types).
    """
    prefix = resolve_namespace(type_name, registered_prefixes)
    if prefix is not None:
        return type_name[len(prefix) + 1:]
    return type_name


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge_results(
    per_namespace_results: dict[str, RetrievalResponse],
    namespace_targets: list[NamespaceTarget],
    config: FederationConfig,
) -> FederatedRetrievalResponse:
    """Merge per-namespace retrieval results via RRF fusion.

    Steps (D407):
    1. Namespace post-filter: drop candidates whose entity_type doesn't
       carry the namespace's label_prefix.
    2. Strip label_prefix via _strip_label_prefix().
    3. Apply filter_properties_for_federation(layer="domain").
    4. Adapt RankedResult -> RetrievalCandidate via _ranked_to_candidate().
    5. Record source_namespace provenance per result.
    6. Call reciprocal_rank_fusion() with {namespace_name: candidates}.
    7. Adapt FusedCandidate -> RankedResult via _fused_to_ranked().
    8. Assemble FederatedRetrievalResponse with result_source_namespaces.
    """
    target_by_name = {t.name: t for t in namespace_targets}
    registered_prefixes = [
        t.label_prefix for t in namespace_targets
        if t.label_prefix is not None
    ]

    strategy_results: dict[str, list[RetrievalCandidate]] = {}
    # Track provenance per grace_id -> namespace for positional matching
    # after RRF fusion.
    grace_id_to_namespace: dict[str, str] = {}
    total_candidates = 0
    latency_ms: dict[str, float] = {}

    for ns_name, response in per_namespace_results.items():
        target = target_by_name.get(ns_name)
        if target is None:
            continue

        candidates: list[RetrievalCandidate] = []
        for pos, result in enumerate(response.results or []):
            # Step 1: namespace post-filter.
            if target.label_prefix and target.namespace_type == "child":
                if not result.entity_type.startswith(target.label_prefix + "_"):
                    continue
            elif target.namespace_type == "mother":
                # Mother types are unprefixed — drop anything with a
                # known child prefix.
                if resolve_namespace(result.entity_type, registered_prefixes) is not None:
                    continue

            # Step 2: strip label prefix.
            stripped_type = _strip_label_prefix(result.entity_type, registered_prefixes)

            # Step 3: filter properties for federation (layer="domain").
            filtered_props = filter_properties_for_federation(
                result.properties, layer="domain", config=config,
            )

            # Create a modified result for adaptation.
            modified = RankedResult(
                grace_id=result.grace_id,
                entity_type=stripped_type,
                name=result.name,
                properties=filtered_props,
                rerank_score=result.rerank_score,
                rrf_score=result.rrf_score,
                contributing_strategies=result.contributing_strategies,
                hop_distance=result.hop_distance,
            )

            # Step 4: adapt to RetrievalCandidate.
            candidate = _ranked_to_candidate(modified, ns_name, pos)
            candidates.append(candidate)

            # Step 5: record provenance.
            grace_id_to_namespace[result.grace_id] = ns_name

        if candidates:
            strategy_results[ns_name] = candidates
        total_candidates += len(candidates)

        # Accumulate latency.
        for k, v in (response.latency_ms or {}).items():
            latency_ms[f"{ns_name}.{k}"] = v

    # Step 6: RRF fusion.
    fused_candidates = reciprocal_rank_fusion(strategy_results)

    # Record merge metric.
    grace_metrics.record_federation_result_merge(merge_strategy="rrf")

    # Step 7: adapt FusedCandidate -> RankedResult.
    ranked_results = [_fused_to_ranked(fc) for fc in fused_candidates]

    # Step 8: assemble result_source_namespaces positionally matched.
    result_source_namespaces = [
        grace_id_to_namespace.get(r.grace_id, "unknown")
        for r in ranked_results
    ]

    # Compute strategy_contributions from fused results.
    contributions: dict[str, int] = {}
    for fc in fused_candidates:
        for s in fc.contributing_strategies:
            contributions[s] = contributions.get(s, 0) + 1

    return FederatedRetrievalResponse(
        query=next(iter(per_namespace_results.values())).query if per_namespace_results else "",
        results=ranked_results,
        total_candidates=total_candidates,
        strategy_contributions=contributions,
        latency_ms=latency_ms,
        source_namespaces=list(per_namespace_results.keys()),
        namespace_warnings=[],
        result_source_namespaces=result_source_namespaces,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def federated_query(
    query: RetrievalQuery,
    *,
    namespace_query_fn: NamespaceQueryFn,
    db: Session,
    principal: Any,
    federation_config: FederationConfig,
    routing_config: QueryRoutingConfig | None = None,
) -> FederatedRetrievalResponse:
    """Fan out a retrieval query across all accessible namespaces.

    D406: broadcast-all strategy.
    D407: naive cross-namespace RRF fusion.
    D408: per-namespace timeout with fail-soft children, fail-query mother.
    """
    if routing_config is None:
        routing_config = QueryRoutingConfig()

    targets = resolve_target_namespaces(db, principal)

    if not targets:
        logger.warning("federation.query.no_namespaces")
        return FederatedRetrievalResponse(query=query.query_text)

    # Record fan-out metric.
    grace_metrics.record_federation_queries(namespace_count=str(len(targets)))

    timeout = routing_config.per_namespace_timeout_seconds
    mother_posture = routing_config.mother_timeout_posture
    cooldown = routing_config.circuit_breaker_cooldown_seconds

    per_namespace_results: dict[str, RetrievalResponse] = {}
    warnings: list[NamespaceWarning] = []
    namespace_latencies: dict[str, float] = {}

    async def _query_namespace(target: NamespaceTarget) -> tuple[str, RetrievalResponse | None, str | None]:
        """Query a single namespace with timeout + circuit breaker (D498)."""
        # Circuit breaker: skip a namespace that recently failed so we don't
        # re-pay the timeout on every query while it is down.
        if _circuit_is_open(target.name):
            return target.name, None, "circuit-open: namespace in cooldown after a recent failure"
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                namespace_query_fn(query, target),
                timeout=timeout,
            )
            duration = time.monotonic() - start
            _circuit_reset(target.name)
            grace_metrics.record_federation_query_duration(
                namespace=target.name, outcome="success", duration=duration,
            )
            return target.name, result, None
        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            _circuit_trip(target.name, cooldown)
            grace_metrics.record_federation_query_duration(
                namespace=target.name, outcome="timeout", duration=duration,
            )
            return target.name, None, f"Timeout after {timeout}s"
        except (ConnectionError, OSError) as exc:
            # Connection not established / refused — fails fast (no timeout wait).
            # Distinguished from a slow-query timeout for operator clarity; both
            # trip the breaker so future queries skip this namespace.
            duration = time.monotonic() - start
            _circuit_trip(target.name, cooldown)
            grace_metrics.record_federation_query_duration(
                namespace=target.name, outcome="error", duration=duration,
            )
            return target.name, None, f"Unreachable: {exc}"
        except Exception as exc:
            duration = time.monotonic() - start
            _circuit_trip(target.name, cooldown)
            grace_metrics.record_federation_query_duration(
                namespace=target.name, outcome="error", duration=duration,
            )
            return target.name, None, str(exc)

    # Fan out concurrently.
    tasks = [_query_namespace(t) for t in targets]
    results = await asyncio.gather(*tasks)

    for ns_name, response, error in results:
        target = next((t for t in targets if t.name == ns_name), None)
        if response is not None:
            per_namespace_results[ns_name] = response
        elif error is not None:
            # D498: only the strict "fail" posture turns a mother failure into a
            # 504. Under the default "degrade" posture the mother is treated like
            # a child — warn and serve whatever other namespaces returned (or an
            # empty result), so an unreachable mother does not fail the query.
            if (
                target
                and target.namespace_type == "mother"
                and mother_posture == "fail"
            ):
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=504,
                    detail=f"Mother namespace '{ns_name}' failed: {error}",
                )
            # Child failure (or degraded mother): continue with a warning.
            warnings.append(NamespaceWarning(namespace=ns_name, reason=error))

    if not per_namespace_results:
        return FederatedRetrievalResponse(
            query=query.query_text,
            namespace_warnings=warnings,
        )

    # Merge results.
    merged = merge_results(per_namespace_results, targets, federation_config)
    merged.namespace_warnings = warnings

    return merged
