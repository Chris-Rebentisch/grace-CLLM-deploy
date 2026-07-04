"""API endpoints for the retrieval pipeline."""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, Request

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.config import ArcadeConfig
from src.graph.cypher_utils import escape_cypher_string
from src.permissions import repository as _matrix_repo
from src.permissions.enforcer import get_enforcer
from src.permissions.models import Allow, PermissionMatrix
from src.permissions.principal_context import from_admission_tree
from src.permissions.sensitivity_resolver import (
    resolve_enforcement_posture,
    resolve_forbidden_tags,
)
from src.retrieval.bm25_strategy import BM25SearchIndex
from src.retrieval.pipeline import RetrievalPipeline
from src.retrieval.query_event_writer import persist_query_response
from src.retrieval.reranker import CrossEncoderReranker
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import RetrievalQuery, RetrievalResponse
from src.retrieval.semantic_strategy import SemanticSearchIndex
from src.shared.database import get_session_factory

logger = structlog.get_logger()

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])

# Module-level singleton — initialized on first use
_pipeline: RetrievalPipeline | None = None

# Chunk 52 (D384) — federation-active boolean.
# F-49 rework (validation run): the original cache was set once and never
# invalidated, so ONE child-namespace registration permanently rerouted ALL
# retrieval through the federated path — even after the namespace was deleted
# (recovery required a process restart). Two changes:
#   1. only READY child namespaces activate federation (is_ready gate,
#      migration f49a_ns_readiness);
#   2. the cache carries a short TTL and an explicit invalidation hook called
#      by the federation register/patch/delete routes.
_federation_active: bool | None = None
_federation_active_checked_at: float = 0.0
_FEDERATION_ACTIVE_TTL_SECONDS = 60.0


def invalidate_federation_cache() -> None:
    """Reset the federation-activation cache (F-49).

    Called by federation namespace register/patch/delete routes so routing
    reflects namespace state without a process restart.
    """
    global _federation_active
    _federation_active = None


def _is_federation_active() -> bool:
    """Return True when at least one READY child namespace is registered."""
    global _federation_active, _federation_active_checked_at
    import time as _time

    now = _time.monotonic()
    if (
        _federation_active is not None
        and (now - _federation_active_checked_at) < _FEDERATION_ACTIVE_TTL_SECONDS
    ):
        return _federation_active
    try:
        from src.graph.namespace_database import GraphNamespaceRow

        session_factory = get_session_factory()
        session = session_factory()
        try:
            count = (
                session.query(GraphNamespaceRow)
                .filter(GraphNamespaceRow.namespace_type == "child")
                .filter(GraphNamespaceRow.is_ready.is_(True))
                .count()
            )
            _federation_active = count > 0
            _federation_active_checked_at = now
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 — degrade to non-federated
        logger.warning(
            "retrieval.federation_active.check_failed",
            error=str(exc),
        )
        _federation_active = False
        _federation_active_checked_at = now
    return _federation_active


def _get_pipeline() -> RetrievalPipeline:
    """Get or create the retrieval pipeline singleton."""
    global _pipeline
    if _pipeline is None:
        config = RetrievalConfig()
        # Phase-9 fix: ``ArcadeConfig()`` with no args picks up the
        # field defaults (timeout=30s, host=localhost, etc.) instead
        # of pulling from operator settings. The retrieval
        # ``build_indexes()`` call fetches all vertices in one query,
        # which exceeds 30s on a modest corpus. Use
        # ``ArcadeConfig.from_settings(get_settings())`` so the
        # operator's ARCADE_TIMEOUT, ARCADE_HOST, etc. are honored.
        from src.shared.config import get_settings

        client = ArcadeClient(
            config=ArcadeConfig.from_settings(get_settings())
        )
        semantic_index = SemanticSearchIndex(
            ollama_base_url=config.ollama_base_url,
            model=config.embedding_model,
        )
        bm25_index = BM25SearchIndex()
        reranker = CrossEncoderReranker(model_name=config.reranker_model)
        _pipeline = RetrievalPipeline(
            client=client,
            config=config,
            semantic_index=semantic_index,
            bm25_index=bm25_index,
            reranker=reranker,
        )
    return _pipeline


