"""Four-input schema merge pipeline: 3 extraction passes + seed reference."""

import asyncio
import time
from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from src.discovery.cq_database import list_cqs
from src.discovery.cq_models import CQStatus, CompetencyQuestion
from src.discovery.merge_embeddings import compute_similarity_matrix, embed_texts
from src.discovery.schema_extractor import _normalize_type_name
from src.discovery.models import load_discovery_config
from src.discovery.ollama_client import _parse_json_robust
from src.discovery.schema_extractor import get_schema_run
from src.discovery.schema_merge_models import (
    CQCoverageEntry,
    MergedEntityType,
    MergedProperty,
    MergedRelationship,
    SchemaMergeRun,
    SeedSchema,
)
from src.discovery.schema_merge_prompts import (
    build_edge_detection_prompt,
    build_entity_canonicalization_prompt,
)
from src.discovery.schema_models import SchemaExtractionRun
from src.discovery.seed_models import SeedReference
from src.discovery.seed_parser import format_for_llm
from src.discovery.seed_provisioner import parse_and_cache_seeds
from src.discovery.seed_registry import resolve_sources_for_industry
from src.shared.database import get_db
from src.shared.llm_provider import get_provider

logger = structlog.get_logger()

# --- In-memory storage ---
_schema_merge_runs: dict[str, SchemaMergeRun] = {}


# --- Provenance computation ---


def compute_provenance(source_passes: list[str]) -> str:
    """Compute provenance tag from the list of source passes.

    Seven values: seed+3pass, seed+2pass, seed+1pass, seed_only,
    3pass_novel, 2pass_novel, 1pass_only.
    """
    has_seed = "seed" in source_passes
    pass_count = len([p for p in source_passes if p != "seed"])
    if has_seed and pass_count >= 3:
        return "seed+3pass"
    if has_seed and pass_count == 2:
        return "seed+2pass"
    if has_seed and pass_count == 1:
        return "seed+1pass"
    if has_seed and pass_count == 0:
        return "seed_only"
    if pass_count >= 3:
        return "3pass_novel"
    if pass_count == 2:
        return "2pass_novel"
    return "1pass_only"


def compute_confidence(source_passes: list[str]) -> float:
    """Compute confidence score from agreement level.

    high=1.0 (3pass), medium=0.67 (2pass), low=0.33 (1pass); +0.1 if seed-aligned.
    """
    has_seed = "seed" in source_passes
    pass_count = len([p for p in source_passes if p != "seed"])
    if pass_count >= 3:
        base = 1.0
    elif pass_count == 2:
        base = 0.67
    else:
        base = 0.33
    if has_seed:
        base = min(base + 0.1, 1.0)
    return round(base, 2)


# --- Stage A: Collect & Flatten ---


def collect_schema_elements(
    extraction_run: SchemaExtractionRun,
    seed_ref: SeedReference | None = None,
) -> tuple[list[dict], list[dict]]:
    """Gather all entity types and relationships from pass outputs + seed.

    Returns: (all_entity_type_dicts, all_relationship_dicts)
    Each dict includes source_pass tag and all original fields.
    """
    entity_dicts: list[dict] = []
    rel_dicts: list[dict] = []

    # From extraction pass outputs
    for po in extraction_run.pass_outputs:
        if not po.success:
            continue
        for et in po.entity_types:
            d = et.model_dump()
            d["source_pass"] = po.pass_name
            entity_dicts.append(d)
        for rel in po.relationships:
            d = rel.model_dump()
            d["source_pass"] = po.pass_name
            rel_dicts.append(d)

    # From seed reference (as fourth input)
    if seed_ref is not None:
        for set_ in seed_ref.entity_types:
            entity_dicts.append({
                "name": set_.name,
                "source_pass": "seed",
                "parent_type": set_.parent_type,
                "description": set_.description,
                "domain": "other",
                "properties": [
                    {"name": p.name, "data_type": p.range_type, "description": p.description}
                    for p in set_.properties
                ],
                "answerable_cqs": [],
                "evidence_documents": [],
                "seed_alignment": set_.name,
                "source_ontology": set_.source_ontology,
            })
        for sr in seed_ref.relationships:
            rel_dicts.append({
                "name": sr.name,
                "source_pass": "seed",
                "source_type": sr.domain_type,
                "target_type": sr.range_type,
                "description": sr.description,
                "richness_hint": "simple",
                "edge_properties": [],
                "answerable_cqs": [],
                "evidence_documents": [],
                "seed_alignment": sr.name,
                "source_ontology": sr.source_ontology,
            })

    logger.info(
        "stage_a_complete",
        entity_types=len(entity_dicts),
        relationships=len(rel_dicts),
    )
    return entity_dicts, rel_dicts


