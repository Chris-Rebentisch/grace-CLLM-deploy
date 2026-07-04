"""MINE sampling harness — Measure of Information in Nodes and Edges.

MINE-inspired fact retention methodology applied to GrACE documents and
ontology. Extracts atomic facts from source text, resolves entity mentions
to graph entities, fetches neighborhood context, and judges whether each
fact is recoverable from the knowledge graph.

Internal metric name: "MINE retention score."

Known limitation: When extraction and judge models are both 7B on 16GB
hardware, the judge has similar blind spots as the extractor. Retention
scores are a relative baseline. Scores become properly independent when
hardware supports 72B extraction + 7B judge.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.extraction.instructor_client import ExtractionLLMClient, ExtractionLLMError
from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string
from src.graph.entity_ops import canonical_lookup
from src.graph.neighborhood import fetch_entity_neighborhood
from src.retrieval.retrieval_models import RankedResult
from src.retrieval.serializer import TemplateSerializer
from src.shared.database import Base

logger = structlog.get_logger()


def _normalize_fact_text(text: str) -> str:
    """Collapse whitespace for robust fact_text ↔ fact matching."""
    return " ".join(text.split()).strip()


def _mentions_for_fact(
    fact: str,
    fact_index: int,
    mentions: list[FactMention],
) -> list[list[str]]:
    """Resolve entity mentions for one fact using fact_text, then index fallback.

    Primary: match ``FactMention.fact_text`` to ``fact`` after normalization.
    Fallback: use ``mentions[fact_index]`` only when its ``fact_text`` matches
    ``fact`` after normalization. Otherwise return [] (avoid wrong pairing).
    """
    if not mentions:
        return []

    nf = _normalize_fact_text(fact)
    by_text: dict[str, FactMention] = {}
    for m in mentions:
        key = _normalize_fact_text(m.fact_text)
        if key and key not in by_text:
            by_text[key] = m

    hit = by_text.get(nf)
    if hit is not None:
        return hit.mentioned_entities

    if fact_index < len(mentions):
        candidate = mentions[fact_index]
        if _normalize_fact_text(candidate.fact_text) == nf:
            return candidate.mentioned_entities
        logger.debug(
            "mine.mention_index_skipped_mismatch",
            fact_index=fact_index,
            expected_prefix=fact[:60],
            got_prefix=candidate.fact_text[:60],
        )

    return []


async def _fallback_name_lookup(
    arcade_client: ArcadeClient, name: str | None,
) -> str | None:
    """Type-agnostic name/alias lookup fallback for MINE seed resolution.

    F-033 / ISS-0015: ``canonical_lookup()`` is scoped to a single entity
    type (``MATCH (n:<type>)``). When the mention extractor guesses a type
    that does not match the vertex type actually in the graph — common for
    skill-path / direct-import entities, which carry no extraction-provenance
    linkage to disambiguate — the typed lookup finds nothing and retention
    falsely reports 0.0 against a populated graph (every judgment: "No
    matching entities found in the knowledge graph"). Fall back to matching
    the mention name against ANY vertex's name/aliases: exact
    case-insensitive first, then substring (catches identifiers like
    "GP-8894-1120" embedded in a longer canonical vertex name), so retention
    judges against what is actually in the graph.

    Returns grace_id if found, None otherwise.
    """
    if not name or not name.strip():
        return None
    escaped = escape_cypher_string(name.strip())
    queries = (
        # Pass 1: exact case-insensitive name/alias match, any vertex type.
        (
            "MATCH (n) "
            f"WHERE toLower(n.name) = toLower('{escaped}') "
            f"OR ANY(a IN n.aliases WHERE toLower(a) = toLower('{escaped}')) "
            "RETURN n.grace_id LIMIT 1"
        ),
        # Pass 2: substring match on vertex name.
        (
            "MATCH (n) "
            f"WHERE toLower(n.name) CONTAINS toLower('{escaped}') "
            "RETURN n.grace_id LIMIT 1"
        ),
    )
    for query in queries:
        try:
            result = await arcade_client.execute_cypher(query)
        except Exception:
            logger.debug("mine.fallback_lookup_query_failed", name=name)
            continue
        rows = result.get("result", [])
        if rows:
            row = rows[0]
            if isinstance(row, dict):
                gid = row.get("n.grace_id") or row.get("grace_id")
                if gid:
                    return gid
    return None


# ---------------------------------------------------------------------------
# Pydantic models for Instructor structured output
# ---------------------------------------------------------------------------


class FactList(BaseModel):
    """List of atomic facts extracted from source text."""

    facts: list[str] = Field(
        default_factory=list,
        description="List of atomic declarative fact sentences.",
    )


class FactMention(BaseModel):
    """Entity mentions identified in a single fact."""

    fact_text: str = Field(description="The fact text this mention belongs to.")
    mentioned_entities: list[list[str]] = Field(
        default_factory=list,
        description="List of [entity_type, name] pairs mentioned in this fact. Max 10.",
    )


class FactEntityMentions(BaseModel):
    """Structured entity mention extraction for all facts."""

    facts: list[FactMention] = Field(
        default_factory=list,
        description="Entity mentions per fact.",
    )


class FactJudgment(BaseModel):
    """LLM judgment on whether a fact is recoverable from graph context."""

    recovered: bool = Field(description="Whether the fact is recoverable from graph context.")
    reasoning: str = Field(description="Explanation of the judgment.")


# ---------------------------------------------------------------------------
# SQLAlchemy ORM model for mine_samples table
# ---------------------------------------------------------------------------


class MineSampleRow(Base):
    """SQLAlchemy ORM model for the mine_samples table."""

    __tablename__ = "mine_samples"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    source_text_hash = Column(Text, nullable=False)
    source_facts = Column(JSONB, nullable=False, default=list)
    judgments = Column(JSONB, nullable=False, default=list)
    total_facts = Column(Integer, nullable=False, default=0)
    recovered_facts = Column(Integer, nullable=False, default=0)
    retention_score = Column(Float, nullable=False, default=0.0)
    extraction_model = Column(Text, nullable=False, default="")
    judge_model = Column(Text, nullable=False, default="")
    schema_version_id = Column(PG_UUID(as_uuid=True), nullable=True)
    sampled_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    metadata_extra = Column(JSONB, default=dict)


# ---------------------------------------------------------------------------
# Helper: Neighborhood → TemplateSerializer inputs
# ---------------------------------------------------------------------------

# F-0048 / ISS-0039 (validation run, 2026-07-03): MINE retention scored
# 7.7% on a document whose facts WERE in the graph — the judge context listed
# an entity's edges but NO property values ("no information about the entity's
# name, jurisdiction…"). Root cause: ``fetch_entity_neighborhood()`` returns
# FULL vertex property maps including the 768-float ``_embedding`` vector;
# ``_entity_dict_to_ranked`` copied it into ``RankedResult.properties``, so
# the seed entity's single TemplateSerializer line (~10k chars) exceeded the
# serializer's WHOLE char budget and the entity loop broke at the FIRST
# entity — every entity line (and every property value) was dropped and only
# edge lines survived. Fix: exclude system-plane/bookkeeping properties from
# the judge context, truncate oversized values, and cap per-entity property
# count so each entity line is budget-bounded and the judge sees name +
# domain property values.
#
# ``sensitivity_tags`` is access-control plane (D519) and never LLM-facing
# prose — excluding it here preserves the existing sensitivity handling.
# ``valid_from``/``valid_to`` are deliberately KEPT: temporal validity is a
# domain fact the judge may need.
_SYSTEM_PLANE_PROP_KEYS: frozenset[str] = frozenset(
    {
        "_embedding",
        "_deprecated",
        "_deprecated_at",
        "extraction_confidence",
        "relationship_confidence",
        "extraction_event_id",
        "source_document_id",
        "schema_version",
        "ontology_module",
        "extracted_at",
        "created_at",
        "updated_at",
        "human_validated",
        "validation_timestamp",
        "validator_id",
        "superseded_by",
        "corroboration_status",
        "corroborating_sender_count",
        "sensitivity_tags",
    }
)

# Per-entity bounds so no single entity line can blow the serializer budget.
_MAX_PROP_VALUE_CHARS = 300
_MAX_PROPS_PER_ENTITY = 20

# F-0048 / ISS-0039: judge-context serialization budget (tokens). The
# TemplateSerializer default (2000) was sized for edge-only lines; with
# property values restored, merged multi-seed neighborhoods need more room
# for entities AND relationships. Entities serialize first, so property
# values are prioritized under pressure.
_GRAPH_CONTEXT_TOKEN_BUDGET = 4000


def neighborhood_to_serializer_inputs(
    neighborhood: dict,
    *,
    entity_cap: int = 50,
    edge_cap: int = 100,
) -> tuple[list[RankedResult], list[dict]]:
    """Convert fetch_entity_neighborhood() output to TemplateSerializer inputs.

    Maps seed + neighbor dicts to RankedResult instances and edge dicts
    to the relationship shape expected by TemplateSerializer.

    Args:
        neighborhood: Dict with "seed", "neighbors", "edges" keys.
        entity_cap: Maximum number of entities to include.
        edge_cap: Maximum number of edges to include.

    Returns:
        Tuple of (ranked_results, relationship_dicts).
    """
    _EXCLUDE_KEYS = {"@rid", "@cat", "@in", "@out"}

    results: list[RankedResult] = []

    # Seed entity first
    seed = neighborhood.get("seed", {})
    if seed:
        results.append(_entity_dict_to_ranked(seed, _EXCLUDE_KEYS))

    # Then neighbors
    for neighbor in neighborhood.get("neighbors", []):
        if len(results) >= entity_cap:
            break
        results.append(_entity_dict_to_ranked(neighbor, _EXCLUDE_KEYS))

    # Edges
    relationships: list[dict] = []
    for edge in neighborhood.get("edges", [])[:edge_cap]:
        rel = {
            "source_grace_id": edge.get("source_grace_id", ""),
            "target_grace_id": edge.get("target_grace_id", ""),
            "relationship_type": edge.get("relationship_type", "related_to"),
        }
        # Include extra edge properties
        for k, v in edge.items():
            if k not in ("source_grace_id", "target_grace_id", "relationship_type",
                         "@rid", "@type", "@cat", "@in", "@out"):
                rel[k] = v
        relationships.append(rel)

    return results, relationships


def _entity_dict_to_ranked(entity: dict, exclude_keys: set) -> RankedResult:
    """Convert an entity dict from ArcadeDB to a RankedResult.

    F-0048 / ISS-0039: system-plane properties (``_embedding`` above all) are
    excluded and per-entity property content is bounded so the judge context
    carries domain property values without a single oversized line evicting
    every entity from the token-budgeted TemplateSerializer output.
    """
    props: dict = {}
    for k, v in entity.items():
        if k in exclude_keys or k in ("grace_id", "@type") or v is None:
            continue
        if k in _SYSTEM_PLANE_PROP_KEYS:
            continue
        if isinstance(v, str):
            if len(v) > _MAX_PROP_VALUE_CHARS:
                v = v[:_MAX_PROP_VALUE_CHARS].rstrip() + "…"
        elif isinstance(v, (list, dict)) and len(str(v)) > _MAX_PROP_VALUE_CHARS:
            # Oversized structured value (stray vector / blob under a
            # non-system key) — drop rather than blow the line budget.
            continue
        props[k] = v
        if len(props) >= _MAX_PROPS_PER_ENTITY:
            break
    return RankedResult(
        grace_id=entity.get("grace_id", ""),
        entity_type=entity.get("@type", "Entity"),
        name=entity.get("name", "unknown"),
        properties=props,
        rerank_score=1.0,
        rrf_score=1.0,
        contributing_strategies=["neighborhood"],
    )


# ---------------------------------------------------------------------------
# Core components
# ---------------------------------------------------------------------------


class FactExtractor:
    """Extracts atomic facts from source text via LLM."""

    _SYSTEM_PROMPT = (
        "You are a fact extraction system. Extract all atomic facts from the "
        "given text as a list of simple declarative sentences. Each fact should "
        "be independently verifiable. Be exhaustive — capture every factual claim."
    )

    async def extract_facts(
        self, text: str, client: ExtractionLLMClient
    ) -> list[str]:
        """Extract atomic facts from source text.

        Returns list of fact strings. Empty list on LLM failure.
        """
        if not text.strip():
            return []

        try:
            result = await client.extract(
                system_prompt=self._SYSTEM_PROMPT,
                user_prompt=text,
                response_model=FactList,
            )
            return result.facts
        except ExtractionLLMError:
            logger.warning("mine.fact_extraction_failed", exc_info=True)
            return []

    _MENTION_SYSTEM_PROMPT = (
        "You are an entity mention extractor. For each fact, identify entities "
        "mentioned and their types from the ontology. Return each entity as an "
        "[entity_type, name] pair. Entity types must match the graph schema "
        "(e.g. Legal_Entity, Person, Insurance_Policy). Max 10 mentions per fact. "
        "For each item, set fact_text to the exact fact sentence it refers to "
        "(verbatim from the numbered list)."
    )

    async def extract_mentions(
        self, facts: list[str], client: ExtractionLLMClient
    ) -> list[FactMention]:
        """Extract entity mentions from facts for graph seed resolution.

        Returns list of FactMention with entity type/name pairs per fact.
        """
        if not facts:
            return []

        user_prompt = "Extract entity mentions from these facts:\n\n"
        for i, fact in enumerate(facts, 1):
            user_prompt += f"{i}. {fact}\n"

        try:
            result = await client.extract(
                system_prompt=self._MENTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=FactEntityMentions,
            )
            return result.facts
        except ExtractionLLMError:
            logger.warning("mine.mention_extraction_failed", exc_info=True)
            return []


class GraphFactChecker:
    """Judges whether facts are recoverable from graph context."""

    _SYSTEM_PROMPT = (
        "Given the following knowledge graph context, determine whether the "
        "following fact is recoverable. The fact does not need to appear "
        "verbatim — it counts as recovered if the information can be inferred "
        "from the entities and relationships shown. Respond with recovered=true "
        "or recovered=false and your reasoning."
    )

    async def check_fact(
        self, fact: str, graph_context: str, client: ExtractionLLMClient
    ) -> dict:
        """Judge whether a single fact is recoverable from graph context.

        Returns dict with 'recovered' (bool) and 'reasoning' (str).
        """
        user_prompt = (
            f"Graph context:\n{graph_context}\n\n"
            f"Fact to check: {fact}"
        )

        try:
            result = await client.extract(
                system_prompt=self._SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=FactJudgment,
            )
            return {
                "fact": fact,
                "recovered": result.recovered,
                "reasoning": result.reasoning,
            }
        except ExtractionLLMError:
            logger.warning("mine.fact_check_failed", fact=fact[:80], exc_info=True)
            return {
                "fact": fact,
                "recovered": False,
                "reasoning": "LLM judgment failed",
            }


class MINESampler:
    """Orchestrates MINE fact retention sampling for a document.

    Pipeline:
    1. Load document text from processed_documents table
    2. Extract atomic facts via LLM
    3. For each fact: resolve mentions → canonical_lookup
    4. Fetch neighborhood per seed via fetch_entity_neighborhood
    5. Convert via neighborhood_to_serializer_inputs → TemplateSerializer → string
    6. Judge each fact against graph context
    7. Compute retention_score = recovered / total
    8. Store results in mine_samples table

    Dedup: Uses (source_text_hash, extraction_model, judge_model) as unique
    key. Cache invalidation on ontology change is manual — delete rows to
    force re-run after major ontology updates.

    Two different ``processed_documents`` rows with identical ``extracted_text``
    share one dedup row; the cached sample may reference another document_id.

    Commits the SQLAlchemy session — callers must not assume an outer transaction
    is still open after ``sample_document`` returns.
    """

    def __init__(
        self,
        *,
        max_seeds_per_fact: int = 3,
        schema_version_id: UUID | None = None,
    ):
        self._max_seeds_per_fact = max_seeds_per_fact
        self._schema_version_id = schema_version_id
        self._fact_extractor = FactExtractor()
        self._fact_checker = GraphFactChecker()
        self._serializer = TemplateSerializer()

    async def sample_document(
        self,
        document_id: UUID,
        session: Session,
        client: ExtractionLLMClient,
        arcade_client: ArcadeClient,
    ) -> dict:
        """Run full MINE sampling pipeline for a document.

        Returns dict with retention_score, total_facts, recovered_facts,
        judgments, and the mine_sample row id.
        """
        from src.discovery.database import ProcessedDocumentRow

        # 1. Load document text
        doc_row = session.query(ProcessedDocumentRow).filter(
            ProcessedDocumentRow.id == document_id
        ).first()
        if not doc_row:
            raise ValueError(f"Document {document_id} not found in processed_documents")

        source_text = doc_row.extracted_text or ""
        if not source_text.strip():
            return self._store_empty_result(
                session, document_id, source_text, client
            )

        text_hash = hashlib.sha256(source_text.encode()).hexdigest()
        extraction_model = client.extraction_model
        judge_model = client.verification_model

        # Dedup check
        existing = session.query(MineSampleRow).filter(
            MineSampleRow.source_text_hash == text_hash,
            MineSampleRow.extraction_model == extraction_model,
            MineSampleRow.judge_model == judge_model,
        ).first()
        if existing:
            logger.info(
                "mine.dedup_hit",
                document_id=str(document_id),
                existing_id=str(existing.id),
            )
            return {
                "id": existing.id,
                "retention_score": existing.retention_score,
                "total_facts": existing.total_facts,
                "recovered_facts": existing.recovered_facts,
                "judgments": existing.judgments,
                "cached": True,
            }

        # 2. Extract facts
        facts = await self._fact_extractor.extract_facts(source_text, client)
        if not facts:
            return self._store_empty_result(
                session, document_id, source_text, client
            )

        # 3. Extract mentions and resolve to graph seeds
        mentions = await self._fact_extractor.extract_mentions(facts, client)

        # 4-6. For each fact: resolve seeds, fetch neighborhoods, judge
        judgments: list[dict] = []
        for i, fact in enumerate(facts):
            fact_mentions = _mentions_for_fact(fact, i, mentions)

            # Resolve mentions to grace_ids via canonical_lookup
            seed_ids: list[str] = []
            seen_gids: set[str] = set()
            for mention in fact_mentions[:10]:
                if len(mention) >= 2:
                    entity_type, name = mention[0], mention[1]
                    gid: str | None = None
                    try:
                        gid = await canonical_lookup(arcade_client, entity_type, name)
                    except Exception:
                        logger.debug(
                            "mine.lookup_failed",
                            entity_type=entity_type,
                            name=name,
                        )
                    # F-033 / ISS-0015: the type-scoped lookup missed (wrong
                    # type guess or no provenance linkage for direct-import
                    # entities). Fall back to type-agnostic name matching so
                    # retention does not report 0.0 purely because the typed
                    # lookup found nothing on a populated graph.
                    if not gid:
                        gid = await _fallback_name_lookup(arcade_client, name)
                        if gid:
                            logger.warning(
                                "mine.fallback_name_lookup_engaged",
                                entity_type=entity_type,
                                name=name,
                                grace_id=gid,
                            )
                    if gid and gid not in seen_gids:
                        seen_gids.add(gid)
                        seed_ids.append(gid)
                if len(seed_ids) >= self._max_seeds_per_fact:
                    break

            # Fetch and merge neighborhoods for seeds
            graph_context = await self._build_graph_context(
                arcade_client, seed_ids
            )

            # Judge fact
            judgment = await self._fact_checker.check_fact(
                fact, graph_context, client
            )
            judgments.append(judgment)

        # 7. Compute retention score
        recovered_count = sum(1 for j in judgments if j.get("recovered", False))
        total = len(facts)
        retention_score = recovered_count / total if total > 0 else 0.0

        # 8. Store in PostgreSQL
        sample_row = MineSampleRow(
            id=uuid4(),
            document_id=document_id,
            source_text_hash=text_hash,
            source_facts=facts,
            judgments=judgments,
            total_facts=total,
            recovered_facts=recovered_count,
            retention_score=round(retention_score, 4),
            extraction_model=extraction_model,
            judge_model=judge_model,
            schema_version_id=self._schema_version_id,
            sampled_at=datetime.now(UTC),
            metadata_extra={},
        )
        session.add(sample_row)
        session.commit()

        logger.info(
            "mine.sample_complete",
            document_id=str(document_id),
            total_facts=total,
            recovered_facts=recovered_count,
            retention_score=round(retention_score, 4),
        )

        return {
            "id": sample_row.id,
            "retention_score": sample_row.retention_score,
            "total_facts": total,
            "recovered_facts": recovered_count,
            "judgments": judgments,
            "cached": False,
        }

    async def _build_graph_context(
        self,
        arcade_client: ArcadeClient,
        seed_ids: list[str],
    ) -> str:
        """Fetch neighborhoods for seed entities and serialize to text."""
        if not seed_ids:
            return "No matching entities found in the knowledge graph."

        # Fetch and merge neighborhoods
        all_entities: dict[str, dict] = {}  # grace_id -> entity dict
        all_edges: list[dict] = []
        seen_edge_keys: set[tuple] = set()

        for gid in seed_ids[:self._max_seeds_per_fact]:
            try:
                neighborhood = await fetch_entity_neighborhood(
                    arcade_client, gid, max_depth=2
                )
                # Merge seed
                seed = neighborhood.get("seed", {})
                if seed and seed.get("grace_id"):
                    all_entities[seed["grace_id"]] = seed
                # Merge neighbors
                for n in neighborhood.get("neighbors", []):
                    ngid = n.get("grace_id", "")
                    if ngid:
                        all_entities[ngid] = n
                # Merge edges (dedup)
                for edge in neighborhood.get("edges", []):
                    ekey = (
                        edge.get("source_grace_id", ""),
                        edge.get("target_grace_id", ""),
                        edge.get("relationship_type", ""),
                    )
                    if ekey not in seen_edge_keys:
                        seen_edge_keys.add(ekey)
                        all_edges.append(edge)
            except Exception:
                logger.warning(
                    "mine.neighborhood_fetch_failed",
                    grace_id=gid,
                    exc_info=True,
                )

        # Prefer an explicit resolved seed (first seed_ids still in graph) for
        # template ordering; avoids arbitrary dict iteration order.
        primary_gid = next((s for s in seed_ids if s in all_entities), None)
        if primary_gid:
            seed_entity = all_entities[primary_gid]
            neighbor_entities = [
                all_entities[gid]
                for gid in all_entities
                if gid != primary_gid
            ]
        else:
            seed_entity = next(iter(all_entities.values()), {})
            neighbor_entities = list(all_entities.values())[1:]

        merged_neighborhood = {
            "seed": seed_entity,
            "neighbors": neighbor_entities,
            "edges": all_edges,
        }
        ranked_results, relationships = neighborhood_to_serializer_inputs(
            merged_neighborhood
        )

        # Serialize via TemplateSerializer
        if not ranked_results:
            return "No matching entities found in the knowledge graph."
        # F-0048 / ISS-0039: explicit budget (see _GRAPH_CONTEXT_TOKEN_BUDGET)
        # so entity property lines + edges both fit for multi-seed merges.
        return self._serializer.serialize(
            ranked_results,
            relationships,
            token_budget=_GRAPH_CONTEXT_TOKEN_BUDGET,
        )

    def _store_empty_result(
        self,
        session: Session,
        document_id: UUID,
        source_text: str,
        client: ExtractionLLMClient,
    ) -> dict:
        """Store a zero-fact result. Commits the session."""
        text_hash = hashlib.sha256(source_text.encode()).hexdigest()
        row = MineSampleRow(
            id=uuid4(),
            document_id=document_id,
            source_text_hash=text_hash,
            source_facts=[],
            judgments=[],
            total_facts=0,
            recovered_facts=0,
            retention_score=0.0,
            extraction_model=client.extraction_model,
            judge_model=client.verification_model,
            schema_version_id=self._schema_version_id,
            sampled_at=datetime.now(UTC),
            metadata_extra={},
        )
        session.add(row)
        session.commit()

        return {
            "id": row.id,
            "retention_score": 0.0,
            "total_facts": 0,
            "recovered_facts": 0,
            "judgments": [],
            "cached": False,
        }
