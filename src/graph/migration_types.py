"""Meta-entity and meta-edge types for the provenance layer.

Migration_Event and Correction_Event track schema evolution
and data corrections. Extraction_Event tracks extraction batches.
produced_by edges link domain entities to their Extraction_Event.
"""

MIGRATION_EVENT_PROPERTIES: list[dict] = [
    {"name": "migration_id", "type": "STRING"},
    {"name": "from_version", "type": "INTEGER"},
    {"name": "to_version", "type": "INTEGER"},
    {"name": "ddl_executed_count", "type": "INTEGER"},
    {"name": "ddl_failed_count", "type": "INTEGER"},
    {"name": "types_added", "type": "STRING"},  # JSON array as string
    {"name": "types_deprecated", "type": "STRING"},  # JSON array as string
    {"name": "properties_added", "type": "STRING"},  # JSON array as string
    {"name": "kgcl_commands", "type": "STRING"},  # JSON array as string
    {"name": "migrated_at", "type": "DATETIME"},
    {"name": "migrated_by", "type": "STRING"},  # "system" or reviewer name
    {"name": "status", "type": "STRING"},  # success | partial | failed
]

CORRECTION_EVENT_PROPERTIES: list[dict] = [
    {"name": "correction_id", "type": "STRING"},
    {"name": "corrected_entity_rid", "type": "STRING"},  # ArcadeDB RID
    {"name": "corrected_type", "type": "STRING"},
    {"name": "corrected_properties", "type": "STRING"},  # JSON
    {"name": "original_values", "type": "STRING"},  # JSON
    {"name": "new_values", "type": "STRING"},  # JSON
    {"name": "corrector_id", "type": "STRING"},
    {"name": "correction_timestamp", "type": "DATETIME"},
    {"name": "correction_reasoning", "type": "STRING"},
]

EXTRACTION_EVENT_PROPERTIES: list[dict] = [
    {"name": "extraction_event_id", "type": "STRING"},
    {"name": "batch_id", "type": "STRING"},
    {"name": "source_document_id", "type": "STRING"},
    {"name": "ontology_module", "type": "STRING"},
    {"name": "schema_version", "type": "INTEGER"},
    {"name": "extractor_model", "type": "STRING"},
    {"name": "verifier_model", "type": "STRING"},
    {"name": "prompt_template_id", "type": "STRING"},
    {"name": "avg_confidence", "type": "DOUBLE"},
    {"name": "entities_created", "type": "INTEGER"},
    {"name": "entities_matched", "type": "INTEGER"},
    {"name": "relationships_created", "type": "INTEGER"},
    {"name": "claims_accepted", "type": "INTEGER"},
    {"name": "claims_quarantined", "type": "INTEGER"},
    {"name": "started_at", "type": "DATETIME"},
    {"name": "completed_at", "type": "DATETIME"},
    {"name": "status", "type": "STRING"},
]

# D267 (Chunk 35b) — Query/Response meta-entity vertex types.
# D349 (Chunk 43, post-FINAL amendment 2026-05-10) — Sensitivity Gate audit
# annotation. ``sensitivity_tags`` carries the active-matrix tag set as a
# bar-delimited STRING (``"|tag1|tag2|"``) so OpenCypher CONTAINS filters
# remain exact (no JSON-substring prefix collisions, no LIST_OF_STRING
# precedent in this codebase). ``sensitivity_tags_matrix_id`` carries the
# matrix UUID as a STRING for SOC 2 fidelity (Q3). Empty-list vertices
# omit both fields entirely (``build_property_map`` skips ``None``); the
# audit-trail filter route must default-deny when either is missing.
QUERY_EVENT_PROPERTIES: list[dict] = [
    {"name": "query_event_id", "type": "STRING"},
    {"name": "query_text", "type": "STRING"},
    {"name": "query_timestamp", "type": "DATETIME"},
    {"name": "session_id", "type": "STRING"},
    {"name": "retrieval_mode", "type": "STRING"},
    {"name": "strategies_fired", "type": "STRING"},  # JSON array as string
    {"name": "total_candidates", "type": "INTEGER"},
    # D349 — Sensitivity Gate audit annotation (Chunk 43 CP5).
    {"name": "sensitivity_tags", "type": "STRING"},  # bar-delimited; e.g. "|pii|finance|"
    {"name": "sensitivity_tags_matrix_id", "type": "STRING"},  # UUID of active matrix
    # D377 (Chunk 45 CP5) — support session audit stamp.
    {"name": "support_session_id", "type": "STRING"},
    # D398 (Chunk 50 CP4) — agent daemon audit stamp.
    {"name": "agent_id", "type": "STRING"},
]

RESPONSE_EVENT_PROPERTIES: list[dict] = [
    {"name": "response_event_id", "type": "STRING"},
    {"name": "query_event_id", "type": "STRING"},  # back-reference
    {"name": "result_count", "type": "INTEGER"},
    {"name": "serialization_format", "type": "STRING"},
    {"name": "latency_ms_total", "type": "DOUBLE"},
    {"name": "created_at", "type": "DATETIME"},
]