# --- Stage B: Cluster & Score ---


async def _two_tier_cluster(
    items: list[dict],
    threshold: float,
) -> dict[int, list[dict]]:
    """Two-tier hybrid clustering: normalized string match then enriched embedding.

    Tier 1: Group by normalized name (fast, high-precision).
    Tier 2: Merge remaining groups if enriched embedding similarity >= threshold.

    Returns: mapping from cluster label to list of member dicts.
    """
    if not items:
        return {}

    # --- Tier 1: Normalized string matching ---
    norm_groups: dict[str, list[int]] = {}
    for i, d in enumerate(items):
        key = _normalize_type_name(d["name"])
        norm_groups.setdefault(key, []).append(i)

    tier1_groups: list[list[dict]] = []
    for indices in norm_groups.values():
        tier1_groups.append([items[i] for i in indices])

    # --- Tier 2: Enriched embedding similarity ---
    if len(tier1_groups) <= 1:
        return {i: group for i, group in enumerate(tier1_groups)}

    enriched_texts = [
        f"clustering: {group[0]['name']} — {group[0].get('description', '')}"
        for group in tier1_groups
    ]

    try:
        embeddings = await embed_texts(enriched_texts)
        sim_matrix = compute_similarity_matrix(embeddings)
    except Exception:
        logger.warning("tier2_embedding_failed", message="Skipping Tier 2, returning Tier 1 results")
        return {i: group for i, group in enumerate(tier1_groups)}

    # Merge groups that exceed threshold
    used: set[int] = set()
    final_groups: dict[int, list[dict]] = {}
    label = 0

    for i, group_i in enumerate(tier1_groups):
        if i in used:
            continue
        used.add(i)
        merged = list(group_i)

        for j in range(i + 1, len(tier1_groups)):
            if j in used:
                continue
            if float(sim_matrix[i][j]) >= threshold:
                used.add(j)
                merged.extend(tier1_groups[j])

        final_groups[label] = merged
        label += 1

    return final_groups


async def cluster_schema_elements(
    entity_dicts: list[dict],
    rel_dicts: list[dict],
    config: dict,
) -> tuple[dict[int, list[dict]], dict[int, list[dict]]]:
    """Two-tier hybrid clustering for entity types and relationships.

    Tier 1: Normalized string match (catches exact/near-exact name duplicates).
    Tier 2: Enriched embedding similarity on remaining groups.

    Returns: (entity_clusters, rel_clusters)
    Each is a mapping from cluster_label to list of member dicts.
    """
    threshold = config.get("dedup_threshold", 0.85)

    # --- Entity type clustering ---
    entity_clusters = await _two_tier_cluster(entity_dicts, threshold)

    # --- Relationship clustering ---
    # Group by (source_type, target_type) first, then two-tier within groups
    rel_clusters: dict[int, list[dict]] = {}
    if rel_dicts:
        endpoint_groups: dict[tuple[str, str], list[dict]] = {}
        for rd in rel_dicts:
            key = (rd.get("source_type", ""), rd.get("target_type", ""))
            endpoint_groups.setdefault(key, []).append(rd)

        cluster_offset = 0
        for _endpoints, group in endpoint_groups.items():
            sub_clusters = await _two_tier_cluster(group, threshold)
            for _sub_label, members in sub_clusters.items():
                rel_clusters[cluster_offset] = members
                cluster_offset += 1

    logger.info(
        "stage_b_complete",
        entity_clusters=len(entity_clusters),
        rel_clusters=len(rel_clusters),
    )
    return entity_clusters, rel_clusters


async def _cluster_relationships(
    rel_dicts: list[dict],
    threshold: float,
) -> dict[int, list[dict]]:
    """Cluster relationships: group by endpoint pair, then two-tier within groups.

    Returns: mapping from cluster label to list of member dicts.
    """
    rel_clusters: dict[int, list[dict]] = {}
    if not rel_dicts:
        return rel_clusters

    endpoint_groups: dict[tuple[str, str], list[dict]] = {}
    for rd in rel_dicts:
        key = (rd.get("source_type", ""), rd.get("target_type", ""))
        endpoint_groups.setdefault(key, []).append(rd)

    cluster_offset = 0
    for _endpoints, group in endpoint_groups.items():
        sub_clusters = await _two_tier_cluster(group, threshold)
        for _sub_label, members in sub_clusters.items():
            rel_clusters[cluster_offset] = members
            cluster_offset += 1

    return rel_clusters