@router.post("/query", response_model=None)
async def retrieval_query(
    query: RetrievalQuery, request: Request
):
    """Execute full retrieval pipeline.

    D267 (Chunk 35b): after the pipeline returns, the route handler
    generates a server-side ``query_event_id`` (UUID4, B1 resolution),
    surfaces it on the response, and asynchronously persists a
    ``Query_Event`` + ``Response_Event`` audit pair plus
    ``retrieved_from`` edges. Writer faults never break the query path.

    Chunk 42 (CP9, D335) — Defense-in-depth Layer 3: after the
    pipeline returns, results are post-filtered through
    ``Enforcer.enforce(principal, "graph_entity", grace_id, "view")``.
    The post-filter lives at the API layer; ``src/retrieval/*`` is
    unchanged (CF3 holds). When no matrix is active the enforcer
    default-denies (OWASP A01) and the response is returned with an
    empty result list rather than 403 — read paths degrade quietly.

    Chunk 52 (D384, D406–D408) — Federation-aware conditional branch:
    when ``_federation_active`` is True (at least one child namespace
    registered), the route delegates to ``federated_query()`` via
    a ``NamespaceQueryFn`` wrapper. The existing permission post-filter
    runs on the merged response. ``response_model=None`` so both
    ``RetrievalResponse`` and ``FederatedRetrievalResponse`` serialize
    correctly. D213 unaffected — contract check verifies class-definition
    presence, not route decorators.
    """
    # Chunk 52 — federation-active branch.
    if _is_federation_active():
        return await _federated_retrieval_query(query, request)

    pipeline = _get_pipeline()
    try:
        response = await pipeline.query(query)
    except ConnectionError as exc:
        logger.error("retrieval.query.connection_error", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=503, detail="Graph or retrieval backend unavailable"
        ) from exc
    except ArcadeDBError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    except Exception as exc:
        logger.error("retrieval.query.error", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=500, detail="Internal error during retrieval"
        ) from exc

    # D335 — post-filter wrapper (additive at the API layer; no
    # changes to src/retrieval/*).
    response = await _apply_permission_post_filter(response, request)

    # D267 — server-generated query_event_id surfacing + writer.
    query_event_id = uuid4()
    response.query_event_id = query_event_id

    # D349 (Chunk 43 CP5) — best-effort sensitivity tag annotation.
    # Resolved BEFORE persist_query_response so tags land in the same
    # INSERT (D345). Failures fall through to log-and-continue posture
    # (D267) — the query path never breaks because annotation broke.
    sensitivity_tags, sensitivity_tags_matrix_id = _resolve_active_matrix_tags()

    try:
        await persist_query_response(
            client=pipeline.client,
            query_event_id=str(query_event_id),
            query_text=query.query_text,
            results=list(response.results or []),
            response_metadata={
                "retrieval_mode": response.retrieval_mode,
                "strategies_fired": list(response.strategy_contributions.keys()),
                "total_candidates": response.total_candidates,
                "result_count": len(response.results or []),
                "serialization_format": response.serialization_format,
                "latency_ms_total": float(
                    response.latency_ms.get("total")
                    or sum(response.latency_ms.values())
                ),
            },
            sensitivity_tags=sensitivity_tags,
            sensitivity_tags_matrix_id=sensitivity_tags_matrix_id,
            # D377 (Chunk 45 CP5) — stamp support session on Query_Event.
            support_session_id=getattr(request.state, "support_session_id", None),
        )
    except Exception as exc:  # pragma: no cover — log-and-continue
        logger.warning(
            "retrieval.query_event_writer.skipped",
            query_event_id=str(query_event_id),
            error=str(exc),
        )

    return response


def _resolve_active_matrix_tags() -> tuple[list[str] | None, str | None]:
    """Resolve the active matrix tag set for audit-trail annotation (D349).

    Returns ``(tags, matrix_id_str)`` — both ``None`` when no active
    matrix is loaded or any failure short-circuits resolution. The
    function never raises: D267 log-and-continue posture means the
    query path is never broken by Sensitivity Gate annotation faults.
    """
    try:
        session_factory = get_session_factory()
        session = session_factory()
        try:
            matrix_row = _matrix_repo.get_active_matrix(session)
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 — D267 log-and-continue
        logger.warning(
            "retrieval.sensitivity_annotation.matrix_lookup_failed",
            error=str(exc),
        )
        return None, None

    if matrix_row is None:
        return None, None

    payload = matrix_row.get("payload")
    matrix_id = matrix_row.get("permission_matrix_id")
    matrix_id_str = str(matrix_id) if matrix_id is not None else None

    try:
        if isinstance(payload, dict):
            matrix = PermissionMatrix.model_validate(payload)
        else:
            matrix = PermissionMatrix.model_validate_json(payload)
    except Exception as exc:  # noqa: BLE001 — D267 log-and-continue
        logger.warning(
            "retrieval.sensitivity_annotation.matrix_parse_failed",
            matrix_id=matrix_id_str,
            error=str(exc),
        )
        return None, matrix_id_str

    tag_names: set[str] = set()
    for cluster in matrix.role_clusters or []:
        for tag in cluster.sensitivity_tags or []:
            if tag.name:
                tag_names.add(tag.name)
        for rule in cluster.access_rules or []:
            for tag in rule.sensitivity_tags or []:
                if tag.name:
                    tag_names.add(tag.name)

    if not tag_names:
        return None, matrix_id_str

    return sorted(tag_names), matrix_id_str