META_ENTITY_TYPES: dict[str, list[dict]] = {
    "Migration_Event": MIGRATION_EVENT_PROPERTIES,
    "Correction_Event": CORRECTION_EVENT_PROPERTIES,
    "Extraction_Event": EXTRACTION_EVENT_PROPERTIES,
    # D267 — Chunk 35b
    "Query_Event": QUERY_EVENT_PROPERTIES,
    "Response_Event": RESPONSE_EVENT_PROPERTIES,
    # D463 (Chunk 71 CP1) — Document_Chunk provenance-layer vertex type.
    # Invariant: META_ENTITY_TYPES governs DDL-sync.
    # Carve-out: Document_Chunk is a provenance-layer type (not domain);
    #   sits between domain entity layer and raw document layer — chunks are
    #   the extraction unit from which domain entities are derived.
    # Authorization: D463.
    "Document_Chunk": [
        {"name": "grace_id", "type": "STRING"},
        {"name": "source_document_id", "type": "STRING"},
        {"name": "chunk_index", "type": "INTEGER"},
        {"name": "text", "type": "STRING"},
        {"name": "chunk_token_count", "type": "INTEGER"},
        {"name": "_embedding", "type": "LIST"},
        {"name": "extracted_at", "type": "DATETIME"},
        {"name": "sensitivity_tags", "type": "STRING"},
        {"name": "_deprecated", "type": "BOOLEAN"},
    ],
    # D501 (Chunk 77b CP2) — Image_Asset provenance-layer vertex type.
    # Invariant: META_ENTITY_TYPES governs DDL-sync.
    # Carve-out: Image_Asset is a provenance-layer type (not domain);
    #   sits at the raw-media layer — photos and scanned images from which
    #   domain entities and Document_Chunks may be derived.
    # Authorization: D501.
    "Image_Asset": [
        {"name": "grace_id", "type": "STRING"},
        {"name": "source_path", "type": "STRING"},
        {"name": "content_sha256", "type": "STRING"},
        {"name": "media_type", "type": "STRING"},
        {"name": "image_class", "type": "STRING"},
        {"name": "ocr_text", "type": "STRING"},
        {"name": "vision_description_json", "type": "STRING"},
        {"name": "sensitivity_tags", "type": "STRING"},
        {"name": "_embedding", "type": "LIST"},
        {"name": "extracted_at", "type": "DATETIME"},
    ],
    # D398 (Chunk 50 CP4) — agent daemon governance audit vertex.
    "GovernanceDecision_Event": [
        {"name": "grace_id", "type": "STRING"},
        {"name": "decision_type", "type": "STRING"},
        {"name": "agent_id", "type": "STRING"},
        {"name": "proposal_id", "type": "STRING"},
        {"name": "schema_version_id", "type": "STRING"},
        {"name": "tier", "type": "INTEGER"},
        {"name": "trust_score_at_time", "type": "DOUBLE"},
        {"name": "outcome", "type": "STRING"},
        {"name": "reason", "type": "STRING"},
        {"name": "recorded_at", "type": "DATETIME"},
    ],
}
"""Map of meta-entity type names to their property definitions.

Used by ddl_generator.generate_full_schema_ddl() to include
provenance layer types in the complete schema DDL.
"""

META_EDGE_TYPES: dict[str, list[dict]] = {
    "produced_by": [
        {"name": "grace_id", "type": "STRING"},
        {"name": "created_at", "type": "DATETIME"},
    ],
    # D267 — Chunk 35b: Query_Event -> domain entity audit edge.
    "retrieved_from": [
        {"name": "grace_id", "type": "STRING"},
        {"name": "created_at", "type": "DATETIME"},
        {"name": "query_event_id", "type": "STRING"},
        {"name": "rank_ordinal", "type": "INTEGER"},
    ],
    # Chunk 51 (D402/D404): federation bridge edge types (META_EDGE_TYPES 2→4).
    # Invariant: META_EDGE_TYPES count 2→4.
    # Authorization source: chunk-51-spec-v4-FINAL.md §2.3.
    "Bridge_Entity": [
        {"name": "grace_id", "type": "STRING"},
        {"name": "canonical_grace_id", "type": "STRING"},
        {"name": "child_grace_id", "type": "STRING"},
        {"name": "namespace", "type": "STRING"},
        {"name": "resolution_method", "type": "STRING"},
        {"name": "resolved_at", "type": "DATETIME"},
    ],
    "Cross_System_Reference": [
        {"name": "grace_id", "type": "STRING"},
        {"name": "relationship_type", "type": "STRING"},
        {"name": "confidence_band", "type": "STRING"},  # D120/D217: high/medium/low only
        {"name": "evidence_source", "type": "STRING"},
        {"name": "created_at", "type": "DATETIME"},
    ],
    # D464 (Chunk 71 CP2) — derives_from provenance edge.
    # Invariant: META_EDGE_TYPES governs DDL-sync.
    # Carve-out: derives_from links Domain entities to source Document_Chunk
    #   vertices, closing the provenance gap for chunk-level retrieval.
    # Authorization: D464.
    "derives_from": [
        {"name": "grace_id", "type": "STRING"},
        {"name": "created_at", "type": "DATETIME"},
    ],
}
"""Map of meta-edge type names to their property definitions.

Used by ddl_generator.generate_meta_entity_ddl() to include
provenance layer edge types in the complete schema DDL.
"""