def _build_entity_name_mapping(
    entity_clusters: dict[int, list[dict]],
    resolved_types: list[MergedEntityType],
) -> dict[str, str]:
    """Build raw_name → canonical_name mapping from entity clustering + canonicalization.

    For each cluster, all member names map to the resolved canonical name.
    Names not in any cluster map to themselves (identity mapping).
    """
    mapping: dict[str, str] = {}
    for _label, members in entity_clusters.items():
        canonical = None
        member_names = {m["name"] for m in members}
        for rt in resolved_types:
            if rt.name in member_names or any(
                alt in member_names for alt in rt.alternative_names
            ):
                canonical = rt.name
                break
        if canonical is None:
            canonical = members[0]["name"]
        for m in members:
            mapping[m["name"]] = canonical
    return mapping


def _remap_relationship_endpoints(
    rel_dicts: list[dict],
    name_mapping: dict[str, str],
) -> list[dict]:
    """Replace source_type and target_type with canonical entity type names."""
    for rd in rel_dicts:
        rd["source_type"] = name_mapping.get(rd.get("source_type", ""), rd.get("source_type", ""))
        rd["target_type"] = name_mapping.get(rd.get("target_type", ""), rd.get("target_type", ""))
    return rel_dicts


def _merge_entity_cluster_simple(label: int, members: list[dict]) -> MergedEntityType:
    """Merge an entity cluster without LLM (for dry-run or fallback)."""
    source_passes = list({m["source_pass"] for m in members})
    all_names = list({m["name"] for m in members})
    # Pick the first name as canonical (seed-aligned preferred)
    canonical = all_names[0]
    for m in members:
        if m.get("source_pass") == "seed":
            canonical = m["name"]
            break

    # Union properties
    props_seen: dict[str, dict] = {}
    for m in members:
        for p in m.get("properties", []):
            pname = p.get("name", "") if isinstance(p, dict) else getattr(p, "name", "")
            if pname and pname not in props_seen:
                props_seen[pname] = p

    merged_props = []
    for pname, p in props_seen.items():
        if isinstance(p, dict):
            merged_props.append(MergedProperty(
                name=pname,
                data_type=p.get("data_type", "string"),
                description=p.get("description", ""),
                required=p.get("required", False),
                answerable_cqs=p.get("answerable_cqs", []),
                source_passes=[mm["source_pass"] for mm in members if any(
                    (pp.get("name") if isinstance(pp, dict) else getattr(pp, "name", "")) == pname
                    for pp in mm.get("properties", [])
                )],
            ))
        else:
            merged_props.append(MergedProperty(
                name=pname,
                data_type=getattr(p, "data_type", "string"),
                description=getattr(p, "description", ""),
                required=getattr(p, "required", False),
                source_passes=source_passes,
            ))

    # Union CQs and evidence docs
    all_cqs: set[str] = set()
    all_evidence: set[str] = set()
    for m in members:
        all_cqs.update(m.get("answerable_cqs", []))
        all_evidence.update(m.get("evidence_documents", []))

    # Seed info
    seed_source = None
    seed_type_name = None
    for m in members:
        if m.get("source_pass") == "seed":
            seed_source = m.get("source_ontology")
            seed_type_name = m.get("name")
            break
        if m.get("seed_alignment"):
            seed_type_name = m.get("seed_alignment")

    # Best description (longest)
    descriptions = [m.get("description", "") for m in members if m.get("description")]
    best_desc = max(descriptions, key=len) if descriptions else canonical

    # Plain-English presentation fields (first non-empty across passes)
    labels = [m.get("display_label", "") for m in members if m.get("display_label")]
    best_label = labels[0] if labels else ""
    plain_descs = [m.get("plain_description", "") for m in members if m.get("plain_description")]
    best_plain = max(plain_descs, key=len) if plain_descs else ""
    snippets = [m.get("example_snippet") for m in members if m.get("example_snippet")]
    example_snippet = snippets[0] if snippets else None

    # Parent type (most common non-None)
    parents = [m.get("parent_type") for m in members if m.get("parent_type")]
    parent_type = max(set(parents), key=parents.count) if parents else None

    # Domain (most common non-"other")
    domains = [m.get("domain", "other") for m in members]
    non_other = [d for d in domains if d != "other"]
    domain = max(set(non_other), key=non_other.count) if non_other else "other"

    return MergedEntityType(
        name=canonical,
        alternative_names=[n for n in all_names if n != canonical],
        parent_type=parent_type,
        description=best_desc,
        display_label=best_label,
        plain_description=best_plain,
        example_snippet=example_snippet,
        domain=domain,
        properties=merged_props,
        provenance=compute_provenance(source_passes),
        confidence=compute_confidence(source_passes),
        source_passes=source_passes,
        seed_source=seed_source,
        seed_type_name=seed_type_name,
        answerable_cqs=sorted(all_cqs),
        evidence_document_count=len(all_evidence),
    )


