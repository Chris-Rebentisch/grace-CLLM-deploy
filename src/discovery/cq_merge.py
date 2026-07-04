"""CQ merge pipeline orchestrator: three-tier merge (embeddings, annotations, LLM calls).

Coordinates Tier 1 (HDBSCAN clustering), Tier 2 (deterministic annotations), and
Tier 3 (LLM calls for canonical phrasing, hierarchy, and gap analysis).
"""

import argparse
import asyncio
import time
from datetime import UTC, datetime

import structlog

from src.discovery.cq_database import (
    create_cluster,
    create_cq,
    bulk_create_cqs,
    list_cqs,
    update_cq,
)
from src.discovery.cq_models import (
    CQCluster,
    CQSource,
    CQStatus,
    CompetencyQuestion,
)
from src.discovery.cq_templates import load_templates
from src.discovery.merge_annotations import (
    classify_cq_types,
    classify_domain_embedding,
    compute_agreement,
    compute_cluster_quality,
    detect_coverage_gaps,
)
from src.discovery.merge_embeddings import embed_texts, run_tier1
from src.discovery.merge_llm_calls import (
    call1_canonical_phrasing,
    call2_hierarchy,
    call3_gap_analysis,
)
from src.discovery.merge_models import MergeRun
from src.discovery.models import get_valid_domains, load_discovery_config
from src.shared.llm_provider import get_provider, read_llm_config_from_yaml

logger = structlog.get_logger()

# In-memory merge run storage (until MergeRunRow is added to cq_database)
_merge_runs: dict[str, MergeRun] = {}


def get_merge_run(run_id: str) -> MergeRun | None:
    """Retrieve a merge run by its ID from in-memory storage."""
    return _merge_runs.get(run_id)


def create_merge_run_record(db, merge_run: MergeRun) -> None:
    """Persist a MergeRun to the database.

    Uses MergeRunRow if available in cq_database; otherwise logs and stores
    in-memory only.
    """
    try:
        from src.discovery.cq_database import MergeRunRow

        row = MergeRunRow(
            id=merge_run.run_id,
            started_at=merge_run.started_at,
            completed_at=merge_run.completed_at,
            status=merge_run.status,
            model=merge_run.model,
            provider=merge_run.provider,
            total_cqs_input=merge_run.total_cqs_input,
            total_clusters=merge_run.total_clusters,
            total_singletons=merge_run.total_singletons,
            total_gap_fills=merge_run.total_gap_fills,
            mean_cluster_size=merge_run.mean_cluster_size,
            mean_intra_similarity=merge_run.mean_intra_similarity,
            agreement_distribution=merge_run.agreement_distribution,
            quality_distribution=merge_run.quality_distribution,
            hierarchy_json=merge_run.hierarchy_json,
            gap_report_json=merge_run.gap_report_json,
            tier3_results_json=merge_run.tier3_results_json,
            duration_ms=merge_run.duration_ms,
            error_message=merge_run.error_message,
        )
        db.add(row)
        db.commit()
        logger.info("merge_run_persisted", run_id=merge_run.run_id)
    except ImportError:
        logger.info(
            "merge_run_stored_in_memory",
            run_id=merge_run.run_id,
            msg="MergeRunRow not yet available in cq_database; stored in-memory only",
        )
    except Exception as e:
        logger.error(
            "merge_run_persist_error",
            run_id=merge_run.run_id,
            error=str(e),
        )