async def _federated_retrieval_query(
    query: RetrievalQuery, request: Request
):
    """Federation-active code path (Chunk 52, D384/D406–D408).

    Constructs a NamespaceQueryFn wrapping pipeline.query() and
    delegates to federated_query(). The existing permission post-filter
    runs on the merged response. Invariant: this function lives in
    src/api/ (outside CF3) and is the sole construction site for the
    NamespaceQueryFn callable.
    """
    from src.federation.models import FederationConfig
    from src.retrieval.federation_router import (
        FederatedRetrievalResponse,
        NamespaceTarget,
        QueryRoutingConfig,
        federated_query,
    )

    pipeline = _get_pipeline()

    # Construct NamespaceQueryFn — wraps pipeline.query() with per-namespace
    # entity_types scoping. Empty caller entity_types -> use target.prefixed_types;
    # non-empty -> intersection.
    async def namespace_query_fn(
        q: RetrievalQuery, target: NamespaceTarget
    ) -> RetrievalResponse:
        scoped_types = list(target.prefixed_types)
        if q.entity_types:
            scoped_types = [
                t for t in q.entity_types if t in target.prefixed_types
            ] or list(target.prefixed_types)

        scoped_query = RetrievalQuery(
            query_text=q.query_text,
            seed_entity_ids=q.seed_entity_ids,
            temporal_start=q.temporal_start,
            temporal_end=q.temporal_end,
            entity_types=scoped_types,
            top_k=q.top_k,
            iterative_mode=q.iterative_mode,
        )
        return await pipeline.query(scoped_query)

    # Load federation + routing config.
    federation_config = FederationConfig()
    routing_config = QueryRoutingConfig()
    try:
        import yaml

        with open("config/federation.yaml") as f:
            raw = yaml.safe_load(f) or {}
        federation_config = FederationConfig(**{
            k: v for k, v in raw.items() if k != "query_routing"
        })
        qr = raw.get("query_routing", {})
        if qr:
            routing_config = QueryRoutingConfig(
                per_namespace_timeout_seconds=qr.get(
                    "per_namespace_timeout_seconds", 5.0
                ),
                mother_timeout_posture=qr.get(
                    "mother_timeout_posture", "degrade"
                ),
                circuit_breaker_cooldown_seconds=qr.get(
                    "circuit_breaker_cooldown_seconds", 60.0
                ),
            )
    except Exception as exc:  # noqa: BLE001 — degrade to defaults
        logger.warning(
            "retrieval.federation_config.load_failed",
            error=str(exc),
        )

    # Open a DB session for resolve_target_namespaces.
    session_factory = get_session_factory()
    session = session_factory()
    principal = from_admission_tree(request)

    try:
        response = await federated_query(
            query,
            namespace_query_fn=namespace_query_fn,
            db=session,
            principal=principal,
            federation_config=federation_config,
            routing_config=routing_config,
        )
    except HTTPException:
        raise
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ArcadeDBError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    except Exception as exc:
        logger.error(
            "retrieval.federated_query.error",
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()

    # D335 — post-filter on merged response.
    response = await _apply_permission_post_filter_federated(response, request)

    # D267 — server-generated query_event_id.
    query_event_id = uuid4()
    response.query_event_id = query_event_id

    # D349 — best-effort sensitivity tag annotation.
    sensitivity_tags, sensitivity_tags_matrix_id = _resolve_active_matrix_tags()

    try:
        await persist_query_response(
            client=pipeline.client,
            query_event_id=str(query_event_id),
            query_text=query.query_text,
            results=list(response.results or []),
            response_metadata={
                "retrieval_mode": response.retrieval_mode,
                "strategies_fired": list(response.strategy_contributions.keys()),
                "total_candidates": response.total_candidates,
                "result_count": len(response.results or []),
                "serialization_format": response.serialization_format,
                "latency_ms_total": float(
                    sum(response.latency_ms.values())
                    if response.latency_ms
                    else 0.0
                ),
                "federated": True,
                "source_namespaces": response.source_namespaces,
            },
            sensitivity_tags=sensitivity_tags,
            sensitivity_tags_matrix_id=sensitivity_tags_matrix_id,
            support_session_id=getattr(request.state, "support_session_id", None),
        )
    except Exception as exc:  # pragma: no cover — log-and-continue
        logger.warning(
            "retrieval.query_event_writer.skipped",
            query_event_id=str(query_event_id),
            error=str(exc),
        )

    return response


async def _fetch_sensitivity_tags_for_ids(grace_ids: list[str]) -> dict[str, str]:
    """Fetch the ``sensitivity_tags`` bar-form string per grace_id from the graph.

    F-50 (validation run, 2026-07-01) capture-the-why: the D521 two-zone
    post-fetch filter previously read ``RankedResult.properties["sensitivity_tags"]``,
    but real retrieval responses ship ``properties == {}`` (the strategies /
    serializer do not populate domain properties — see also F-21). The privileged
    vertex was therefore served under a ratified matrix that forbade ``privileged``.
    The tag is only trustworthy from the graph itself, so the enforcement path
    must fetch it by grace_id rather than trust serializer-stripped properties.

    Best-effort: on any graph error we return an empty map and the caller falls
    back to whatever ``properties`` carried (never LESS restrictive than before).
    """
    if not grace_ids:
        return {}
    id_list = ", ".join(f"'{escape_cypher_string(gid)}'" for gid in grace_ids)
    cypher = (
        f"MATCH (n) WHERE n.grace_id IN [{id_list}] "
        f"RETURN n.grace_id AS gid, n.sensitivity_tags AS tags"
    )
    try:
        pipeline = _get_pipeline()
        result = await pipeline.client.execute_cypher(cypher)
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment; enforce() + properties fallback still hold
        logger.warning("sensitivity_tag_fetch_failed", error=str(exc))
        return {}
    out: dict[str, str] = {}
    for row in result.get("result", []) or []:
        gid = row.get("gid")
        tags = row.get("tags")
        if gid and tags:
            out[str(gid)] = str(tags)
    return out


def _resolve_result_forbidden(
    entity_tags_str: str,
    graph_tags_str: str,
    forbidden_tags: set[str],
) -> bool:
    """True if the union of property-carried + graph-fetched tags hits a forbidden tag.

    Union-of-sources is deliberately the *more* restrictive choice (F-50): a
    result is dropped if EITHER source shows a forbidden tag, so a stripped
    ``properties`` map can never widen access.
    """
    from src.ingestion.communications.sensitivity_tagger import tags_from_bar_form

    entity_tags: set[str] = set()
    for src_str in (entity_tags_str, graph_tags_str):
        if src_str:
            entity_tags |= set(tags_from_bar_form(src_str))
    return bool(entity_tags & forbidden_tags)


def _scrub_serialized_context(context: str, dropped: list) -> str:
    """Remove dropped-vertex content from the LLM-facing context (F2-10).

    Two-zone enforcement dropped privileged vertices from ``results`` but the
    pipeline assembles ``serialized_context`` BEFORE this route-level filter
    runs, so a restricted principal's response still carried the forbidden
    entity verbatim — and every consumer that feeds the context to an LLM
    (regeneration, MCP answer paths, draft guidance) leaked two-zone-denied
    content. Line-filter the context against the dropped vertices' names and
    grace_ids (name matching also removes edge lines that reference the
    dropped entity as an endpoint).
    """
    if not context or not dropped:
        return context
    needles: list[str] = []
    for r in dropped:
        name = getattr(r, "name", None)
        if name and len(str(name)) >= 3:
            needles.append(str(name))
        gid = getattr(r, "grace_id", None)
        if gid:
            needles.append(str(gid))
    if not needles:
        return context
    lines = context.splitlines()
    kept = [line for line in lines if not any(n in line for n in needles)]
    if len(kept) == len(lines):
        return context  # nothing matched — preserve the original verbatim
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# F-0047b / ISS-0055 Layer 2 — evidence_scoped enforcement posture (CP5).
#
# Capture-the-why: under the legacy "deny" posture a shared canonical entity
# (a Person, a Property) vanished ENTIRELY for restricted principals because
# ONE privileged email contributed a tag to its irreversible union
# (F-0047b). With Layer-1 provenance (`sensitivity_tag_sources`,
# `sensitivity_source_total`, `_privileged_props`) the "evidence_scoped"
# posture can distinguish:
#   * INHERITED-partial (tag write-count < total write-count — clean
#     evidence exists): SERVE the vertex, scrub privileged-contributed
#     properties from the result AND the serialized context, and drop
#     forbidden-tagged edge lines.
#   * UNIVERSAL (tag count == total) or provenance-ABSENT (pre-Layer-1
#     vertices, Document_Chunk / Image_Asset self-tagged vertices): DROP in
#     both postures — existence itself is privileged; missing provenance
#     fails safe.
# CP4/CP5 interaction decision: the cypher rewriter (CP4) has ZERO
# production call sites — verified 2026-07-03 via repo-wide grep: only
# `src/permissions/cypher_rewriter.py` itself and tests import `rewrite`.
# The retrieval query path is enforced solely by this CP5 post-fetch filter
# (which is exactly why F-50 existed), so no CP4 posture-awareness is
# needed today. If CP4 gains a production call site under evidence_scoped
# its vertex predicates would OVER-exclude (fail-safe direction: restrict,
# never leak); making CP4 posture-aware is deferred until such a call site
# exists.
# Fail-safe rule throughout: ANY failure in the evidence_scoped machinery
# (provenance fetch, JSON parse, edge fetch) degrades the response to the
# "deny" posture.
# ---------------------------------------------------------------------------


def _parse_json_safe(default, raw):
    """Best-effort JSON parse for vertex-carried provenance strings."""
    import json

    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


async def _fetch_sensitivity_records_for_ids(
    grace_ids: list[str],
) -> dict[str, dict]:
    """Fetch tags + Layer-1 provenance per grace_id. RAISES on graph failure.

    Unlike ``_fetch_sensitivity_tags_for_ids`` (deny path, best-effort
    empty-map fallback), this fetch must raise: under evidence_scoped a
    failed fetch means partial-vs-universal cannot be distinguished, so the
    caller degrades the whole response to the deny posture.
    """
    if not grace_ids:
        return {}
    id_list = ", ".join(f"'{escape_cypher_string(gid)}'" for gid in grace_ids)
    cypher = (
        f"MATCH (n) WHERE n.grace_id IN [{id_list}] "
        f"RETURN n.grace_id AS gid, n.sensitivity_tags AS tags, "
        f"n.sensitivity_tag_sources AS tag_sources, "
        f"n.sensitivity_source_total AS source_total, "
        f"n._privileged_props AS privileged_props"
    )
    pipeline = _get_pipeline()
    result = await pipeline.client.execute_cypher(cypher)
    out: dict[str, dict] = {}
    for row in result.get("result", []) or []:
        if not isinstance(row, dict):
            continue
        gid = row.get("gid")
        if gid:
            out[str(gid)] = row
    return out


def _classify_evidence_scoped(
    entity_tags_str: str,
    record: dict | None,
    forbidden_tags: set[str],
) -> tuple[str, list[str]]:
    """Classify one result under the evidence_scoped posture.

    Returns ``("serve", [])``, ``("drop", [])``, or
    ``("scrub", privileged_prop_names)``.
    """
    from src.ingestion.communications.sensitivity_tagger import tags_from_bar_form

    union_tags: set[str] = set()
    if entity_tags_str:
        union_tags |= set(tags_from_bar_form(entity_tags_str))
    if record and record.get("tags"):
        union_tags |= set(tags_from_bar_form(str(record["tags"])))

    hit = union_tags & forbidden_tags
    if not hit:
        return ("serve", [])

    # Forbidden tag present — provenance decides partial vs universal.
    if not record:
        # No graph row at all: fail safe (existence-privileged).
        return ("drop", [])
    total = record.get("source_total")
    if isinstance(total, bool) or not isinstance(total, (int, float)) or total <= 0:
        # Provenance absent (pre-Layer-1 vertex, or self-tagged
        # Document_Chunk / Image_Asset which never gets stamped): drop.
        return ("drop", [])
    sources = _parse_json_safe({}, record.get("tag_sources"))
    if not sources:
        return ("drop", [])
    for tag in hit:
        rec = sources.get(tag)
        if not isinstance(rec, dict):
            return ("drop", [])
        cnt = rec.get("count")
        if isinstance(cnt, bool) or not isinstance(cnt, (int, float)) or cnt <= 0:
            # Legacy record without per-write count: fall back to id math.
            ids = rec.get("ids")
            overflow = rec.get("overflow")
            cnt = (len(ids) if isinstance(ids, list) else 0) + (
                overflow if isinstance(overflow, (int, float)) else 0
            )
        if cnt <= 0 or cnt >= total:
            # Universal (every counted write carried the tag) — or
            # incoherent provenance: existence-privileged, drop.
            return ("drop", [])

    priv_props = _parse_json_safe([], record.get("privileged_props"))
    return ("scrub", [str(p) for p in priv_props])


async def _classify_results_evidence_scoped(
    results: list, forbidden_tags: set[str]
) -> tuple[dict[str, tuple[str, list[str]]], list[dict]]:
    """Per-result verdicts + forbidden-tagged edge pairs among the results.

    Raises on any graph failure — caller degrades to the deny posture.
    """
    gids = [r.grace_id for r in results]
    records = await _fetch_sensitivity_records_for_ids(gids)
    verdicts: dict[str, tuple[str, list[str]]] = {}
    for r in results:
        verdicts[r.grace_id] = _classify_evidence_scoped(
            (r.properties or {}).get("sensitivity_tags", ""),
            records.get(r.grace_id),
            forbidden_tags,
        )
    edge_pairs = await _fetch_forbidden_edge_pairs(gids, forbidden_tags)
    return verdicts, edge_pairs


async def _fetch_forbidden_edge_pairs(
    grace_ids: list[str], forbidden_tags: set[str]
) -> list[dict]:
    """Endpoint pairs of forbidden-tagged edges among the result vertices.

    Layer 2 (c): relationship lines carrying a forbidden tag must drop from
    the serialized context even when both endpoints are servable. Raises on
    graph failure (fail-safe handled by the caller).
    """
    if not grace_ids or not forbidden_tags:
        return []
    id_list = ", ".join(f"'{escape_cypher_string(gid)}'" for gid in grace_ids)
    tag_preds = " OR ".join(
        f"r.sensitivity_tags CONTAINS '|{tag}|'" for tag in sorted(forbidden_tags)
    )
    cypher = (
        f"MATCH (a)-[r]->(b) "
        f"WHERE a.grace_id IN [{id_list}] AND b.grace_id IN [{id_list}] "
        f"AND ({tag_preds}) "
        f"RETURN a.grace_id AS a_gid, a.name AS a_name, "
        f"b.grace_id AS b_gid, b.name AS b_name"
    )
    pipeline = _get_pipeline()
    result = await pipeline.client.execute_cypher(cypher)
    return [row for row in result.get("result", []) or [] if isinstance(row, dict)]


def _entity_needles(name, gid) -> list[str]:
    needles: list[str] = []
    if name and len(str(name)) >= 3:
        needles.append(str(name))
    if gid:
        needles.append(str(gid))
    return needles


def _scrub_evidence_scoped_context(
    context: str,
    scrubbed: list[tuple[object, list[str]]],
    edge_pairs: list[dict],
) -> str:
    """Scrub privileged-prop mentions + forbidden-edge lines from context.

    Conservative line filter (extends the F2-10 mechanics):
    * a line mentioning a scrubbed entity AND any of its privileged prop
      names is dropped (may drop the entity's whole inline-props line —
      fail-safe direction: over-scrub, never leak);
    * a line mentioning BOTH endpoints of a forbidden-tagged edge is
      dropped (relationship lines reference both endpoints).
    """
    if not context or (not scrubbed and not edge_pairs):
        return context

    lines = context.splitlines()
    kept: list[str] = []
    for line in lines:
        drop = False
        for result, priv_props in scrubbed:
            if not priv_props:
                continue
            ent_needles = _entity_needles(
                getattr(result, "name", None), getattr(result, "grace_id", None)
            )
            if any(n in line for n in ent_needles) and any(
                p in line for p in priv_props
            ):
                drop = True
                break
        if not drop:
            for pair in edge_pairs:
                a_needles = _entity_needles(pair.get("a_name"), pair.get("a_gid"))
                b_needles = _entity_needles(pair.get("b_name"), pair.get("b_gid"))
                if (
                    a_needles
                    and b_needles
                    and any(n in line for n in a_needles)
                    and any(n in line for n in b_needles)
                ):
                    drop = True
                    break
        if not drop:
            kept.append(line)
    if len(kept) == len(lines):
        return context
    return "\n".join(kept)


async def _resolve_sensitivity_enforcement(
    principal, matrix, results: list, forbidden_tags: set[str]
) -> tuple[str, dict[str, str], dict[str, tuple[str, list[str]]], list[dict]]:
    """Shared CP5 setup: posture + per-posture enrichment data.

    Returns ``(posture, graph_tags, verdicts, edge_pairs)``:
    * posture "deny": ``graph_tags`` populated (byte-identical legacy path);
      ``verdicts`` / ``edge_pairs`` empty.
    * posture "evidence_scoped": ``verdicts`` + ``edge_pairs`` populated;
      ``graph_tags`` empty.
    Degrades to "deny" on ANY evidence_scoped machinery failure.
    """
    posture = "deny"
    graph_tags: dict[str, str] = {}
    verdicts: dict[str, tuple[str, list[str]]] = {}
    edge_pairs: list[dict] = []
    if not forbidden_tags:
        return posture, graph_tags, verdicts, edge_pairs

    posture = resolve_enforcement_posture(principal, matrix)
    if posture == "evidence_scoped":
        try:
            verdicts, edge_pairs = await _classify_results_evidence_scoped(
                results, forbidden_tags
            )
        except Exception as exc:  # noqa: BLE001 — fail-safe: degrade to deny
            logger.warning(
                "sensitivity.evidence_scoped_degraded_to_deny", error=str(exc)
            )
            posture = "deny"
            verdicts, edge_pairs = {}, []
    if posture == "deny":
        # F-50 legacy path — best-effort graph tag fetch, unchanged.
        graph_tags = await _fetch_sensitivity_tags_for_ids(
            [r.grace_id for r in results]
        )
    return posture, graph_tags, verdicts, edge_pairs


async def _apply_permission_post_filter_federated(
    response: "FederatedRetrievalResponse", request: Request
) -> "FederatedRetrievalResponse":
    """Permission post-filter for federated responses (mirrors _apply_permission_post_filter).

    Operates on FederatedRetrievalResponse — same enforcer logic as the
    non-federated path but also maintains result_source_namespaces alignment.

    D521 extends with sensitivity-tag post-filter (same as standard path).
    """
    from src.retrieval.federation_router import FederatedRetrievalResponse

    enforcer = get_enforcer()
    if enforcer.matrix is None:
        return response

    principal = from_admission_tree(request)

    # D521 — derive forbidden sensitivity tags for this principal.
    forbidden_tags = resolve_forbidden_tags(principal, enforcer.matrix)

    # F-0047b / ISS-0055 Layer 2 — posture-aware enforcement setup.
    # "deny" (default + fail-safe): F-50 legacy graph-tag fetch, byte-
    # identical filtering below. "evidence_scoped": Layer-1 provenance
    # verdicts + forbidden-edge pairs.
    posture, graph_tags, verdicts, edge_pairs = (
        await _resolve_sensitivity_enforcement(
            principal, enforcer.matrix, list(response.results or []), forbidden_tags
        )
    )

    surviving: list = []
    surviving_ns: list[str] = []
    dropped: list = []
    scrubbed: list[tuple[object, list[str]]] = []
    for idx, result in enumerate(response.results or []):
        decision = enforcer.enforce(
            principal, "graph_entity", result.grace_id, "view"
        )
        if not isinstance(decision, Allow):
            dropped.append(result)
            continue
        # D521 — post-fetch sensitivity-tag filter (two-zone enforcement).
        if forbidden_tags:
            if posture == "deny":
                if _resolve_result_forbidden(
                    result.properties.get("sensitivity_tags", ""),
                    graph_tags.get(result.grace_id, ""),
                    forbidden_tags,
                ):
                    dropped.append(result)
                    continue
            else:
                # ISS-0055 Layer 2 evidence_scoped: serve partial-inherited
                # vertices with privileged props scrubbed; drop universal /
                # provenance-less / chunk results. Missing verdict -> drop
                # (fail-safe).
                verdict, priv_props = verdicts.get(result.grace_id, ("drop", []))
                if verdict == "drop":
                    dropped.append(result)
                    continue
                if verdict == "scrub":
                    for p in priv_props:
                        result.properties.pop(p, None)
                    scrubbed.append((result, priv_props))
        surviving.append(result)
        if idx < len(response.result_source_namespaces):
            surviving_ns.append(response.result_source_namespaces[idx])

    response.results = surviving
    response.result_source_namespaces = surviving_ns

    # F2-10 — the context string was assembled pre-filter; scrub dropped
    # vertices out of the LLM-facing payload too.
    if dropped and getattr(response, "serialized_context", None):
        response.serialized_context = _scrub_serialized_context(
            response.serialized_context, dropped
        )
    # ISS-0055 Layer 2 — scrub privileged-prop mentions of served-partial
    # vertices and forbidden-tagged relationship lines from the context.
    if (scrubbed or edge_pairs) and getattr(response, "serialized_context", None):
        response.serialized_context = _scrub_evidence_scoped_context(
            response.serialized_context, scrubbed, edge_pairs
        )

    # Recompute strategy contributions.
    contributions: dict[str, int] = {}
    for r in surviving:
        for s in getattr(r, "contributing_strategies", []) or []:
            contributions[s] = contributions.get(s, 0) + 1
    response.strategy_contributions = contributions

    return response


@router.get("/query-events/{query_event_id}/subgraph")
async def get_query_event_subgraph(query_event_id: UUID) -> dict:
    """Return Cytoscape elements for a single Query_Event neighborhood (D267).

    B2 resolution: the subgraph projection includes the ``Query_Event``
    vertex and the retrieved domain entities. ``Response_Event`` is
    excluded — it exists in the graph for audit but adds nothing to the
    "which entities did this query retrieve?" answer.

    D217 (NB2 clarification): ``rank_ordinal`` is returned in the API
    JSON (programmatic Cytoscape layout use). The frontend component is
    responsible for never rendering it as DOM text or ``data-*`` numeric
    attribute.
    """
    pipeline = _get_pipeline()
    qeid = str(query_event_id)
    escaped = escape_cypher_string(qeid)
    cypher = (
        f"MATCH (q:Query_Event {{query_event_id: '{escaped}'}})"
        f"-[r:retrieved_from]->(e) "
        f"RETURN q, r, e"
    )
    try:
        result = await pipeline.client.execute_cypher(cypher)
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ArcadeDBError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc

    rows = result.get("result", []) or []

    if not rows:
        # Confirm absence vs. zero-edge query: a Query_Event vertex may
        # exist with zero retrieved_from edges (no results). Probe directly.
        probe = await pipeline.client.execute_cypher(
            f"MATCH (q:Query_Event {{query_event_id: '{escaped}'}}) "
            f"RETURN q LIMIT 1"
        )
        probe_rows = probe.get("result", []) or []
        if not probe_rows:
            raise HTTPException(status_code=404, detail="Query event not found")
        # Vertex exists, no edges — return Query_Event-only graph.
        q_vertex = _extract_subgraph_vertex(probe_rows[0], "q")
        return {
            "query_event_id": qeid,
            "nodes": [_query_event_node(q_vertex, qeid)],
            "edges": [],
        }

    # Project rows into Cytoscape elements.
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_node_ids: set[str] = set()
    query_node_grace_id: str | None = None

    for row in rows:
        if not isinstance(row, dict):
            continue
        q_vertex = _extract_subgraph_vertex(row, "q")
        e_vertex = _extract_subgraph_vertex(row, "e")
        r_edge = _extract_subgraph_vertex(row, "r")

        if q_vertex and query_node_grace_id is None:
            query_node_grace_id = q_vertex.get("grace_id") or qeid
            if query_node_grace_id not in seen_node_ids:
                nodes.append(_query_event_node(q_vertex, qeid))
                seen_node_ids.add(query_node_grace_id)

        if e_vertex:
            entity_grace_id = e_vertex.get("grace_id")
            if entity_grace_id and entity_grace_id not in seen_node_ids:
                nodes.append(_entity_node(e_vertex))
                seen_node_ids.add(entity_grace_id)

            if r_edge and query_node_grace_id and entity_grace_id:
                edge_grace_id = r_edge.get("grace_id") or (
                    f"{query_node_grace_id}->{entity_grace_id}"
                )
                edges.append(
                    {
                        "data": {
                            "id": edge_grace_id,
                            "source": query_node_grace_id,
                            "target": entity_grace_id,
                            "type": "retrieved_from",
                            "rank_ordinal": r_edge.get("rank_ordinal"),
                        }
                    }
                )

    return {"query_event_id": qeid, "nodes": nodes, "edges": edges}


def _extract_subgraph_vertex(row: dict, alias: str) -> dict | None:
    """ArcadeDB may return a row keyed by alias or by raw column names."""
    val = row.get(alias)
    if isinstance(val, dict):
        return val
    return None


def _query_event_node(q_vertex: dict, query_event_id: str) -> dict:
    """Cytoscape node element for the Query_Event vertex (group=query_event)."""
    grace_id = q_vertex.get("grace_id") or query_event_id
    label = q_vertex.get("query_text") or "Query_Event"
    return {
        "data": {
            "id": grace_id,
            "label": label,
            "type": "Query_Event",
            "group": "query_event",
        }
    }


def _entity_node(e_vertex: dict) -> dict:
    """Cytoscape node element for a retrieved domain entity (group=entity)."""
    grace_id = e_vertex.get("grace_id") or ""
    label = e_vertex.get("name") or grace_id
    entity_type = e_vertex.get("@type") or e_vertex.get("entity_type") or "Entity"
    return {
        "data": {
            "id": grace_id,
            "label": label,
            "type": entity_type,
            "group": "entity",
        }
    }


@router.post("/build-indexes")
async def build_indexes() -> dict:
    """Rebuild semantic + BM25 indexes from graph entities."""
    pipeline = _get_pipeline()
    try:
        count = await pipeline.build_indexes()
        return {"status": "ok", "entity_count": count}
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ArcadeDBError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc


@router.get("/config", response_model=RetrievalConfig)
async def get_config() -> RetrievalConfig:
    """Return current retrieval configuration."""
    pipeline = _get_pipeline()
    return pipeline.config


async def _apply_permission_post_filter(
    response: RetrievalResponse, request: Request
) -> RetrievalResponse:
    """Filter ``response.results`` through the permission enforcer.

    Defense-in-depth Layer 3 (D335 / CP9). Lives at the API layer so
    no file in ``src/retrieval/*`` is edited (CF3 hard-lock holds).

    Activation contract: when no ``PermissionMatrix`` is active, the
    post-filter is a no-op (the engine ships dormant; activation
    arrives with the first ratification). Once a matrix is active,
    each :class:`RankedResult` is gated by
    ``enforce(principal, "graph_entity", grace_id, "view")`` and
    denied rows are silently dropped. ``strategy_contributions`` is
    recomputed over the surviving rows.

    D521 extends with sensitivity-tag post-filter: vertices whose
    ``sensitivity_tags`` contain a tag forbidden to the principal are
    silently dropped. This covers ANN ``vectorNeighbors()`` paths that
    bypass the cypher rewriter (D521 fallback / two-zone enforcement).
    """
    enforcer = get_enforcer()
    if enforcer.matrix is None:
        # Dormant — no matrix ratified yet. Pass through unfiltered.
        return response

    principal = from_admission_tree(request)

    # D521 — derive forbidden sensitivity tags for this principal.
    forbidden_tags = resolve_forbidden_tags(principal, enforcer.matrix)

    # F-0047b / ISS-0055 Layer 2 — posture-aware enforcement setup.
    # "deny" (default + fail-safe): F-50 legacy graph-tag fetch (serializer
    # ships properties == {} — the old code read those empty props and let
    # privileged vertices through), byte-identical filtering below.
    # "evidence_scoped": Layer-1 provenance verdicts + forbidden-edge pairs.
    posture, graph_tags, verdicts, edge_pairs = (
        await _resolve_sensitivity_enforcement(
            principal, enforcer.matrix, list(response.results or []), forbidden_tags
        )
    )

    surviving: list = []
    dropped: list = []
    scrubbed: list[tuple[object, list[str]]] = []
    for result in response.results or []:
        decision = enforcer.enforce(
            principal, "graph_entity", result.grace_id, "view"
        )
        if not isinstance(decision, Allow):
            dropped.append(result)
            continue
        # D521 — post-fetch sensitivity-tag filter (two-zone enforcement
        # fallback for ANN vectorNeighbors paths that bypass the rewriter).
        if forbidden_tags:
            if posture == "deny":
                if _resolve_result_forbidden(
                    result.properties.get("sensitivity_tags", ""),
                    graph_tags.get(result.grace_id, ""),
                    forbidden_tags,
                ):
                    dropped.append(result)
                    continue
            else:
                # ISS-0055 Layer 2 evidence_scoped: serve partial-inherited
                # vertices with privileged props scrubbed; drop universal /
                # provenance-less / chunk results. Missing verdict -> drop
                # (fail-safe).
                verdict, priv_props = verdicts.get(result.grace_id, ("drop", []))
                if verdict == "drop":
                    dropped.append(result)
                    continue
                if verdict == "scrub":
                    for p in priv_props:
                        result.properties.pop(p, None)
                    scrubbed.append((result, priv_props))
        surviving.append(result)

    response.results = surviving

    # F2-10 — the context string was assembled pre-filter; scrub dropped
    # vertices out of the LLM-facing payload too.
    if dropped and getattr(response, "serialized_context", None):
        response.serialized_context = _scrub_serialized_context(
            response.serialized_context, dropped
        )
    # ISS-0055 Layer 2 — scrub privileged-prop mentions of served-partial
    # vertices and forbidden-tagged relationship lines from the context.
    if (scrubbed or edge_pairs) and getattr(response, "serialized_context", None):
        response.serialized_context = _scrub_evidence_scoped_context(
            response.serialized_context, scrubbed, edge_pairs
        )

    # Recompute strategy contribution counts from the surviving rows so
    # downstream consumers see a consistent picture.
    contributions: dict[str, int] = {}
    for r in surviving:
        for s in getattr(r, "contributing_strategies", []) or []:
            contributions[s] = contributions.get(s, 0) + 1
    response.strategy_contributions = contributions

    return response