def _merge_rel_cluster_simple(label: int, members: list[dict]) -> MergedRelationship:
    """Merge a relationship cluster without LLM."""
    source_passes = list({m["source_pass"] for m in members})
    all_names = list({m["name"] for m in members})
    canonical = all_names[0]
    for m in members:
        if m.get("source_pass") == "seed":
            canonical = m["name"]
            break

    # Determine richness from hints
    hints = [m.get("richness_hint", "simple") for m in members]
    # Most common hint
    richness = max(set(hints), key=hints.count) if hints else "simple"

    # Union edge properties
    edge_props_seen: dict[str, dict] = {}
    for m in members:
        for p in m.get("edge_properties", []):
            pname = p.get("name", "") if isinstance(p, dict) else getattr(p, "name", "")
            if pname and pname not in edge_props_seen:
                edge_props_seen[pname] = p

    merged_edge_props = []
    for pname, p in edge_props_seen.items():
        if isinstance(p, dict):
            merged_edge_props.append(MergedProperty(
                name=pname,
                data_type=p.get("data_type", "string"),
                description=p.get("description", ""),
                source_passes=source_passes,
            ))

    all_cqs: set[str] = set()
    for m in members:
        all_cqs.update(m.get("answerable_cqs", []))

    seed_source = None
    seed_rel_name = None
    for m in members:
        if m.get("source_pass") == "seed":
            seed_source = m.get("source_ontology")
            seed_rel_name = m.get("name")
            break

    descriptions = [m.get("description", "") for m in members if m.get("description")]
    best_desc = max(descriptions, key=len) if descriptions else canonical

    labels = [m.get("display_label", "") for m in members if m.get("display_label")]
    best_label = labels[0] if labels else ""
    plain_descs = [m.get("plain_description", "") for m in members if m.get("plain_description")]
    best_plain = max(plain_descs, key=len) if plain_descs else ""
    snippets = [m.get("example_snippet") for m in members if m.get("example_snippet")]
    example_snippet = snippets[0] if snippets else None

    return MergedRelationship(
        name=canonical,
        alternative_names=[n for n in all_names if n != canonical],
        source_type=members[0].get("source_type", ""),
        target_type=members[0].get("target_type", ""),
        description=best_desc,
        display_label=best_label,
        plain_description=best_plain,
        example_snippet=example_snippet,
        richness_tier=richness,
        richness_rationale=f"Based on {len(members)} pass(es), most common hint: {richness}",
        edge_properties=merged_edge_props,
        provenance=compute_provenance(source_passes),
        confidence=compute_confidence(source_passes),
        source_passes=source_passes,
        seed_source=seed_source,
        seed_rel_name=seed_rel_name,
        answerable_cqs=sorted(all_cqs),
    )


# --- Stage C: LLM Judgment ---


async def run_entity_canonicalization(
    entity_clusters: dict[int, list[dict]],
    provider,
    merged_types: list[MergedEntityType],
) -> list[MergedEntityType]:
    """LLM Call 1: Canonical naming + hierarchy resolution."""
    if not entity_clusters:
        return merged_types

    system_prompt, user_prompt = build_entity_canonicalization_prompt(entity_clusters)

    try:
        response = await provider.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            json_mode=True,
        )
        parsed = _parse_json_robust(response.text)
        if parsed and isinstance(parsed, dict):
            resolved = parsed.get("resolved_types", [])
            # Build lookup by cluster_label
            resolved_by_label: dict[int, dict] = {}
            for r in resolved:
                if isinstance(r, dict):
                    resolved_by_label[r.get("cluster_label", -999)] = r

            # Apply LLM resolutions to merged types
            label_to_type: dict[int, MergedEntityType] = {}
            for label, mt in zip(sorted(entity_clusters.keys()), merged_types):
                label_to_type[label] = mt

            for label, resolution in resolved_by_label.items():
                if label in label_to_type:
                    mt = label_to_type[label]
                    if resolution.get("canonical_name"):
                        if mt.name != resolution["canonical_name"]:
                            if mt.name not in mt.alternative_names:
                                mt.alternative_names.append(mt.name)
                            mt.name = resolution["canonical_name"]
                    if "parent_type" in resolution:
                        mt.parent_type = resolution.get("parent_type")
                    if resolution.get("description"):
                        mt.description = resolution["description"]

        logger.info("entity_canonicalization_complete", resolved=len(merged_types))
    except Exception as e:
        logger.warning("entity_canonicalization_failed", error=str(e))

    return merged_types


