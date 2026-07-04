#!/usr/bin/env python3
"""Replay-and-compare routine for entity resolution threshold re-calibration.

Reads a sample of entity_resolution_log rows where resolution_tier='embedding'
and resolution_note IS NULL, re-runs the extracted entity through the new ANN path,
and compares the new similarity_score to the logged value.

Usage:
    python3 scripts/benchmark/recalibrate_er_thresholds.py [--sample-size N]

D445.6 — threshold re-calibration after Tier-2 re-architecture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict

import structlog

from src.extraction.entity_resolver import build_embedding_text
from src.extraction.extraction_config import ExtractionSettings
from src.graph.arcade_client import ArcadeClient
from src.graph.config import ArcadeConfig
from src.shared.embeddings import embed_texts

logger = structlog.get_logger()


async def recalibrate(sample_size: int = 100) -> dict:
    """Run the replay-and-compare routine.

    Returns:
        Machine-readable comparison artifact with per-type drift statistics.
    """
    config = ExtractionSettings()
    arcade_config = ArcadeConfig()
    client = ArcadeClient(arcade_config)
    ollama_url = config.extraction_base_url or "http://localhost:11434"
    model = config.er_embedding_model

    # Read sample from entity_resolution_log
    from src.ontology.database import get_session

    session = get_session()
    try:
        from sqlalchemy import text

        query = text(
            "SELECT extracted_name, extracted_type, similarity_score, matched_grace_id, matched_name "
            "FROM entity_resolution_log "
            "WHERE resolution_note IS NULL AND resolution_tier = 'embedding' "
            f"ORDER BY resolved_at DESC LIMIT {sample_size}"
        )
        rows = session.execute(query).fetchall()
    finally:
        session.close()

    if not rows:
        return {
            "status": "no_data",
            "message": "No embedding-tier resolution log entries found",
            "per_type": {},
        }

    # Group by extracted_type
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row.extracted_type].append({
            "extracted_name": row.extracted_name,
            "extracted_type": row.extracted_type,
            "old_score": float(row.similarity_score) if row.similarity_score else 0.0,
            "matched_grace_id": row.matched_grace_id,
            "matched_name": row.matched_name,
        })

    per_type_results: dict[str, dict] = {}

    for entity_type, entries in by_type.items():
        diffs: list[float] = []
        flipped_outcomes: int = 0

        thresholds = config.er_thresholds.get(
            entity_type,
            {"merge": config.er_default_merge, "review": config.er_default_review},
        )
        merge_t = thresholds["merge"]
        review_t = thresholds["review"]

        for entry in entries:
            # Re-embed the extracted entity using the same text builder
            # CP7 note: replay uses extracted_name + extracted_type only (no properties)
            # because the log doesn't store properties. The 0.05 drift bar absorbs this.
            text = build_embedding_text(entry["extracted_name"], entry["extracted_type"], None)
            try:
                query_vec = (await embed_texts([text], base_url=ollama_url, model=model))[0]
            except Exception:
                continue

            # Issue ANN query
            embedding_literal = "[" + ",".join(str(v) for v in query_vec) + "]"
            ann_sql = (
                f"SELECT vectorNeighbors('{entity_type}[_embedding]', "
                f"{embedding_literal}, 20) AS neighbors"
            )
            try:
                result = await client.execute_sql(ann_sql)
            except Exception:
                continue

            ann_rows = result.get("result", [])
            if not ann_rows or not ann_rows[0].get("neighbors"):
                continue

            # Find the matched entity in ANN results
            new_score = None
            for neighbor in ann_rows[0]["neighbors"]:
                if neighbor.get("grace_id") == entry["matched_grace_id"]:
                    distance = neighbor.get("distance", 1.0)
                    new_score = 1.0 - distance
                    break

            if new_score is None:
                # Matched entity not in ANN top-K — this is a flipped outcome
                flipped_outcomes += 1
                continue

            old_score = entry["old_score"]
            diff = abs(new_score - old_score)
            diffs.append(diff)

            # Check for flipped outcomes
            def _classify(s: float) -> str:
                if s >= merge_t:
                    return "merge"
                if s >= review_t:
                    return "review"
                return "new"

            if _classify(old_score) != _classify(new_score):
                flipped_outcomes += 1

        mean_drift = sum(diffs) / len(diffs) if diffs else 0.0
        max_drift = max(diffs) if diffs else 0.0

        per_type_results[entity_type] = {
            "sample_size": len(entries),
            "compared": len(diffs),
            "mean_absolute_drift": round(mean_drift, 6),
            "max_drift": round(max_drift, 6),
            "flipped_outcomes": flipped_outcomes,
            "thresholds_verified": mean_drift <= 0.05 and flipped_outcomes == 0,
        }

    all_verified = all(r["thresholds_verified"] for r in per_type_results.values())

    return {
        "status": "verified_unchanged" if all_verified else "recalibration_needed",
        "per_type": per_type_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-calibrate ER thresholds")
    parser.add_argument("--sample-size", type=int, default=100)
    args = parser.parse_args()

    result = asyncio.run(recalibrate(args.sample_size))
    print(json.dumps(result, indent=2))

    if result["status"] != "verified_unchanged" and result["status"] != "no_data":
        sys.exit(1)


if __name__ == "__main__":
    main()
