"""Four-layer system properties added to every vertex and edge type.

These are GrACE infrastructure properties, not part of the domain ontology.
They are declared in the ArcadeDB schema (typed, validated).
"""

VERTEX_SYSTEM_PROPERTIES: list[dict] = [
    # Identity layer
    {"name": "grace_id", "type": "STRING", "description": "GrACE-generated UUID, stable across DB operations"},
    # Temporal layer
    {"name": "valid_from", "type": "DATETIME", "description": "When this fact became valid"},
    {"name": "valid_to", "type": "DATETIME", "description": "When this fact ceased to be valid"},
    {"name": "extracted_at", "type": "DATETIME", "description": "When this was extracted"},
    # Provenance layer
    {"name": "extraction_confidence", "type": "DOUBLE", "description": "0.0-1.0 confidence score"},
    {"name": "source_document_id", "type": "STRING", "description": "Document that produced this entity"},
    {"name": "extraction_event_id", "type": "STRING", "description": "Extraction_Event that created this"},
    {"name": "schema_version", "type": "INTEGER", "description": "Ontology version active at extraction"},
    {"name": "ontology_module", "type": "STRING", "description": "Which ontology module governed extraction"},
    # Governance layer
    {"name": "human_validated", "type": "BOOLEAN", "description": "Whether a human has validated this"},
    {"name": "validation_timestamp", "type": "DATETIME", "description": "When validation occurred"},
    {"name": "validator_id", "type": "STRING", "description": "Who validated this"},
    # Deprecation layer
    {"name": "_deprecated", "type": "BOOLEAN", "description": "Whether this type/entity is deprecated"},
    {"name": "_deprecated_at", "type": "DATETIME", "description": "When deprecation occurred"},
    # Supersession layer
    # D514 — `superseded_by` is NET-NEW; brief's 'reuse existing' was inaccurate
    # (verified: no graph `superseded_by` exists — only in `src/change_directives/`).
    # See research Subject 2. Authorization: D514.
    {"name": "superseded_by", "type": "STRING", "description": "grace_id of the entity that supersedes this one"},
    # Corroboration layer
    # D517 — per-entity trust label for email corroboration. D274 segment-level gate
    # NOT bypassed. Authorization: D517.
    {"name": "corroboration_status", "type": "STRING", "description": "Corroboration trust label: first_class or provisional"},
    {"name": "corroborating_sender_count", "type": "INTEGER", "description": "Count of distinct resolved Persons corroborating this entity"},
    # D519 — access-control vertex property for privilege governance;
    # format per D344/D440; D466 Document_Chunk precedent mirrored for domain entities.
    {"name": "sensitivity_tags", "type": "STRING", "description": "Bar-form sensitivity tags (D519)"},
    # Sensitivity-provenance layer — F-0047b / ISS-0055 Layer 1 (2026-07-03).
    # Capture-the-why (D356 discipline): the D520 vertex-global tag union is
    # irreversible and provenance-free, so ONE privileged email touching a
    # shared canonical entity made it vanish ENTIRELY for restricted
    # principals (F-0047b). These two properties record, at write
    # time, WHICH sources contributed each tag and HOW MANY writes touched
    # the vertex overall, enabling the Layer-2 evidence_scoped posture to
    # distinguish universally-privileged vertices from partially-inherited
    # ones. INVARIANT-COUNT CHANGE: VERTEX_SYSTEM_PROPERTIES 18 -> 20.
    {"name": "sensitivity_tag_sources", "type": "STRING", "description": "JSON map tag -> {ids: [source_document_ids, cap 20], overflow: int, count: writes carrying tag} (ISS-0055 Layer 1)"},
    {"name": "sensitivity_source_total", "type": "INTEGER", "description": "Count of extraction writes/merges (tagged or not) that carried a source_document_id (ISS-0055 Layer 1)"},
]

EDGE_SYSTEM_PROPERTIES: list[dict] = [
    # Identity layer
    {"name": "grace_id", "type": "STRING", "description": "GrACE-generated UUID, stable across DB operations"},
    # Temporal layer
    {"name": "valid_from", "type": "DATETIME", "description": "When this fact became valid"},
    {"name": "valid_to", "type": "DATETIME", "description": "When this fact ceased to be valid"},
    {"name": "extracted_at", "type": "DATETIME", "description": "When this was extracted"},
    # Provenance layer
    {"name": "extraction_confidence", "type": "DOUBLE", "description": "0.0-1.0 confidence score"},
    {"name": "source_document_id", "type": "STRING", "description": "Document that produced this entity"},
    {"name": "extraction_event_id", "type": "STRING", "description": "Extraction_Event that created this"},
    {"name": "schema_version", "type": "INTEGER", "description": "Ontology version active at extraction"},
    {"name": "ontology_module", "type": "STRING", "description": "Which ontology module governed extraction"},
    # Governance layer
    {"name": "human_validated", "type": "BOOLEAN", "description": "Whether a human has validated this"},
    {"name": "validation_timestamp", "type": "DATETIME", "description": "When validation occurred"},
    {"name": "validator_id", "type": "STRING", "description": "Who validated this"},
    # Deprecation layer
    {"name": "_deprecated", "type": "BOOLEAN", "description": "Whether this type/entity is deprecated"},
    {"name": "_deprecated_at", "type": "DATETIME", "description": "When deprecation occurred"},
    # Supersession layer
    # D514 — `superseded_by` NET-NEW on edges (mirrors vertex-side). Authorization: D514.
    {"name": "superseded_by", "type": "STRING", "description": "grace_id of the edge that supersedes this one"},
    # Edge-specific
    {"name": "relationship_confidence", "type": "DOUBLE", "description": "Confidence in this relationship"},
    # F-0047b / ISS-0055 Layer 2 (2026-07-03) — capture-the-why (D356):
    # edges inherit source sensitivity_tags the same way vertices do
    # (D519/D520 was vertex-only). Prerequisite for the evidence_scoped
    # enforcement posture: a relationship claim extracted from a privileged
    # email previously produced an UNTAGGED edge that would leak the moment
    # its endpoints became visible. INVARIANT-COUNT CHANGE:
    # EDGE_SYSTEM_PROPERTIES 16 -> 17.
    {"name": "sensitivity_tags", "type": "STRING", "description": "Bar-form sensitivity tags inherited from the contributing source (ISS-0055 Layer 2)"},
]