def _build_entity_context(entity_clusters: dict[int, list[dict]]) -> dict[str, dict]:
    """Build entity name → {description, properties} lookup for Stage D context.

    When multiple entries exist for the same name, picks the one with the most properties.
    """
    context: dict[str, dict] = {}
    for members in entity_clusters.values():
        for m in members:
            name = m.get("name", "")
            if not name:
                continue
            props = m.get("properties", [])
            existing = context.get(name)
            if existing is None or len(props) > len(existing.get("properties", [])):
                context[name] = {
                    "description": m.get("description", ""),
                    "properties": props,
                }
    return context


async def run_edge_detection(
    rel_clusters: dict[int, list[dict]],
    provider,
    merged_rels: list[MergedRelationship],
    entity_clusters: dict[int, list[dict]] | None = None,
    batch_size: int = 12,
    concurrency: int = 5,
) -> list[MergedRelationship]:
    """LLM Call 2: Stage D Edge Property Detection with batching.

    Batches relationships into groups of batch_size per LLM call to avoid
    overwhelming the model. Includes entity type context for better classification.
    """
    if not rel_clusters:
        return merged_rels

    # Build entity context lookup for source/target type properties
    entity_context = _build_entity_context(entity_clusters) if entity_clusters else None

    # Build label → MergedRelationship lookup
    label_to_rel: dict[int, MergedRelationship] = {}
    for label, mr in zip(sorted(rel_clusters.keys()), merged_rels):
        label_to_rel[label] = mr

    # Split rel_clusters into batches of batch_size
    sorted_labels = sorted(rel_clusters.keys())
    batches: list[dict[int, list[dict]]] = []
    for i in range(0, len(sorted_labels), batch_size):
        batch_labels = sorted_labels[i : i + batch_size]
        batches.append({label: rel_clusters[label] for label in batch_labels})

    # Process batches concurrently with semaphore
    semaphore = asyncio.Semaphore(concurrency)
    all_classifications: dict[int, dict] = {}

    async def _classify_batch(batch: dict[int, list[dict]]) -> dict[int, dict]:
        async with semaphore:
            system_prompt, user_prompt = build_edge_detection_prompt(
                batch, entity_context=entity_context,
            )
            try:
                response = await provider.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.0,
                    json_mode=True,
                )
                parsed = _parse_json_robust(response.text)
                result: dict[int, dict] = {}
                if parsed and isinstance(parsed, dict):
                    for c in parsed.get("classifications", []):
                        if isinstance(c, dict):
                            result[c.get("cluster_label", -999)] = c
                return result
            except Exception as e:
                logger.warning("edge_detection_batch_failed", error=str(e))
                return {}

    try:
        tasks = [_classify_batch(batch) for batch in batches]
        results = await asyncio.gather(*tasks)

        for batch_result in results:
            all_classifications.update(batch_result)

        # Apply classifications to merged rels
        for label, cls in all_classifications.items():
            if label in label_to_rel:
                mr = label_to_rel[label]
                if cls.get("richness_tier"):
                    mr.richness_tier = cls["richness_tier"]
                if cls.get("richness_rationale"):
                    mr.richness_rationale = cls["richness_rationale"]
                if cls.get("canonical_name"):
                    if mr.name != cls["canonical_name"]:
                        if mr.name not in mr.alternative_names:
                            mr.alternative_names.append(mr.name)
                        mr.name = cls["canonical_name"]
                # Apply edge properties from LLM
                if cls.get("edge_properties"):
                    for ep in cls["edge_properties"]:
                        if isinstance(ep, dict) and ep.get("name"):
                            existing_names = {p.name for p in mr.edge_properties}
                            if ep["name"] not in existing_names:
                                mr.edge_properties.append(MergedProperty(
                                    name=ep["name"],
                                    data_type=ep.get("data_type", "string"),
                                    description=ep.get("description", ""),
                                    source_passes=["llm_stage_d"],
                                ))

        logger.info(
            "edge_detection_complete",
            classified=len(merged_rels),
            batches=len(batches),
        )
    except Exception as e:
        logger.warning("edge_detection_failed", error=str(e))

    return merged_rels


# --- Stage D: CQ Coverage Matrix ---