async def run_merge_pipeline(db=None, dry_run: bool = False) -> MergeRun:
    """Execute the full three-tier CQ merge pipeline.

    Steps:
        1. Load all CQs with status DRAFT or ACCEPTED
        2. Load config from discovery.yaml cq_merge section
        3. Tier 1: embedding + HDBSCAN clustering
        4. Tier 2: agreement, domain classification, type classification, quality
        5. Coverage gap detection
        6. Create CQCluster records in DB
        7. Update CQ records with cluster_id and type classifications
        8. Set generation_confidence from agreement tier
        9. If dry_run: stop and return
        10. Tier 3 Call 1: canonical phrasing
        11. Tier 3 Call 2: hierarchy
        12. Tier 3 Call 3: gap analysis + paths
        13. Create gap-fill CQ records
        14. Store path annotations in CQ.metadata_extra
        15. Create/update merge_run record
        16. Return MergeRun

    Args:
        db: SQLAlchemy Session (optional; if None, DB operations are skipped).
        dry_run: If True, stop after Tier 2 (no LLM calls).

    Returns:
        MergeRun with full pipeline results.
    """
    start_time = time.monotonic()
    llm_config = read_llm_config_from_yaml()

    merge_run = MergeRun(
        model=llm_config.get("model", ""),
        provider=llm_config.get("provider", ""),
    )
    _merge_runs[merge_run.run_id] = merge_run

    try:
        # --- Step 1: Load CQs ---
        if db is not None:
            draft_cqs = list_cqs(db, status=CQStatus.DRAFT, limit=10000)
            accepted_cqs = list_cqs(db, status=CQStatus.ACCEPTED, limit=10000)
            all_cqs = draft_cqs + accepted_cqs
        else:
            all_cqs = []
            logger.warning("merge_no_db", msg="No database session; using empty CQ list")

        if len(all_cqs) < 2:
            merge_run.status = "completed"
            merge_run.completed_at = datetime.now(UTC)
            merge_run.total_cqs_input = len(all_cqs)
            merge_run.duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.warning("merge_insufficient_cqs", count=len(all_cqs))
            return merge_run

        merge_run.total_cqs_input = len(all_cqs)
        logger.info("merge_cqs_loaded", count=len(all_cqs))

        # --- Step 2: Load config ---
        discovery_config = load_discovery_config()
        cq_merge_config = discovery_config.get("cq_merge", {})
        configured_domains = get_valid_domains()

        # --- Step 3: Tier 1 ---
        tier1 = await run_tier1(all_cqs, cq_merge_config)
        merge_run.total_clusters = len(tier1.cluster_groups)
        merge_run.total_singletons = len(tier1.singleton_indices)

        if tier1.cluster_groups:
            sizes = [len(v) for v in tier1.cluster_groups.values()]
            merge_run.mean_cluster_size = sum(sizes) / len(sizes)

        # --- Step 4: Tier 2 annotations ---
        # Load templates and their embeddings for type classification
        templates = list(load_templates())
        template_texts = [t.example for t in templates]
        template_embeddings: list[list[float]] = []
        if template_texts:
            template_embeddings = await embed_texts(template_texts)

        agreement_dist: dict[str, int] = {}
        quality_dist: dict[str, int] = {}
        cluster_data_for_db: list[dict] = []
        intra_similarities: list[float] = []

        # Process each cluster
        for label, indices in tier1.cluster_groups.items():
            cluster_cqs = [all_cqs[i] for i in indices]
            cluster_embeddings = [tier1.embeddings[i] for i in indices]
            cluster_texts = [cq.canonical_text for cq in cluster_cqs]
            cluster_emb_list = [tier1.embeddings[i] for i in indices]

            # Agreement
            agreement_tier, source_passes = compute_agreement(cluster_cqs)
            agreement_dist[agreement_tier] = agreement_dist.get(agreement_tier, 0) + 1

            # Domain classification
            emb_domain, emb_domain_conf, domain_dist = classify_domain_embedding(
                cluster_embeddings, tier1.embeddings, all_cqs, cq_merge_config
            )

            # Type classification
            type_classifications = classify_cq_types(
                cluster_texts, cluster_emb_list, template_embeddings, templates
            )

            # Quality
            quality_info = compute_cluster_quality(
                indices, tier1.similarity_matrix, tier1.hdbscan_result, label, cq_merge_config
            )
            quality_dist[quality_info["quality"]] = quality_dist.get(quality_info["quality"], 0) + 1

            # Track intra-cluster similarity
            if quality_info["min_pairwise_similarity"] < 2.0:
                intra_similarities.append(quality_info["min_pairwise_similarity"])

            # Has human anchor?
            has_human = any(cq.source == CQSource.HUMAN_AUTHORED for cq in cluster_cqs)

            # CQ type distribution
            cq_type_dist: dict[str, int] = {}
            for tc in type_classifications:
                t = tc.embedding_cq_type
                cq_type_dist[t] = cq_type_dist.get(t, 0) + 1

            # Cross-domain check
            unique_domains = set(cq.domain for cq in cluster_cqs)
            cross_domain = len(unique_domains) > 1

            # Average similarity
            import numpy as np
            sim_arr = np.array(tier1.similarity_matrix)
            sub = sim_arr[np.ix_(indices, indices)]
            np.fill_diagonal(sub, 0.0)
            n = len(indices)
            avg_sim = float(np.sum(sub) / (n * (n - 1))) if n > 1 else 1.0

            cluster_data_for_db.append({
                "label": label,
                "indices": indices,
                "agreement_tier": agreement_tier,
                "source_passes": source_passes,
                "domain": emb_domain,
                "embedding_domain": emb_domain,
                "embedding_domain_confidence": emb_domain_conf,
                "domain_distribution": domain_dist,
                "quality": quality_info["quality"],
                "min_pairwise_similarity": quality_info["min_pairwise_similarity"],
                "max_membership_probability": quality_info["max_membership_probability"],
                "cluster_quality_score": quality_info["cluster_quality_score"],
                "has_human_anchor": has_human,
                "cross_domain": cross_domain,
                "cq_type_distribution": cq_type_dist,
                "similarity_score": avg_sim,
                "member_count": len(indices),
                "type_classifications": type_classifications,
            })

        # Process singletons
        singleton_type_classifications = {}
        if tier1.singleton_indices:
            singleton_texts = [all_cqs[i].canonical_text for i in tier1.singleton_indices]
            singleton_embs = [tier1.embeddings[i] for i in tier1.singleton_indices]
            singleton_types = classify_cq_types(
                singleton_texts, singleton_embs, template_embeddings, templates
            )
            for idx, tc in zip(tier1.singleton_indices, singleton_types):
                singleton_type_classifications[idx] = tc

        merge_run.agreement_distribution = agreement_dist
        merge_run.quality_distribution = quality_dist
        if intra_similarities:
            merge_run.mean_intra_similarity = sum(intra_similarities) / len(intra_similarities)

        # --- Step 5: Coverage gaps ---
        gap_report = detect_coverage_gaps(
            tier1.cluster_groups, tier1.singleton_indices, configured_domains, cq_merge_config
        )
        # Enrich gap report with actual domain/type counts
        domain_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        for cd in cluster_data_for_db:
            d = cd["domain"]
            domain_counts[d] = domain_counts.get(d, 0) + 1
            for t, c in cd["cq_type_distribution"].items():
                type_counts[t] = type_counts.get(t, 0) + c
        gap_report.domain_coverage = domain_counts
        gap_report.type_coverage = type_counts

        merge_run.gap_report_json = gap_report.model_dump()

        # --- Step 6: Create CQCluster records ---
        cluster_id_map: dict[int, CQCluster] = {}
        for cd in cluster_data_for_db:
            cluster = CQCluster(
                domain=cd["domain"],
                agreement_tier=cd["agreement_tier"],
                source_passes=cd["source_passes"],
                similarity_score=cd["similarity_score"],
                member_count=cd["member_count"],
                cluster_quality_score=cd["cluster_quality_score"],
                max_membership_probability=cd["max_membership_probability"],
                min_pairwise_similarity=cd["min_pairwise_similarity"],
                quality=cd["quality"],
                cross_domain=cd["cross_domain"],
                domain_distribution=cd["domain_distribution"],
                has_human_anchor=cd["has_human_anchor"],
                cq_type_distribution=cd["cq_type_distribution"],
                embedding_domain=cd["embedding_domain"],
                embedding_domain_confidence=cd["embedding_domain_confidence"],
            )
            if db is not None:
                cluster = create_cluster(db, cluster)
            cluster_id_map[cd["label"]] = cluster

        # --- Step 7: Update CQ records ---
        confidence_map = {
            "high": 1.0,
            "medium": 0.67,
            "low": 0.33,
            "singleton": 0.2,
            "gap_fill": 0.5,
        }

        for cd in cluster_data_for_db:
            cluster = cluster_id_map[cd["label"]]
            gen_conf = confidence_map.get(cd["agreement_tier"], 0.33)

            for i, cq_idx in enumerate(cd["indices"]):
                cq = all_cqs[cq_idx]
                tc = cd["type_classifications"][i]
                updates = {
                    "cluster_id": cluster.id,
                    "generation_confidence": gen_conf,
                    "embedding_cq_type": tc.embedding_cq_type,
                    "embedding_cq_type_confidence": tc.embedding_cq_type_confidence,
                    "rule_cq_type": tc.rule_cq_type,
                    "type_agreement": tc.type_agreement,
                }
                if db is not None:
                    update_cq(db, cq.id, updates)

        # Update singletons
        for idx in tier1.singleton_indices:
            cq = all_cqs[idx]
            tc = singleton_type_classifications.get(idx)
            updates: dict = {"generation_confidence": 0.2}
            if tc:
                updates["embedding_cq_type"] = tc.embedding_cq_type
                updates["embedding_cq_type_confidence"] = tc.embedding_cq_type_confidence
                updates["rule_cq_type"] = tc.rule_cq_type
                updates["type_agreement"] = tc.type_agreement
            if db is not None:
                update_cq(db, cq.id, updates)

        logger.info(
            "tier2_complete",
            clusters=len(cluster_data_for_db),
            singletons=len(tier1.singleton_indices),
        )

        # --- Step 9: Dry run exit ---
        if dry_run:
            merge_run.status = "completed"
            merge_run.completed_at = datetime.now(UTC)
            merge_run.duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info("merge_dry_run_complete", run_id=merge_run.run_id)
            return merge_run

        # --- Step 10: Tier 3 Call 1 ---
        clusters_for_llm = []
        for cd in cluster_data_for_db:
            members = []
            for cq_idx in cd["indices"]:
                cq = all_cqs[cq_idx]
                members.append({
                    "index": cq_idx,
                    "text": cq.canonical_text,
                    "source_pass": cq.source_pass or "",
                    "domain": cq.domain,
                })
            clusters_for_llm.append({
                "cluster_label": cd["label"],
                "members": members,
            })

        call1_result = await call1_canonical_phrasing(clusters_for_llm, cq_merge_config)

        # Update canonical CQ IDs from Call 1
        if call1_result:
            for item in call1_result.clusters:
                if item.cluster_label in cluster_id_map:
                    cluster = cluster_id_map[item.cluster_label]
                    cd = next(
                        (c for c in cluster_data_for_db if c["label"] == item.cluster_label),
                        None,
                    )
                    if cd and 0 <= item.canonical_index < len(cd["indices"]):
                        canonical_cq = all_cqs[cd["indices"][item.canonical_index]]
                        if db is not None:
                            from src.discovery.cq_database import update_cluster
                            update_cluster(db, cluster.id, {"canonical_cq_id": canonical_cq.id})

        # --- Step 11: Tier 3 Call 2 ---
        canonical_cqs_for_llm = []
        for cd in cluster_data_for_db:
            # Use the canonical text from Call 1 if available
            canonical_text = None
            if call1_result:
                match = next(
                    (c for c in call1_result.clusters if c.cluster_label == cd["label"]),
                    None,
                )
                if match:
                    canonical_text = match.canonical_text

            if canonical_text is None:
                canonical_text = all_cqs[cd["indices"][0]].canonical_text

            canonical_cqs_for_llm.append({
                "id": str(cluster_id_map[cd["label"]].id),
                "text": canonical_text,
                "domain": cd["domain"],
                "cq_type": list(cd["cq_type_distribution"].keys())[0] if cd["cq_type_distribution"] else "UNCLASSIFIED",
            })

        singletons_for_llm = []
        for idx in tier1.singleton_indices:
            cq = all_cqs[idx]
            tc = singleton_type_classifications.get(idx)
            singletons_for_llm.append({
                "id": str(cq.id),
                "text": cq.canonical_text,
                "domain": cq.domain,
                "cq_type": tc.embedding_cq_type if tc else "UNCLASSIFIED",
            })

        call2_result = await call2_hierarchy(
            canonical_cqs_for_llm, singletons_for_llm, cq_merge_config
        )

        hierarchy_dict = call2_result.model_dump() if call2_result else {}
        merge_run.hierarchy_json = hierarchy_dict

        # --- Step 12: Tier 3 Call 3 ---
        call3_result = await call3_gap_analysis(
            hierarchy_dict,
            gap_report.model_dump(),
            canonical_cqs_for_llm,
            singletons_for_llm,
            cq_merge_config,
        )

        # --- Step 13: Create gap-fill CQ records ---
        if call3_result and call3_result.gap_fill_cqs:
            gap_fill_models = []
            for gf in call3_result.gap_fill_cqs:
                # Validate domain
                domain = gf.domain if gf.domain in configured_domains else "other"
                gap_cq = CompetencyQuestion(
                    canonical_text=gf.canonical_text,
                    domain=domain,
                    source=CQSource.LLM_GAP_FILL,
                    source_pass="gap_fill",
                    generation_confidence=0.5,
                    metadata_extra={
                        "gap_addressed": gf.gap_addressed,
                        "rationale": gf.rationale,
                        "cq_type_proposed": gf.cq_type,
                    },
                )
                gap_fill_models.append(gap_cq)

            if db is not None and gap_fill_models:
                bulk_create_cqs(db, gap_fill_models)

            merge_run.total_gap_fills = len(gap_fill_models)
            logger.info("gap_fill_cqs_created", count=len(gap_fill_models))

        # --- Step 14: Store path annotations ---
        if call3_result and call3_result.path_annotations:
            for pa in call3_result.path_annotations:
                if db is not None:
                    # Try to find the CQ by ID and update metadata_extra
                    try:
                        from uuid import UUID
                        cq_uuid = UUID(pa.cq_id)
                        update_cq(db, cq_uuid, {
                            "metadata_extra": {
                                "expected_path": pa.expected_path,
                                "path_types": pa.path_types,
                                "path_properties": pa.path_properties,
                            },
                        })
                    except (ValueError, Exception) as e:
                        logger.warning(
                            "path_annotation_update_failed",
                            cq_id=pa.cq_id,
                            error=str(e),
                        )

        # --- Step 15: Finalize merge run ---
        tier3_results = {}
        if call1_result:
            tier3_results["call1"] = call1_result.model_dump()
        if call2_result:
            tier3_results["call2"] = call2_result.model_dump()
        if call3_result:
            tier3_results["call3"] = call3_result.model_dump()
        merge_run.tier3_results_json = tier3_results

        # --- Canonical review set ---
        # Collapse near-duplicate CQs into a coverage-ranked canonical set so human
        # review sees ~dozens of distinct competency questions, not thousands. Uses
        # deterministic Tier-2 types (not the generator's noisy labels) so schema_only
        # routes per-instance VALIDATING facts to extraction rather than review.
        # Guarded: a failure here must never break the merge. Full-run path only.
        try:
            from src.discovery.cq_relevance import collapse_and_rank

            all_types = classify_cq_types(
                [cq.canonical_text for cq in all_cqs],
                tier1.embeddings,
                template_embeddings,
                templates,
            )
            canon_input = [
                {
                    "question": cq.canonical_text,
                    "cq_type": (
                        tc.embedding_cq_type
                        if tc.embedding_cq_type != "UNCLASSIFIED"
                        else tc.rule_cq_type
                    ),
                }
                for cq, tc in zip(all_cqs, all_types)
            ]
            schema_only = cq_merge_config.get("canonical_schema_only", True)
            canonical = await collapse_and_rank(
                canon_input, embeddings=tier1.embeddings, schema_only=schema_only
            )
            merge_run.tier3_results_json = {
                **merge_run.tier3_results_json,
                "canonical_review_set": canonical,
            }
            merge_run.canonical_count = len(canonical)
            logger.info(
                "canonical_review_set_built",
                raw=len(all_cqs),
                canonical=len(canonical),
                schema_only=schema_only,
            )
        except Exception as e:  # noqa: BLE001 - review surface must not break the merge
            logger.warning("canonical_collapse_skipped", error=str(e))

        merge_run.status = "completed"
        merge_run.completed_at = datetime.now(UTC)
        merge_run.duration_ms = int((time.monotonic() - start_time) * 1000)

        if db is not None:
            create_merge_run_record(db, merge_run)

        _merge_runs[merge_run.run_id] = merge_run

        logger.info(
            "merge_pipeline_complete",
            run_id=merge_run.run_id,
            total_cqs=merge_run.total_cqs_input,
            clusters=merge_run.total_clusters,
            singletons=merge_run.total_singletons,
            gap_fills=merge_run.total_gap_fills,
            duration_ms=merge_run.duration_ms,
        )

    except Exception as e:
        merge_run.status = "failed"
        merge_run.error_message = str(e)
        merge_run.completed_at = datetime.now(UTC)
        merge_run.duration_ms = int((time.monotonic() - start_time) * 1000)
        _merge_runs[merge_run.run_id] = merge_run
        logger.error(
            "merge_pipeline_failed",
            run_id=merge_run.run_id,
            error=str(e),
            duration_ms=merge_run.duration_ms,
        )
        raise

    return merge_run


def main() -> None:
    """CLI entry point for the CQ merge pipeline."""
    parser = argparse.ArgumentParser(description="Run the CQ three-tier merge pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stop after Tier 2 (no LLM calls)",
    )
    args = parser.parse_args()

    # Set up database session
    try:
        from src.shared.database import get_session_factory
        session_factory = get_session_factory()
        db = session_factory()
    except Exception as e:
        logger.warning("merge_cli_no_db", error=str(e), msg="Running without database")
        db = None

    try:
        result = asyncio.run(run_merge_pipeline(db=db, dry_run=args.dry_run))
        logger.info(
            "merge_cli_complete",
            run_id=result.run_id,
            status=result.status,
            duration_ms=result.duration_ms,
        )
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    main()
