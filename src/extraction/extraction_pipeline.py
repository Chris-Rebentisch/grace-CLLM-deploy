"""Pipeline orchestrator: document -> chunks -> route -> extract -> dedup -> batch.

Wires together DocumentChunker, OntologyRouter, and ExtractionLLMClient
into a single extraction flow for a document.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import structlog
from opentelemetry import trace
from sqlalchemy.orm import Session

from src.analytics.compression_ratio import record_compression_ratio
from src.analytics.llm_instrumentation import grace_call_tags
from src.analytics.pipeline_instrumentation import record_pipeline_stage
from src.extraction.claim_database import (
    insert_claims_batch,
    insert_extraction_event,
    update_extraction_event_status,
)
from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict
from src.extraction.entity_resolver import EntityResolutionResult, EntityResolver
from src.extraction.confidence_scorer import score_claim
from src.extraction.document_chunker import DocumentChunker
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import (
    DocumentChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionBatch,
    ExtractionResult,
)
from src.extraction.extractor import ExtractedChunkResult, extract_chunk
from src.extraction.instructor_client import ExtractionLLMClient
from src.extraction.name_utils import DEFAULT_STRIP_SUFFIXES, normalize_entity_name
from src.extraction.schema_utils import normalize_property_shape
from src.extraction.resolution_database import insert_resolution_logs_batch
from src.extraction.verification import record_triple_confidence, verify_batch

log = structlog.get_logger()
_tracer = trace.get_tracer("grace.extraction.pipeline")


class ExtractionPipeline:
    """Orchestrator: document -> chunks -> route -> extract -> dedup -> batch.

    Wires together DocumentChunker, OntologyRouter, and ExtractionLLMClient
    into a single extraction flow for a document.
    """

    def __init__(
        self,
        config: ExtractionSettings,
        chunker: DocumentChunker,
        router,
        client: ExtractionLLMClient,
        strip_suffixes: list[str] | None = None,
        arcade_client=None,
        ollama_base_url: str = "http://localhost:11434",
    ):
        self._config = config
        self._chunker = chunker
        self._router = router
        self._client = client
        self._strip_suffixes = strip_suffixes or DEFAULT_STRIP_SUFFIXES
        self._arcade_client = arcade_client
        self._ollama_base_url = ollama_base_url

    async def extract_document(
        self,
        document_text: str,
        document_id: str,
        module_name: str | None = None,
        docling_json: dict | None = None,
        verify: bool = True,
        resolve: bool = True,
        write: bool = True,
        session: Session | None = None,
        evidence_origin: Literal["document", "communication", "hybrid"] = "document",
        sensitivity_tags: str = "",
    ) -> ExtractionBatch:
        """Public entry point — wraps `_run_extract_document` in the outer span.

        D510 — evidence_origin kwarg widens for email extraction bridge;
        D520 — sensitivity_tags propagates source sensitivity to domain vertices;
        D106 write-batch contract unchanged.
        """
        with _tracer.start_as_current_span("extraction.run") as _outer:
            _outer.set_attribute("grace.module", "extraction")
            _outer.set_attribute("grace.pipeline", "extraction")
            return await self._run_extract_document(
                document_text=document_text,
                document_id=document_id,
                module_name=module_name,
                docling_json=docling_json,
                verify=verify,
                resolve=resolve,
                write=write,
                session=session,
                evidence_origin=evidence_origin,
                sensitivity_tags=sensitivity_tags,
            )

    async def _run_extract_document(
        self,
        document_text: str,
        document_id: str,
        module_name: str | None = None,
        docling_json: dict | None = None,
        verify: bool = True,
        resolve: bool = True,
        write: bool = True,
        session: Session | None = None,
        evidence_origin: Literal["document", "communication", "hybrid"] = "document",
        sensitivity_tags: str = "",
    ) -> ExtractionBatch:
        """Extract entities and relationships from a full document.

        Pipeline steps:
        1. Chunk document via chunker.chunk_document()
        2. Apply D65 multi-module guard and resolve schema
        3. Extract each chunk in parallel (Semaphore for concurrency)
        4. Aggregate results across chunks (with chunk_source_map annotation)
        5. Cross-chunk entity dedup (D62)
        6. Cross-chunk relationship dedup (D62)
        7. Verify entities and relationships (when verify=True)
        8. Score and create claims (when verify=True)
        9. Entity resolution on AUTO_ACCEPTED entities (when resolve=True)
        10. Persist claims and extraction event (when session provided)
        11. Temporal tagging (when write=True)
        12. Constraint validation (when write=True)
        13. Graph write + provenance (when write=True)

        D93: resolve=True requires verify=True. If resolve=True and
        verify=False, a warning is logged and resolve is forced to False.
        D102: write=True requires resolve=True requires verify=True.
        """
        # D102 guard
        if write and not resolve:
            log.warning(
                "extraction.write_requires_resolve",
                msg="write=True requires resolve=True; forcing write=False",
            )
            write = False
        if write and self._arcade_client is None:
            log.warning(
                "extraction.write_no_arcade_client",
                msg="write=True but no arcade_client; forcing write=False",
            )
            write = False

        # D93 guard
        if resolve and not verify:
            log.warning(
                "extraction.resolve_requires_verify",
                msg="resolve=True requires verify=True; forcing resolve=False",
            )
            resolve = False

        if resolve and self._arcade_client is None:
            log.warning(
                "extraction.resolve_no_arcade_client",
                msg="resolve=True but no arcade_client; forcing resolve=False",
            )
            resolve = False

        started_at = datetime.now(UTC)

        # Step 1: Chunk
        async with record_pipeline_stage(pipeline="extraction", stage="chunk"):
            chunks = self._chunker.chunk_document(
                document_text, document_id, docling_json
            )

        if not chunks:
            return ExtractionBatch(
                document_id=document_id,
                module_name=module_name,
                chunks_total=0,
                chunks_succeeded=0,
                chunks_failed=0,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                chunks=[],
            )

        # Step 2: Resolve schema with D65 guard
        async with record_pipeline_stage(pipeline="extraction", stage="route"):
            schema, schema_version = await self._resolve_schema_with_guard(module_name)

        # Step 3: Extract chunks (grace_call_tags so Instructor sees the tags)
        async with record_pipeline_stage(pipeline="extraction", stage="extract"), \
                   grace_call_tags("extraction", "extract"):
            chunk_results = await self._extract_chunks(chunks, schema)

        # Step 4: Aggregate with chunk_source_map annotation
        all_entities: list[ExtractedEntity] = []
        all_relationships: list[ExtractedRelationship] = []
        succeeded = 0
        failed = 0

        chunk_extraction_succeeded: list[bool] = []
        chunk_entity_counts: list[int] = []
        chunk_relationship_counts: list[int] = []
        chunk_latency_ms: list[float] = []

        for cr, chunk in zip(chunk_results, chunks, strict=True):
            chunk_latency_ms.append(cr.latency_ms)
            chunk_extraction_succeeded.append(cr.success)
            if cr.success:
                succeeded += 1
                if cr.result is not None:
                    chunk_entity_counts.append(len(cr.result.entities))
                    chunk_relationship_counts.append(len(cr.result.relationships))
                    annotated = self._annotate_chunk_sources(cr.result, chunk)
                    all_entities.extend(annotated.entities)
                    all_relationships.extend(annotated.relationships)
                else:
                    chunk_entity_counts.append(0)
                    chunk_relationship_counts.append(0)
            else:
                failed += 1
                chunk_entity_counts.append(0)
                chunk_relationship_counts.append(0)

        # Record pre-dedup counts
        entities_pre_dedup = len(all_entities)
        relationships_pre_dedup = len(all_relationships)

        # Steps 5-6: Dedup (returns entities + chunk_counts)
        deduped_entities, entity_chunk_counts = self._dedup_entities(all_entities)
        deduped_rels, rel_chunk_counts = self._dedup_relationships(all_relationships)

        batch = ExtractionBatch(
            document_id=document_id,
            module_name=module_name,
            schema_version=schema_version,
            chunks_total=len(chunks),
            chunks_succeeded=succeeded,
            chunks_failed=failed,
            chunk_extraction_succeeded=chunk_extraction_succeeded,
            chunk_entity_counts=chunk_entity_counts,
            chunk_relationship_counts=chunk_relationship_counts,
            chunk_latency_ms=chunk_latency_ms,
            entities_pre_dedup_count=entities_pre_dedup,
            relationships_pre_dedup_count=relationships_pre_dedup,
            entities=deduped_entities,
            relationships=deduped_rels,
            provider_used=self._client.extraction_provider,
            model_used=self._client.extraction_model,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            chunks=chunks,
        )

        if not verify:
            return batch

        # Step 7: Verify
        async with record_pipeline_stage(pipeline="extraction", stage="verify"), \
                   grace_call_tags("extraction", "verify"):
            verification_results = await verify_batch(
                batch, self._client, self._config
            )

        # Step 8: Score and create claims
        event_id = str(uuid4())
        claims: list[Claim] = []
        failure_count = 0

        for i, entity in enumerate(batch.entities):
            verdict, evidence_spans, contradiction_reason, was_failure = (
                verification_results[i]
            )
            if was_failure:
                failure_count += 1
                confidence = self._config.verification_failure_confidence
            else:
                merge_key = (
                    normalize_entity_name(entity.name, self._strip_suffixes),
                    entity.entity_type,
                )
                chunk_count = entity_chunk_counts.get(merge_key, 1)
                confidence = score_claim(
                    entity, verdict, schema, self._config, chunk_count
                )

            record_triple_confidence(confidence, verdict, module_name)

            status = (
                ClaimStatus.QUARANTINED if verdict == ClaimVerdict.REFUTED
                else ClaimStatus.AUTO_ACCEPTED
            )
            source_chunk_id = (
                entity.chunk_source_map[0][0] if entity.chunk_source_map else ""
            )

            claims.append(Claim(
                entity_type=entity.entity_type,
                subject_name=entity.name,
                subject_type=entity.entity_type,
                predicate="entity",
                properties_json=entity.properties,
                evidence_spans=evidence_spans,
                verdict=verdict,
                confidence=confidence,
                status=status,
                decision_source="verifier",
                source_document_id=document_id,
                source_chunk_id=source_chunk_id,
                ontology_module=module_name,
                schema_version=schema_version,
                prompt_template_id="verification_v1",
                model_name=self._client.extraction_model,
                verifier_model=self._client.verification_model,
                extraction_event_id=event_id,
                extraction_unit_id=Claim.compute_extraction_unit_id(
                    document_id, source_chunk_id,
                    schema_version, "extraction_v1",
                ),
                claim_fingerprint=Claim.compute_fingerprint(
                    entity.name, "entity", None, entity.properties,
                    [es.text for es in evidence_spans],
                ),
                contradiction_reason=contradiction_reason,
            ))

        for j, rel in enumerate(batch.relationships):
            vr_idx = len(batch.entities) + j
            verdict, evidence_spans, contradiction_reason, was_failure = (
                verification_results[vr_idx]
            )
            if was_failure:
                failure_count += 1
                confidence = self._config.verification_failure_confidence
            else:
                merge_key = (
                    normalize_entity_name(rel.subject_name, self._strip_suffixes),
                    self._normalize_predicate(rel.predicate),
                    normalize_entity_name(rel.object_name, self._strip_suffixes),
                    rel.subject_type,
                    rel.object_type,
                )
                chunk_count = rel_chunk_counts.get(merge_key, 1)
                confidence = score_claim(
                    rel, verdict, schema, self._config, chunk_count
                )

            record_triple_confidence(confidence, verdict, module_name)

            status = (
                ClaimStatus.QUARANTINED if verdict == ClaimVerdict.REFUTED
                else ClaimStatus.AUTO_ACCEPTED
            )
            source_chunk_id = (
                rel.chunk_source_map[0][0] if rel.chunk_source_map else ""
            )

            claims.append(Claim(
                relationship_type=rel.predicate,
                subject_name=rel.subject_name,
                subject_type=rel.subject_type,
                predicate=rel.predicate,
                object_name=rel.object_name,
                object_type=rel.object_type,
                properties_json=rel.properties,
                evidence_spans=evidence_spans,
                verdict=verdict,
                confidence=confidence,
                status=status,
                decision_source="verifier",
                source_document_id=document_id,
                source_chunk_id=source_chunk_id,
                ontology_module=module_name,
                schema_version=schema_version,
                prompt_template_id="verification_v1",
                model_name=self._client.extraction_model,
                verifier_model=self._client.verification_model,
                extraction_event_id=event_id,
                extraction_unit_id=Claim.compute_extraction_unit_id(
                    document_id, source_chunk_id,
                    schema_version, "extraction_v1",
                ),
                claim_fingerprint=Claim.compute_fingerprint(
                    rel.subject_name, rel.predicate, rel.object_name,
                    rel.properties,
                    [es.text for es in evidence_spans],
                ),
                contradiction_reason=contradiction_reason,
            ))

        # Set batch claim aggregates
        batch.claims = claims
        accepted = [c for c in claims if c.status == ClaimStatus.AUTO_ACCEPTED]
        quarantined = [c for c in claims if c.status == ClaimStatus.QUARANTINED]
        batch.claims_accepted = len(accepted)
        batch.claims_quarantined = len(quarantined)
        batch.avg_claim_confidence = (
            sum(c.confidence for c in claims if c.confidence is not None) / len(claims)
            if claims else None
        )
        batch.verification_failure_count = failure_count

        # Step 9: Entity resolution (when resolve=True, after claims built)
        resolution_results = None
        if resolve and batch.claims:
            entity_claims = [
                c for c in batch.claims
                if c.entity_type and c.status == ClaimStatus.AUTO_ACCEPTED
            ]
            if entity_claims:
                accepted_keys = {
                    (normalize_entity_name(c.subject_name, self._strip_suffixes), c.entity_type)
                    for c in entity_claims
                }
                entities_resolvable = [
                    e for e in batch.entities
                    if (normalize_entity_name(e.name, self._strip_suffixes), e.entity_type)
                    in accepted_keys
                ]
                resolver = EntityResolver(
                    arcade_client=self._arcade_client,
                    config=self._config,
                    ollama_base_url=self._ollama_base_url,
                    instructor_client=self._client,
                    strip_suffixes=self._strip_suffixes,
                )
                async with record_pipeline_stage(
                    pipeline="extraction", stage="resolve"
                ), grace_call_tags("extraction", "resolve"):
                    resolution_results = await resolver.resolve_batch(
                        entities_resolvable,
                        extraction_event_id=event_id,
                    )
                _apply_resolution_to_entities(
                    batch.entities, entities_resolvable,
                    resolution_results, self._strip_suffixes,
                )
                _apply_resolution_to_claims(
                    entity_claims, resolution_results,
                    entities_resolvable, self._strip_suffixes,
                )
                batch.er_stats = _compute_er_stats(resolution_results)

        # Step 10: Persist (when session provided, D84: no commit)
        if session is not None and claims:
            event_id = insert_extraction_event(session, {
                "event_id": event_id,
                "batch_id": batch.batch_id,
                "source_document_id": document_id,
                "ontology_module": module_name,
                "schema_version": schema_version,
                "provider_used": self._client.extraction_provider,
                "model_used": self._client.extraction_model,
                "chunks_total": batch.chunks_total,
                "chunks_succeeded": batch.chunks_succeeded,
                "chunks_failed": batch.chunks_failed,
                "entities_extracted": len(batch.entities),
                "relationships_extracted": len(batch.relationships),
                "started_at": batch.started_at,
                "status": "running",
            })
            insert_claims_batch(session, claims)
            if batch.er_stats is not None and resolution_results is not None:
                insert_resolution_logs_batch(
                    session, resolution_results,
                    extraction_event_id=event_id,
                    batch_id=batch.batch_id,
                )
            update_extraction_event_status(session, event_id, "verified", {
                "claims_accepted": batch.claims_accepted,
                "claims_quarantined": batch.claims_quarantined,
                "avg_confidence": batch.avg_claim_confidence,
                "completed_at": datetime.now(UTC),
            })

        # Steps 11-13: Constraint validation, temporal tagging, graph write
        if write and session is not None and batch.claims:
            from src.extraction.constraint_validator import validate_batch as _validate_batch
            from src.extraction.temporal_tagger import tag_temporal
            from src.extraction.graph_writer import write_batch

            # Step 11: Temporal tagging on non-quarantined claims
            # Tag temporal hints from source entities/relationships and store
            # on claim properties for the graph writer to consume
            for claim in batch.claims:
                if claim.status == ClaimStatus.QUARANTINED:
                    continue
                # Find matching entity or relationship for temporal hints
                if claim.entity_type:
                    for entity in batch.entities:
                        if (
                            normalize_entity_name(entity.name, self._strip_suffixes)
                            == normalize_entity_name(claim.subject_name, self._strip_suffixes)
                            and entity.entity_type == claim.entity_type
                        ):
                            vf, vt = tag_temporal(entity.temporal_hints)
                            if vf is not None:
                                claim.properties_json["_tagged_valid_from"] = vf
                            if vt is not None:
                                claim.properties_json["_tagged_valid_to"] = vt
                            break
                elif claim.relationship_type:
                    for rel in batch.relationships:
                        if (
                            claim.subject_name == rel.subject_name
                            and claim.predicate == rel.predicate
                            and claim.object_name == rel.object_name
                        ):
                            vf, vt = tag_temporal(rel.temporal_hints)
                            if vf is not None:
                                claim.properties_json["_tagged_valid_from"] = vf
                            if vt is not None:
                                claim.properties_json["_tagged_valid_to"] = vt
                            break

            # Step 12: Validate all claims (after temporal tagging)
            _validate_batch(
                batch.claims, schema, active_schema_version=schema_version,
                low_confidence_threshold=self._config.confidence_threshold_insufficient,
            )
            # Chunk 32 (D242): emit grace_extraction_validation_failures_total
            # for every ERROR-severity violation. Source for Signals C and E.
            from src.analytics.extraction_validation_emitter import (
                emit_validation_failure,
            )
            from src.extraction.claim_models import ConstraintSeverity as _CS

            for _claim in batch.claims:
                _violations = _claim.constraint_violations or []
                for _v in _violations:
                    if _v.severity != _CS.ERROR:
                        continue
                    emit_validation_failure(
                        kind=_v.rule,
                        ontology_module=(_claim.ontology_module or "__global__"),
                        entity_type=(_claim.entity_type or "__none__"),
                    )
            # Recount quarantined after validation
            batch.claims_quarantined = len(
                [c for c in batch.claims if c.status == ClaimStatus.QUARANTINED]
            )
            batch.claims_accepted = len(
                [c for c in batch.claims if c.status == ClaimStatus.AUTO_ACCEPTED]
            )

            # F-0016 (validation run, 2026-07-03): claims are INSERTED at
            # Step 10, but constraint validation (Step 12) only mutated the
            # in-memory Claim objects — a constraint-quarantined claim stayed
            # `auto_accepted` (violations NULL) in extraction_claims. The graph
            # writer skipped it correctly (in-memory status), so the graph and
            # the claims table disagreed: /api/claims review and Signal D read
            # the stale rows. Persist post-validation status + violations for
            # every claim the validator touched.
            from src.extraction.claim_database import (
                update_claim_status as _update_claim_status,
                update_claim_violations as _update_claim_violations,
            )
            for _claim in batch.claims:
                if _claim.constraint_violations:
                    _update_claim_violations(
                        session, _claim.claim_id, _claim.constraint_violations,
                        decision_source=_claim.decision_source,
                    )
                if _claim.status == ClaimStatus.QUARANTINED and _claim.decision_source == "validator":
                    _update_claim_status(
                        session, _claim.claim_id, _claim.status, _claim.decision_source
                    )

            # Step 13: Graph write
            async with record_pipeline_stage(
                pipeline="extraction", stage="write"
            ):
                # D520 — propagate source sensitivity to domain vertices;
                # D106 write-batch contract unchanged.
                write_result = await write_batch(
                    batch=batch,
                    schema=schema,
                    arcade_client=self._arcade_client,
                    session=session,
                    event_id=event_id,
                    config=self._config,
                    strip_suffixes=self._strip_suffixes,
                    evidence_origin=evidence_origin,
                    sensitivity_tags=sensitivity_tags,
                )
            batch.write_stats = write_result.model_dump() if write_result else None

        # Chunk 25 §3.2: grace_compression_ratio first real observation.
        # Numerator = source_tokens from chunker; denominator = entities+relationships.
        source_tokens = sum(c.token_count_estimate or 0 for c in chunks)
        record_compression_ratio(
            source_tokens=source_tokens,
            entities=len(batch.entities),
            relationships=len(batch.relationships),
            ontology_module=module_name,
            event_id=event_id if verify else None,
            document_id=document_id,
        )

        return batch

    async def _resolve_schema_with_guard(
        self, module_name: str | None
    ) -> tuple[dict, int | None]:
        """Resolve schema with multi-module guard (D65).

        Returns (schema_dict, version_number_or_None).

        - Fewer than 2 registered modules + module_name is None:
          resolve_schema(None) -> returns full active schema_json
        - 2+ modules + module_name is None:
          raise ValueError listing available modules
        - 2+ modules + module_name provided:
          resolve_schema(module_name)
        - Schema not found (None returned): raise ValueError
        """
        if module_name is None:
            available = await self._router.get_available_modules()
            if len(available) >= 2:
                raise ValueError(
                    f"Multiple ontology modules available: {available}. "
                    f"Provide module_name to select one."
                )
        schema_tuple = await self._router.resolve_schema(module_name)
        schema, version = (
            schema_tuple if isinstance(schema_tuple, tuple)
            else (schema_tuple, None)
        )
        if schema is None:
            raise ValueError(
                f"Schema not found for module_name={module_name!r}. "
                f"Check ontology is loaded and active."
            )
        # D546 — module schemas store per-type `properties` as a list; the full
        # schema_json (and every downstream consumer that calls `.keys()`) expects a
        # dict. Normalize once at the resolution boundary so module-scoped extraction
        # works (was: `'list' object has no attribute 'keys'`).
        schema = normalize_property_shape(schema)
        return schema, version

    async def _extract_chunks(
        self,
        chunks: list[DocumentChunk],
        schema: dict,
    ) -> list[ExtractedChunkResult]:
        """Extract all chunks with concurrency limit."""
        semaphore = asyncio.Semaphore(self._config.concurrency_limit)

        async def _extract_one(chunk: DocumentChunk) -> ExtractedChunkResult:
            async with semaphore:
                return await extract_chunk(chunk, schema, self._client, self._config)

        return await asyncio.gather(*[_extract_one(c) for c in chunks])

    @staticmethod
    def _annotate_chunk_sources(
        result: ExtractionResult,
        chunk: DocumentChunk,
    ) -> ExtractionResult:
        """Populate chunk_source_map on extracted entities/relationships.

        Creates (chunk_id, sentence_index) pairs from the entity's
        source_sentence_indices and the chunk's chunk_id.
        """
        annotated_entities = []
        for e in result.entities:
            annotated_entities.append(ExtractedEntity(
                name=e.name,
                entity_type=e.entity_type,
                properties=e.properties,
                source_sentence_indices=e.source_sentence_indices,
                temporal_hints=e.temporal_hints,
                chunk_source_map=[
                    (chunk.chunk_id, idx) for idx in e.source_sentence_indices
                ],
            ))
        annotated_rels = []
        for r in result.relationships:
            annotated_rels.append(ExtractedRelationship(
                subject_name=r.subject_name,
                subject_type=r.subject_type,
                predicate=r.predicate,
                object_name=r.object_name,
                object_type=r.object_type,
                properties=r.properties,
                source_sentence_indices=r.source_sentence_indices,
                temporal_hints=r.temporal_hints,
                chunk_source_map=[
                    (chunk.chunk_id, idx) for idx in r.source_sentence_indices
                ],
            ))
        return ExtractionResult(
            entities=annotated_entities, relationships=annotated_rels
        )

    @staticmethod
    def _normalize_predicate(predicate: str) -> str:
        """Normalize relationship predicate for dedup key: strip + casefold (D62)."""
        return predicate.strip().casefold()

    def _dedup_entities(
        self, entities: list[ExtractedEntity]
    ) -> tuple[list[ExtractedEntity], dict[tuple[str, str], int]]:
        """Merge entities with same (normalized_name, entity_type).

        Returns (deduped_entities, chunk_counts_by_key).

        Merge rules:
        - Union source_sentence_indices (deduplicated, sorted)
        - Concatenate chunk_source_map (preserves chunk provenance)
        - Properties: later values win on key conflict
        - Keep first entity's temporal_hints
        - Keep first entity's name (original casing)
        """
        groups: dict[tuple[str, str], list[ExtractedEntity]] = {}
        for e in entities:
            key = (normalize_entity_name(e.name, self._strip_suffixes), e.entity_type)
            groups.setdefault(key, []).append(e)

        chunk_counts = {key: len(group) for key, group in groups.items()}

        result = []
        for group in groups.values():
            first = group[0]
            merged_props = {}
            merged_indices: set[int] = set()
            merged_chunk_map: list[tuple[str, int]] = []

            for e in group:
                merged_props.update(e.properties)
                merged_indices.update(e.source_sentence_indices)
                merged_chunk_map.extend(e.chunk_source_map)

            result.append(ExtractedEntity(
                name=first.name,
                entity_type=first.entity_type,
                properties=merged_props,
                source_sentence_indices=sorted(merged_indices),
                temporal_hints=first.temporal_hints,
                chunk_source_map=merged_chunk_map,
            ))

        return result, chunk_counts

    def _dedup_relationships(
        self, relationships: list[ExtractedRelationship]
    ) -> tuple[list[ExtractedRelationship], dict[tuple, int]]:
        """Merge relationships with same key.

        Returns (deduped_relationships, chunk_counts_by_key).

        Key: (normalized_subject_name, normalized_predicate,
              normalized_object_name, subject_type, object_type)

        Merge rules:
        - Union source_sentence_indices
        - Concatenate chunk_source_map (preserves chunk provenance)
        - Properties: later values win on key conflict
        - Keep first relationship's temporal_hints and predicate casing
        """
        groups: dict[tuple, list[ExtractedRelationship]] = {}
        for r in relationships:
            key = (
                normalize_entity_name(r.subject_name, self._strip_suffixes),
                self._normalize_predicate(r.predicate),
                normalize_entity_name(r.object_name, self._strip_suffixes),
                r.subject_type,
                r.object_type,
            )
            groups.setdefault(key, []).append(r)

        chunk_counts = {key: len(group) for key, group in groups.items()}

        result = []
        for group in groups.values():
            first = group[0]
            merged_props = {}
            merged_indices: set[int] = set()
            merged_chunk_map: list[tuple[str, int]] = []

            for r in group:
                merged_props.update(r.properties)
                merged_indices.update(r.source_sentence_indices)
                merged_chunk_map.extend(r.chunk_source_map)

            result.append(ExtractedRelationship(
                subject_name=first.subject_name,
                subject_type=first.subject_type,
                predicate=first.predicate,
                object_name=first.object_name,
                object_type=first.object_type,
                properties=merged_props,
                source_sentence_indices=sorted(merged_indices),
                temporal_hints=first.temporal_hints,
                chunk_source_map=merged_chunk_map,
            ))

        return result, chunk_counts


def _apply_resolution_to_entities(
    all_entities: list[ExtractedEntity],
    entities_resolvable: list[ExtractedEntity],
    resolution_results: list[EntityResolutionResult],
    strip_suffixes: list[str],
) -> None:
    """Copy resolution results onto ExtractedEntity objects in-place."""
    by_key: dict[tuple[str, str], EntityResolutionResult] = {}
    for e, result in zip(entities_resolvable, resolution_results):
        key = (normalize_entity_name(e.name, strip_suffixes), e.entity_type)
        by_key[key] = result

    for e in all_entities:
        key = (normalize_entity_name(e.name, strip_suffixes), e.entity_type)
        if key in by_key:
            e.resolved_grace_id = by_key[key].resolved_grace_id
            e.resolution_tier = by_key[key].resolution_tier


def _apply_resolution_to_claims(
    entity_claims: list[Claim],
    resolution_results: list[EntityResolutionResult],
    entities_resolvable: list[ExtractedEntity],
    strip_suffixes: list[str],
) -> None:
    """Set resolved_entity_grace_id and resolution_note on entity claims."""
    by_key: dict[tuple[str, str], EntityResolutionResult] = {}
    for e, result in zip(entities_resolvable, resolution_results):
        key = (normalize_entity_name(e.name, strip_suffixes), e.entity_type)
        by_key[key] = result

    for c in entity_claims:
        key = (normalize_entity_name(c.subject_name, strip_suffixes), c.entity_type)
        if key in by_key:
            r = by_key[key]
            c.resolved_entity_grace_id = r.resolved_grace_id
            c.resolution_note = r.resolution_note


def _compute_er_stats(results: list[EntityResolutionResult]) -> dict:
    """Compute entity resolution summary stats from results."""
    tier_counts: dict[str, int] = {}
    new_count = 0
    matched_count = 0
    for r in results:
        tier_counts[r.resolution_tier] = tier_counts.get(r.resolution_tier, 0) + 1
        if r.is_new:
            new_count += 1
        else:
            matched_count += 1
    total = len(results)
    return {
        "tier_counts": tier_counts,
        "total": total,
        "new_count": new_count,
        "matched_count": matched_count,
        "new_ratio": new_count / total if total > 0 else 0.0,
        "matched_ratio": matched_count / total if total > 0 else 0.0,
    }