def build_coverage_matrix(
    merged_types: list[MergedEntityType],
    merged_rels: list[MergedRelationship],
    cqs: list[CompetencyQuestion],
) -> list[CQCoverageEntry]:
    """Map every CQ to the merged types/relationships that address it."""
    entries = []
    for cq in cqs:
        short_id = str(cq.id)[:8]
        covered_types = [
            mt.name for mt in merged_types if short_id in mt.answerable_cqs
        ]
        covered_rels = [
            mr.name for mr in merged_rels if short_id in mr.answerable_cqs
        ]
        if covered_types and covered_rels:
            status = "covered"
        elif covered_types or covered_rels:
            status = "partial"
        else:
            status = "uncovered"

        entries.append(CQCoverageEntry(
            cq_id=short_id,
            cq_text=cq.canonical_text,
            domain=cq.domain or "other",
            covered_by_types=covered_types,
            covered_by_relationships=covered_rels,
            coverage_status=status,
        ))
    return entries


# --- Stage E: Assembly ---


def assemble_seed_schema(
    merged_types: list[MergedEntityType],
    merged_rels: list[MergedRelationship],
    coverage_matrix: list[CQCoverageEntry],
    extraction_run_id: str,
    industry_profile: str,
) -> SeedSchema:
    """Build the final SeedSchema from merged results."""
    # Provenance summary
    prov_summary: dict[str, int] = {}
    for mt in merged_types:
        prov_summary[mt.provenance] = prov_summary.get(mt.provenance, 0) + 1
    for mr in merged_rels:
        prov_summary[mr.provenance] = prov_summary.get(mr.provenance, 0) + 1

    # Richness distribution
    richness_dist: dict[str, int] = {"simple": 0, "attributed": 0, "reified": 0}
    for mr in merged_rels:
        tier = mr.richness_tier
        if tier in richness_dist:
            richness_dist[tier] += 1

    # CQ coverage rate
    total_cqs = len(coverage_matrix)
    covered_count = sum(
        1 for e in coverage_matrix if e.coverage_status in ("covered", "partial")
    )
    cq_coverage_rate = covered_count / total_cqs if total_cqs > 0 else 0.0

    # Cross-pass agreement rate
    multi_pass_types = sum(
        1 for mt in merged_types
        if any(x in mt.provenance for x in ("2pass", "3pass"))
    )
    agreement_rate = (
        multi_pass_types / len(merged_types) if merged_types else 0.0
    )

    # Orphans
    orphan_types = [mt.name for mt in merged_types if not mt.answerable_cqs]
    orphan_rels = [mr.name for mr in merged_rels if not mr.answerable_cqs]
    uncovered_cqs = [
        e.cq_id for e in coverage_matrix if e.coverage_status == "uncovered"
    ]

    quality_metrics = {
        "total_entity_types": len(merged_types),
        "total_relationships": len(merged_rels),
        "cq_coverage_rate": round(cq_coverage_rate, 3),
        "cross_pass_agreement_rate": round(agreement_rate, 3),
        "orphan_type_count": len(orphan_types),
        "orphan_relationship_count": len(orphan_rels),
        "richness_distribution": richness_dist,
    }

    gap_report = {
        "uncovered_cqs": uncovered_cqs,
        "orphan_types": orphan_types,
        "orphan_relationships": orphan_rels,
    }

    return SeedSchema(
        entity_types=merged_types,
        relationships=merged_rels,
        coverage_matrix=coverage_matrix,
        provenance_summary=prov_summary,
        quality_metrics=quality_metrics,
        gap_report=gap_report,
        extraction_run_id=extraction_run_id,
        industry_profile=industry_profile,
    )


# --- Pipeline Orchestrator ---


def _load_seed_reference() -> tuple[SeedReference | None, str]:
    """Load seed reference if an industry profile is configured.

    Returns (seed_ref_or_none, industry_profile_str).
    """
    discovery_config = load_discovery_config()
    seed_config = discovery_config.get("seed", {})
    industry_profile = seed_config.get("industry_profile", "")

    if not industry_profile:
        return None, ""

    sources = resolve_sources_for_industry(industry_profile)
    if not sources:
        return None, industry_profile

    try:
        seed_ref = parse_and_cache_seeds(
            sources, discovery_config, industry_id=industry_profile
        )
        if seed_ref.total_entity_types == 0:
            return None, industry_profile
        return seed_ref, industry_profile
    except Exception as e:
        logger.warning("seed_reference_load_failed", error=str(e))
        return None, industry_profile


