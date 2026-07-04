"""Extraction eval checkpoint — measures extraction quality against sample documents.

Runnable as:
    PYTHONPATH=. python3 -m src.extraction.eval_checkpoint --schema path/to/schema.json

v1 (Chunk 18): extraction-only metrics.
v2 (Chunk 19): add --verify for verification metrics.
v3 (Chunk 20): add --resolve for entity resolution metrics.

Does NOT require FastAPI or PostgreSQL. Uses FileSchemaRouter to read
schema from a local JSON file instead of calling the API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog

from src.extraction.claim_models import ClaimStatus, ClaimVerdict
from src.extraction.document_chunker import DocumentChunker
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_pipeline import ExtractionPipeline
from src.extraction.instructor_client import ExtractionLLMClient
from src.extraction.schema_utils import extract_allowed_types

log = structlog.get_logger()

# F-0008 / ISS-0041: the honest set of suffixes this CLI can read DIRECTLY —
# it reads files as plain text (``Path.read_text()``); there is NO Docling
# path here, so binary formats (.pdf/.docx/.xlsx/.pptx) cannot be read from
# disk. Binary-format follow-up (ISS-0041 addendum): binary formats ARE
# servable via ``--from-processed-doc``, which sources the text from
# ``processed_documents.extracted_text`` (already populated by the Docling
# batch runner, keyed on UNIQUE(file_path)) instead of reading the file.
# ``src/api/extraction_routes.py`` mirrors this list to gate suffixes at the
# route (it cannot import this constant — D246 route isolation forbids
# importing eval_checkpoint from the route module).
SUPPORTED_DOC_SUFFIXES = (".txt", ".md")


class FileSchemaRouter:
    """Schema router that reads from a local JSON file.

    Drop-in replacement for OntologyRouter in eval context.
    Does not require FastAPI or PostgreSQL.
    """

    def __init__(self, schema: dict):
        self._schema = schema

    async def resolve_schema(self, module_name=None):
        return (self._schema, None)

    async def get_available_modules(self):
        return []  # single module / flat schema


def compute_metrics(
    batches: list,
    schema: dict,
    provider: str,
    model: str,
    schema_file: str,
    verify: bool = False,
    verification_provider: str = "",
    verification_model: str = "",
) -> dict:
    """Compute aggregate metrics from extraction batches.

    Args:
        batches: List of ExtractionBatch results.
        schema: Ontology schema dict used for extraction.
        provider: Provider name used.
        model: Model name used.
        schema_file: Path to schema file.
        verify: Whether verification was enabled (adds v2 metrics).
        verification_provider: Provider used for verification.
        verification_model: Model used for verification.

    Returns:
        Metrics dict. ``entities_per_chunk`` / ``relationships_per_chunk`` use
        per-chunk counts from ``ExtractionBatch.chunk_*`` (successful chunks
        only). ``avg_latency_ms`` is the mean of ``chunk_latency_ms`` for
        those successful chunks.
    """
    entity_types, predicates = extract_allowed_types(schema)

    total_chunks = sum(b.chunks_total for b in batches)
    total_succeeded = sum(b.chunks_succeeded for b in batches)
    total_failed = sum(b.chunks_failed for b in batches)
    total_entities_pre = sum(b.entities_pre_dedup_count for b in batches)
    total_entities_post = sum(len(b.entities) for b in batches)
    total_rels_pre = sum(b.relationships_pre_dedup_count for b in batches)
    total_rels_post = sum(len(b.relationships) for b in batches)

    # Parse success rate
    parse_success_rate = total_succeeded / total_chunks if total_chunks > 0 else 0.0

    # Schema-legal entity rate
    all_entities = [e for b in batches for e in b.entities]
    if not entity_types:
        schema_legal_entity_rate = None
        log.info("Entity type enumeration unavailable; schema_legal_entity_rate omitted.")
    elif all_entities:
        legal_count = sum(1 for e in all_entities if e.entity_type in entity_types)
        schema_legal_entity_rate = legal_count / len(all_entities)
    else:
        schema_legal_entity_rate = 0.0

    # Schema-legal relationship rate
    all_rels = [r for b in batches for r in b.relationships]
    if not predicates:
        schema_legal_relationship_rate = None
        log.info("Relationship predicate enumeration unavailable; metric omitted.")
    elif all_rels:
        legal_count = sum(1 for r in all_rels if r.predicate in predicates)
        schema_legal_relationship_rate = legal_count / len(all_rels)
    else:
        schema_legal_relationship_rate = 0.0

    # Duplicate rates
    dup_entity_rate = (
        (total_entities_pre - total_entities_post) / total_entities_pre
        if total_entities_pre > 0
        else 0.0
    )
    dup_rel_rate = (
        (total_rels_pre - total_rels_post) / total_rels_pre
        if total_rels_pre > 0
        else 0.0
    )

    # Per-chunk entity/relationship counts: successful chunks only, from pipeline
    chunk_entity_samples: list[int] = []
    chunk_rel_samples: list[int] = []
    chunk_latencies_success: list[float] = []

    for b in batches:
        n = b.chunks_total
        if n <= 0:
            continue
        ok = (
            len(b.chunk_extraction_succeeded) == n
            and len(b.chunk_entity_counts) == n
            and len(b.chunk_relationship_counts) == n
            and len(b.chunk_latency_ms) == n
        )
        if not ok:
            continue
        for i in range(n):
            if b.chunk_extraction_succeeded[i]:
                chunk_entity_samples.append(b.chunk_entity_counts[i])
                chunk_rel_samples.append(b.chunk_relationship_counts[i])
                chunk_latencies_success.append(b.chunk_latency_ms[i])

    if chunk_entity_samples:
        entities_per_chunk = {
            "mean": sum(chunk_entity_samples) / len(chunk_entity_samples),
            "min": min(chunk_entity_samples),
            "max": max(chunk_entity_samples),
        }
    else:
        entities_per_chunk = {"mean": 0.0, "min": 0, "max": 0}

    if chunk_rel_samples:
        relationships_per_chunk = {
            "mean": sum(chunk_rel_samples) / len(chunk_rel_samples),
            "min": min(chunk_rel_samples),
            "max": max(chunk_rel_samples),
        }
    else:
        relationships_per_chunk = {"mean": 0.0, "min": 0, "max": 0}

    avg_latency_ms = (
        sum(chunk_latencies_success) / len(chunk_latencies_success)
        if chunk_latencies_success
        else 0.0
    )

    metrics = {
        "provider": provider,
        "model": model,
        "schema_file": schema_file,
        "documents_processed": len(batches),
        "total_chunks": total_chunks,
        "total_entities_pre_dedup": total_entities_pre,
        "total_entities_post_dedup": total_entities_post,
        "total_relationships_pre_dedup": total_rels_pre,
        "total_relationships_post_dedup": total_rels_post,
        "parse_success_rate": parse_success_rate,
        "schema_legal_entity_rate": schema_legal_entity_rate,
        "schema_legal_relationship_rate": schema_legal_relationship_rate,
        "entities_per_chunk": entities_per_chunk,
        "relationships_per_chunk": relationships_per_chunk,
        "duplicate_entity_rate": dup_entity_rate,
        "duplicate_relationship_rate": dup_rel_rate,
        "chunks_failed": total_failed,
        "avg_latency_ms": avg_latency_ms,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    # v2 verification metrics (from batch.claims populated by pipeline)
    if verify:
        all_claims = [c for b in batches for c in b.claims]
        total_verified = len(all_claims)
        total_failures = sum(b.verification_failure_count for b in batches)

        if total_verified > 0:
            supported = sum(
                1 for c in all_claims if c.verdict == ClaimVerdict.SUPPORTED
            )
            refuted = sum(
                1 for c in all_claims if c.verdict == ClaimVerdict.REFUTED
            )
            insufficient = sum(
                1 for c in all_claims if c.verdict == ClaimVerdict.INSUFFICIENT
            )
            supported_confs = [
                c.confidence for c in all_claims
                if c.verdict == ClaimVerdict.SUPPORTED and c.confidence is not None
            ]
            all_confs = [
                c.confidence for c in all_claims if c.confidence is not None
            ]
            avg_evidence = sum(
                len(c.evidence_spans) for c in all_claims
            ) / total_verified
        else:
            supported = refuted = insufficient = 0
            supported_confs = []
            all_confs = []
            avg_evidence = 0.0

        metrics.update({
            "verifier_contradiction_rate": (
                refuted / total_verified if total_verified else 0.0
            ),
            "verdict_distribution": {
                "SUPPORTED": supported,
                "REFUTED": refuted,
                "INSUFFICIENT": insufficient,
            },
            "avg_evidence_span_count": avg_evidence,
            "avg_confidence_supported": (
                sum(supported_confs) / len(supported_confs)
                if supported_confs else 0.0
            ),
            "avg_confidence_overall": (
                sum(all_confs) / len(all_confs) if all_confs else 0.0
            ),
            "verification_failure_count": total_failures,
            "verification_provider": verification_provider,
            "verification_model": verification_model,
        })

    # v3 entity resolution metrics (from batch.er_stats)
    er_batches = [b for b in batches if b.er_stats is not None]
    if er_batches:
        combined_tier_counts: dict[str, int] = {}
        total_resolved = 0
        total_new = 0
        total_matched = 0
        for b in er_batches:
            ers = b.er_stats
            for tier, count in ers.get("tier_counts", {}).items():
                combined_tier_counts[tier] = combined_tier_counts.get(tier, 0) + count
            total_resolved += ers.get("total", 0)
            total_new += ers.get("new_count", 0)
            total_matched += ers.get("matched_count", 0)

        metrics["entity_resolution"] = {
            "tier_distribution": combined_tier_counts,
            "total_resolved": total_resolved,
            "new_count": total_new,
            "matched_count": total_matched,
            "new_ratio": total_new / total_resolved if total_resolved else 0.0,
            "matched_ratio": total_matched / total_resolved if total_resolved else 0.0,
        }

    return metrics


async def run_eval(args: argparse.Namespace) -> dict:
    """Run the eval checkpoint pipeline."""
    # Load schema
    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"Error: schema file not found: {schema_path}", file=sys.stderr)
        sys.exit(1)
    schema = json.loads(schema_path.read_text())

    # Build config with overrides
    config_kwargs = {}
    if args.provider:
        config_kwargs["extraction_provider"] = args.provider
    if args.model:
        config_kwargs["extraction_model"] = args.model
    config = ExtractionSettings(**config_kwargs)

    # Select documents
    doc_file_arg = getattr(args, "doc_file", None)
    from_processed_doc = getattr(args, "from_processed_doc", False)
    if from_processed_doc and not doc_file_arg:
        # F-0008 / ISS-0041 (binary-format follow-up): the flag's only
        # semantic is "look up THIS file's row" — meaningless without a file.
        print(
            "Error: --from-processed-doc requires --doc-file (the resolved "
            "path is the processed_documents lookup key).",
            file=sys.stderr,
        )
        sys.exit(1)
    if doc_file_arg:
        # F-0008 / ISS-0041: --doc-file extracts exactly the requested file.
        # Previously the route spawned this CLI with
        # ``--doc-dir <parent> --sample-count 1`` and the requested filename
        # was DISCARDED — ``sorted(dir)[:1]`` extracted the alphabetically
        # first .txt/.md in the directory instead of the named document.
        doc_path = Path(doc_file_arg)
        if from_processed_doc:
            # F-0008 / ISS-0041 (binary-format follow-up): the DB row is
            # authoritative in this mode — the file itself is never read, so
            # neither ``is_file()`` nor the plain-text suffix check applies.
            # ``resolve()`` (non-strict) matches ``process_document``'s
            # ``Path(file_path).resolve()`` key in src/discovery/
            # document_processor.py, so the lookup key is byte-identical to
            # what the batch runner stored even if the source file has since
            # been moved or deleted.
            doc_path = doc_path.resolve()
        else:
            if not doc_path.is_file():
                print(
                    f"Error: --doc-file not found or not a regular file: {doc_path}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if doc_path.suffix.lower() not in SUPPORTED_DOC_SUFFIXES:
                print(
                    f"Error: --doc-file has unsupported suffix {doc_path.suffix!r}. "
                    f"Supported suffixes: {', '.join(SUPPORTED_DOC_SUFFIXES)} "
                    "(this CLI reads plain text only — no Docling path for binary "
                    "formats; pass --from-processed-doc to source text from "
                    "processed_documents.extracted_text instead).",
                    file=sys.stderr,
                )
                sys.exit(1)
        doc_files = [doc_path]
    else:
        # Legacy --doc-dir behavior for existing eval callers (unchanged).
        doc_dir = Path(args.doc_dir)
        if not doc_dir.exists():
            print(f"Error: document directory not found: {doc_dir}", file=sys.stderr)
            sys.exit(1)

        doc_files = sorted(
            [
                f
                for f in doc_dir.iterdir()
                if f.suffix in SUPPORTED_DOC_SUFFIXES and f.is_file()
            ]
        )
        doc_files = doc_files[: args.sample_count]

        if not doc_files:
            print(f"No .txt or .md files found in {doc_dir}", file=sys.stderr)
            sys.exit(1)

    use_verify = getattr(args, "verify", False)
    use_resolve = getattr(args, "resolve", False)
    if use_resolve and not use_verify:
        use_verify = True
        print("Note: --resolve implies --verify; enabling verification.")

    # F-0009 / ISS-0041: --persist makes this an honest document→claims path.
    # Without it, extract_document ran with session=None — claims and
    # extraction events were never persisted, so the "document extraction"
    # API was really a metrics-only eval run and every downstream dependent
    # (the /api/claims review queue, Signal A/B/D over extraction_claims,
    # Signal C/E validation counters) starved. Session construction mirrors
    # src/extraction/embedding_backfill.py (the ISS-0007 phantom-import fix).
    use_persist = getattr(args, "persist", False)
    persist_session = None
    if use_persist:
        from src.shared.database import get_session_factory

        persist_session = get_session_factory()()
        if not use_verify:
            # Claims are produced by the verification pass; persisting
            # without --verify would be a no-op (no claims to write).
            use_verify = True
            print("Note: --persist implies --verify; enabling verification.")

    # F-0008 / ISS-0041 (binary-format follow-up): resolve the document text
    # from processed_documents.extracted_text. The Docling batch runner is
    # the ONLY component that parses binary formats (architect decision: no
    # second Docling stack here); this CLI consumes its persisted output.
    # A DB session is required for the lookup even in non-persist eval mode —
    # when --persist supplied one we reuse it, otherwise a short-lived
    # read-only session is opened for the lookup and closed immediately.
    processed_doc_text: str | None = None
    if from_processed_doc:
        lookup_session = persist_session
        owns_lookup_session = False
        if lookup_session is None:
            from src.shared.database import get_session_factory

            lookup_session = get_session_factory()()
            owns_lookup_session = True
        try:
            from src.discovery.database import get_document_by_path

            processed = get_document_by_path(lookup_session, str(doc_files[0]))
        finally:
            if owns_lookup_session:
                lookup_session.close()
        if processed is None or not (processed.extracted_text or "").strip():
            # Missing row (or a row with no text, e.g. a FAILED Docling run)
            # → clear exit-1 guidance instead of a confusing downstream error.
            print(
                f"Error: no processed_documents row with extracted text for "
                f"{doc_files[0]} — run the Docling batch first: "
                f"python -m src.discovery.batch_runner --source-dir "
                f"{doc_files[0].parent}",
                file=sys.stderr,
            )
            sys.exit(1)
        processed_doc_text = processed.extracted_text

    print(f"Eval: {len(doc_files)} documents, schema={schema_path.name}"
          f"{' (with verification)' if use_verify else ''}"
          f"{' (with resolution)' if use_resolve else ''}")

    # Build pipeline components
    chunker = DocumentChunker(config)
    router = FileSchemaRouter(schema)
    client = ExtractionLLMClient(config)

    # Set up ArcadeDB client for resolution if requested
    arcade_client = None
    if use_resolve:
        try:
            from src.graph.arcade_client import ArcadeClient, ArcadeConfig
            arcade_client = ArcadeClient(config=ArcadeConfig())
            # Quick health check
            import httpx
            resp = httpx.get(
                f"http://{arcade_client.config.host}:{arcade_client.config.port}/api/v1/server",
                auth=(arcade_client.config.username, arcade_client.config.password),
                timeout=3.0,
            )
            if resp.status_code != 200:
                raise ConnectionError("ArcadeDB not healthy")
        except Exception as e:
            print(f"Warning: ArcadeDB unavailable ({e}). Skipping entity resolution.")
            arcade_client = None
            use_resolve = False

    # Resolve provider/model for metrics
    provider_used = client.extraction_provider
    model_used = client.extraction_model

    # Process documents
    batches = []
    try:
        for doc_file in doc_files:
            # F-0008 / ISS-0041 (binary-format follow-up): in
            # --from-processed-doc mode the text comes from the Docling
            # batch's persisted extracted_text — the (possibly binary) file
            # is never read here.
            if processed_doc_text is not None:
                text = processed_doc_text
            else:
                text = doc_file.read_text()
            doc_id = doc_file.name
            print(f"  Processing: {doc_id} ({len(text)} chars)")

            pipeline = ExtractionPipeline(
                config=config, chunker=chunker, router=router, client=client,
                arcade_client=arcade_client,
            )
            # F-0009 / ISS-0041: session was hardcoded to None here — with
            # --persist a real session flows through so claims/events land in
            # Postgres. write=True (extract_document default) so the graph
            # write path also runs when resolution is enabled.
            batch = await pipeline.extract_document(
                text, doc_id, verify=use_verify, resolve=use_resolve,
                session=persist_session,
            )
            if persist_session is not None:
                # D84: the pipeline never commits — the caller owns the txn.
                persist_session.commit()
            batches.append(batch)
            print(
                f"    chunks={batch.chunks_total} "
                f"entities={len(batch.entities)} "
                f"rels={len(batch.relationships)}"
            )
    finally:
        if persist_session is not None:
            persist_session.close()

    # Compute metrics
    metrics = compute_metrics(
        batches, schema, provider_used, model_used, str(schema_path),
        verify=use_verify,
        verification_provider=client.verification_provider if use_verify else "",
        verification_model=client.verification_model if use_verify else "",
    )

    # Adjust output path for --verify / --resolve
    output = args.output
    if use_resolve and output == "docs/eval/chunk18_eval.json":
        output = "docs/eval/chunk20_eval.json"
    elif use_verify and output == "docs/eval/chunk18_eval.json":
        output = "docs/eval/chunk19_eval.json"

    # Write output
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(metrics, indent=2, default=str))
    print(f"\nMetrics written to: {output_path}")

    # Print summary
    print("\n=== Eval Summary ===")
    print(f"Documents:        {metrics['documents_processed']}")
    print(f"Total chunks:     {metrics['total_chunks']}")
    print(f"Parse success:    {metrics['parse_success_rate']:.1%}")
    print(f"Entities (post):  {metrics['total_entities_post_dedup']}")
    print(f"Relationships:    {metrics['total_relationships_post_dedup']}")
    slr = metrics["schema_legal_entity_rate"]
    print(f"Schema-legal E:   {slr:.1%}" if slr is not None else "Schema-legal E:   N/A")
    srr = metrics["schema_legal_relationship_rate"]
    print(f"Schema-legal R:   {srr:.1%}" if srr is not None else "Schema-legal R:   N/A")
    print(f"Dedup entity:     {metrics['duplicate_entity_rate']:.1%}")
    print(f"Dedup rel:        {metrics['duplicate_relationship_rate']:.1%}")
    print(f"Avg latency:      {metrics['avg_latency_ms']:.0f}ms")
    print(f"Chunks failed:    {metrics['chunks_failed']}")

    if "verifier_contradiction_rate" in metrics:
        print(f"Verifier:         {metrics['verification_provider']}/{metrics['verification_model']}")
        print(f"Contradiction:    {metrics['verifier_contradiction_rate']:.1%}")
        vd = metrics["verdict_distribution"]
        print(f"Verdicts:         S={vd['SUPPORTED']} R={vd['REFUTED']} I={vd['INSUFFICIENT']}")
        print(f"Avg evidence:     {metrics['avg_evidence_span_count']:.1f} sentences")
        print(f"Avg confidence:   {metrics['avg_confidence_overall']:.2f} (SUPPORTED: {metrics['avg_confidence_supported']:.2f})")
        print(f"Verify failures:  {metrics['verification_failure_count']}")

    if "entity_resolution" in metrics:
        er = metrics["entity_resolution"]
        print(f"ER total:         {er['total_resolved']}")
        print(f"ER new/matched:   {er['new_count']}/{er['matched_count']}")
        print(f"ER tiers:         {er['tier_distribution']}")

    return metrics


# D476: argparser factory for CLI argv contract testing. Authorization: D476.
def _build_argparser() -> argparse.ArgumentParser:
    """Return the CLI argparser without parsing argv.

    Extracted from ``main()`` so that ``tests/api/test_route_cli_spawn_contracts.py``
    can validate route-side argv construction against this parser.
    """
    parser = argparse.ArgumentParser(
        description="Extraction eval checkpoint"
    )
    parser.add_argument(
        "--schema", required=True, help="Path to ontology JSON Schema file"
    )
    parser.add_argument(
        "--provider", default=None, help="Override extraction provider"
    )
    parser.add_argument(
        "--model", default=None, help="Override extraction model"
    )
    parser.add_argument(
        "--sample-count", type=int, default=10, help="Documents to process"
    )
    parser.add_argument(
        "--output",
        default="docs/eval/chunk18_eval.json",
        help="Output path for metrics JSON",
    )
    parser.add_argument(
        "--doc-dir",
        default="data/discovery-sample/",
        help="Directory of sample documents",
    )
    parser.add_argument(
        # F-0008 / ISS-0041: extract exactly the named file instead of
        # sorted(--doc-dir)[:--sample-count]. Takes precedence over --doc-dir.
        "--doc-file",
        default=None,
        help=(
            "Path to a single document to extract (takes precedence over "
            f"--doc-dir). Supported suffixes: {', '.join(SUPPORTED_DOC_SUFFIXES)}"
        ),
    )
    parser.add_argument(
        # F-0008 / ISS-0041 (binary-format follow-up): source the document
        # text from ``processed_documents.extracted_text`` (populated by the
        # Docling batch runner, keyed on UNIQUE(file_path)) instead of
        # ``Path.read_text()``. This is the architect-decided path for binary
        # formats (.pdf/.docx/.xlsx/.pptx) — do NOT wire a second Docling
        # stack into this CLI. Requires --doc-file (the resolved path is the
        # lookup key); bypasses the SUPPORTED_DOC_SUFFIXES check because the
        # file itself is never read. Explicit flag (not suffix-sniffing) so
        # the caller — the route, after its own pre-check — states intent.
        "--from-processed-doc", action="store_true", default=False,
        help=(
            "Source document text from processed_documents.extracted_text "
            "(Docling batch output) keyed on the resolved --doc-file path, "
            "instead of reading the file as plain text. Enables binary "
            "formats (.pdf/.docx/.xlsx/.pptx) that the batch runner has "
            "already processed."
        ),
    )
    parser.add_argument(
        # F-0009 / ISS-0041: persist claims + extraction events via a real DB
        # session (implies --verify). Without this flag behavior is the
        # legacy metrics-only eval run (session=None, nothing persisted).
        "--persist", action="store_true", default=False,
        help="Persist claims and extraction events to Postgres (implies --verify)",
    )
    parser.add_argument(
        "--verify", action="store_true", default=False,
        help="Run verification pass and compute v2 metrics",
    )
    parser.add_argument(
        "--resolve", action="store_true", default=False,
        help="Run entity resolution and compute v3 ER metrics (requires ArcadeDB)",
    )
    parser.add_argument(
        "--job-id", type=str, default=None,
        help="extraction_jobs UUID — when present, report progress back to the DB row",
    )
    return parser


def main():
    # F-0023 / ISS-0041: mirror this subprocess's OTel counters into the
    # Prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is
    # unset). Without this call the extraction counters died with the
    # process. Pattern mirrors src/extraction/extraction_bridge.py main().
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = _build_argparser()
    args = parser.parse_args()

    if args.job_id:
        _run_with_job_tracking(args)
    else:
        asyncio.run(run_eval(args))


def _run_with_job_tracking(args) -> None:
    """Wrap run_eval with extraction_jobs progress reporting (D470)."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from src.shared.database import get_session_factory

    factory = get_session_factory()
    session = factory()
    job_id = args.job_id

    try:
        # Mark running
        session.execute(
            text(
                "UPDATE extraction_jobs SET status='running', started_at=now(), "
                "progress_json=:pj WHERE job_id=:jid"
            ),
            {"jid": job_id, "pj": '{"documents_processed": 0, "documents_total": 0, "current_file": "", "last_tick_at": "' + datetime.now(timezone.utc).isoformat() + '"}'},
        )
        session.commit()

        asyncio.run(run_eval(args))

        # Mark completed
        session.execute(
            text(
                "UPDATE extraction_jobs SET status='completed', completed_at=now(), "
                "progress_json=:pj WHERE job_id=:jid"
            ),
            {"jid": job_id, "pj": '{"documents_processed": 0, "documents_total": 0, "current_file": "done", "last_tick_at": "' + datetime.now(timezone.utc).isoformat() + '"}'},
        )
        session.commit()

        # OTel emit (best-effort)
        try:
            from src.analytics.metrics import record_extraction_job_completed
            record_extraction_job_completed(job_kind="document")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        import traceback

        # D475: capture up to 4096 chars of traceback for operator visibility
        tb = traceback.format_exc()[-4096:]
        try:
            session.execute(
                text(
                    "UPDATE extraction_jobs SET status='failed', completed_at=now(), "
                    "error_message=:err WHERE job_id=:jid"
                ),
                {"jid": job_id, "err": tb},
            )
            session.commit()
        except Exception:  # noqa: BLE001 — D460 pattern: don't mask original error
            log.warning("eval_checkpoint.job_tracking.update_failed", exc_info=True)

        # OTel emit (best-effort)
        try:
            from src.analytics.metrics import record_extraction_job_failed
            record_extraction_job_failed(job_kind="document")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