async def run_schema_merge(
    extraction_run_id: str | None = None,
    db: Session | None = None,
    dry_run: bool = False,
    run_id: str | None = None,
) -> SchemaMergeRun:
    """Run the full four-input schema merge pipeline.

    Steps:
    1. Load SchemaExtractionRun
    2. Load seed reference
    3. Load CQs
    4. Stage A: Collect & flatten
    5. Stage B: Cluster via HDBSCAN
    6. Stage C: LLM judgment (skip if dry_run)
    7. Stage D: CQ coverage matrix
    8. Stage E: Assemble SeedSchema
    """
    start_time = time.time()

    provider = get_provider()
    try:
        provider_model = str(
            getattr(provider, "model", "")
            or getattr(getattr(provider, "config", None), "model", "")
            or ""
        )
    except Exception:
        provider_model = ""

    # Reuse the caller-supplied run (the one the API/frontend polls) so the
    # polled run_id IS the run that does the work — otherwise the route's
    # placeholder merge run never completes and the UI times out even though the
    # merge succeeded (same fix as schema extraction's run_id threading).
    if run_id is not None and run_id in _schema_merge_runs:
        run = _schema_merge_runs[run_id]
        run.model = provider_model
        run.provider = provider.provider_name
    else:
        run = SchemaMergeRun(model=provider_model, provider=provider.provider_name)

    close_db = False
    if db is None:
        db_gen = get_db()
        db = next(db_gen)
        close_db = True

    try:
        # Find extraction run (in-memory first, then DB)
        extraction_run: SchemaExtractionRun | None = None
        if extraction_run_id:
            extraction_run = get_schema_run(extraction_run_id)
            # Fall back to DB if not in memory
            if extraction_run is None:
                try:
                    from src.discovery.schema_extractor import load_extraction_run_from_db
                    extraction_run = load_extraction_run_from_db(db, extraction_run_id)
                except Exception as e:
                    logger.warning("extraction_run_db_load_failed", error=str(e))
        else:
            # Find the most recent completed run (in-memory first)
            from src.discovery.schema_extractor import _schema_runs
            for rid in reversed(list(_schema_runs.keys())):
                r = _schema_runs[rid]
                if r.status == "completed":
                    extraction_run = r
                    break
            # Fall back to DB if not in memory
            if extraction_run is None:
                try:
                    from src.discovery.schema_extractor import get_latest_completed_extraction_run_from_db
                    extraction_run = get_latest_completed_extraction_run_from_db(db)
                except Exception as e:
                    logger.warning("extraction_run_db_load_failed", error=str(e))

        if extraction_run is None:
            run.status = "failed"
            run.error_message = "No completed schema extraction run found"
            run.completed_at = datetime.now(UTC)
            _schema_merge_runs[run.run_id] = run
            return run

        run.extraction_run_id = extraction_run.run_id

        # Load seed reference
        seed_ref, industry_profile = _load_seed_reference()
        if seed_ref:
            run.seed_types_count = seed_ref.total_entity_types

        # Load CQs
        all_cqs = list_cqs(db, status=CQStatus.ACCEPTED, limit=10000)
        if not all_cqs:
            all_cqs = list_cqs(db, status=CQStatus.DRAFT, limit=10000)
        run.input_cqs = len(all_cqs)

        # Stage A: Collect & Flatten
        entity_dicts, rel_dicts = collect_schema_elements(extraction_run, seed_ref)
        run.input_entity_types = len(entity_dicts)
        run.input_relationships = len(rel_dicts)

        if not entity_dicts and not rel_dicts:
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            _schema_merge_runs[run.run_id] = run
            return run

        merge_config = load_discovery_config().get("cq_merge", {})
        threshold = merge_config.get("dedup_threshold", 0.85)

        # Stage B1: Cluster entity types only (two-tier hybrid)
        entity_clusters = await _two_tier_cluster(entity_dicts, threshold)

        # Build initial merged types from entity clusters
        merged_types = [
            _merge_entity_cluster_simple(label, members)
            for label, members in sorted(entity_clusters.items())
        ]

        if dry_run:
            # Dry run: remap endpoints using simple merge names, cluster rels, skip LLM
            name_mapping = _build_entity_name_mapping(entity_clusters, merged_types)
            rel_dicts = _remap_relationship_endpoints(rel_dicts, name_mapping)
            rel_clusters = await _cluster_relationships(rel_dicts, threshold)
            merged_rels = [
                _merge_rel_cluster_simple(label, members)
                for label, members in sorted(rel_clusters.items())
            ]
            coverage_matrix = build_coverage_matrix(merged_types, merged_rels, all_cqs)
            seed_schema = assemble_seed_schema(
                merged_types, merged_rels, coverage_matrix,
                extraction_run.run_id, industry_profile,
            )
            run.merged_entity_types = len(merged_types)
            run.merged_relationships = len(merged_rels)
            run.cq_coverage_rate = seed_schema.quality_metrics.get("cq_coverage_rate", 0.0)
            run.cross_pass_agreement_rate = seed_schema.quality_metrics.get("cross_pass_agreement_rate", 0.0)
            run.provenance_distribution = seed_schema.provenance_summary
            run.richness_distribution = seed_schema.quality_metrics.get("richness_distribution", {})
            run.seed_schema_json = seed_schema.model_dump(mode="json")
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            run.duration_ms = int((time.time() - start_time) * 1000)
            _schema_merge_runs[run.run_id] = run
            return run

        # Check provider health
        health = await provider.health_check()
        if not health["healthy"]:
            raise RuntimeError(
                f"Provider not healthy: {health.get('details', 'unknown')}"
            )

        # Stage C1: Canonicalize entity type names via LLM
        merged_types = await run_entity_canonicalization(
            entity_clusters, provider, merged_types
        )

        # Stage B1.5: Remap relationship endpoints to canonical names
        name_mapping = _build_entity_name_mapping(entity_clusters, merged_types)
        rel_dicts = _remap_relationship_endpoints(rel_dicts, name_mapping)

        # Stage B2: Cluster relationships (now with canonical endpoints)
        rel_clusters = await _cluster_relationships(rel_dicts, threshold)

        merged_rels = [
            _merge_rel_cluster_simple(label, members)
            for label, members in sorted(rel_clusters.items())
        ]

        # Stage C2: Edge Property Detection (batched, with entity context)
        merged_rels = await run_edge_detection(
            rel_clusters, provider, merged_rels,
            entity_clusters=entity_clusters,
        )

        # Stage D: CQ Coverage Matrix
        coverage_matrix = build_coverage_matrix(merged_types, merged_rels, all_cqs)

        # Stage E: Assembly
        seed_schema = assemble_seed_schema(
            merged_types, merged_rels, coverage_matrix,
            extraction_run.run_id, industry_profile,
        )

        run.merged_entity_types = len(merged_types)
        run.merged_relationships = len(merged_rels)
        run.cq_coverage_rate = seed_schema.quality_metrics.get("cq_coverage_rate", 0.0)
        run.cross_pass_agreement_rate = seed_schema.quality_metrics.get("cross_pass_agreement_rate", 0.0)
        run.provenance_distribution = seed_schema.provenance_summary
        run.richness_distribution = seed_schema.quality_metrics.get("richness_distribution", {})
        run.seed_schema_json = seed_schema.model_dump(mode="json")
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.duration_ms = int((time.time() - start_time) * 1000)

        logger.info(
            "schema_merge_complete",
            entity_types=run.merged_entity_types,
            relationships=run.merged_relationships,
            cq_coverage=run.cq_coverage_rate,
            duration_ms=run.duration_ms,
        )

        # Store in DB if available
        try:
            _store_merge_run_db(db, run)
        except Exception as e:
            logger.warning("schema_merge_db_store_failed", error=str(e))

        _schema_merge_runs[run.run_id] = run
        return run

    except Exception as e:
        run.status = "failed"
        run.error_message = str(e)
        run.completed_at = datetime.now(UTC)
        run.duration_ms = int((time.time() - start_time) * 1000)
        logger.error("schema_merge_failed", error=str(e))
        _schema_merge_runs[run.run_id] = run
        return run

    finally:
        if close_db:
            try:
                next(db_gen)
            except StopIteration:
                pass


def _store_merge_run_db(db: Session, run: SchemaMergeRun) -> None:
    """Store a schema merge run in the database."""
    from src.discovery.cq_database import SchemaMergeRunRow
    row = SchemaMergeRunRow(
        id=run.run_id,
        extraction_run_id=run.extraction_run_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        status=run.status,
        model=run.model,
        provider=run.provider,
        input_entity_types=run.input_entity_types,
        input_relationships=run.input_relationships,
        input_cqs=run.input_cqs,
        seed_types_count=run.seed_types_count,
        merged_entity_types=run.merged_entity_types,
        merged_relationships=run.merged_relationships,
        cq_coverage_rate=run.cq_coverage_rate,
        cross_pass_agreement_rate=run.cross_pass_agreement_rate,
        provenance_distribution=run.provenance_distribution,
        richness_distribution=run.richness_distribution,
        seed_schema_json=run.seed_schema_json,
        duration_ms=run.duration_ms,
        error_message=run.error_message,
    )
    db.add(row)
    db.commit()


def get_schema_merge_run(run_id: str) -> SchemaMergeRun | None:
    """Retrieve a schema merge run by ID from in-memory storage."""
    return _schema_merge_runs.get(run_id)
