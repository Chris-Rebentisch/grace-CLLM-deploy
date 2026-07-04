"""Entity-to-text conversion for retrieval indexing.

Builds rich text representations from entity properties using a generic
template pattern: "{type}: {name}. {description_fragment}. Key properties: {k=v}."
The description fragment is assembled from well-known property names
(description, jurisdiction, status, type) when present.
"""

from __future__ import annotations

from typing import Any


# System fields excluded from text representation.
# Phase-5 fix: previously this set only excluded the Arcade `@*` system
# properties + grace_id; the 768-dim `_embedding` vector and the per-vertex
# provenance/audit/temporal fields all leaked into the "Key properties"
# stringification, producing ~10 K-char text representations that exceed
# `nomic-embed-text`'s 8 K-token context window. Excluding them keeps
# corpus texts compact and semantically meaningful.
_SYSTEM_KEYS = {
    "@rid", "@type", "@cat", "@in", "@out",
    "grace_id", "entity_type",
    "_embedding", "_deprecated",
    # Provenance / temporal / governance fields written by graph_writer:
    "extraction_event_id", "source_document_id", "evidence_origin",
    "extracted_at", "extraction_confidence", "human_validated",
    "_valid_from", "_valid_to",
}

# Well-known properties promoted to description fragment
_DESCRIPTION_KEYS = {"name", "description", "jurisdiction", "status", "type"}


def entity_to_text(entity_type: str, properties: dict[str, Any]) -> str:
    """Convert entity properties to searchable text string.

    Output format: "{Type}: {Name}. {description}. jurisdiction: {j}. status: {s}.
    type: {t}. Key properties: k1=v1, k2=v2."

    Well-known properties (description, jurisdiction, status, type) are promoted
    to the description fragment. Remaining non-system properties appear as
    key=value pairs.
    """
    name = properties.get("name", "unknown")

    # Build description fragment from well-known properties
    desc_parts: list[str] = []
    if desc := properties.get("description"):
        desc_parts.append(str(desc))
    if jurisdiction := properties.get("jurisdiction"):
        desc_parts.append(f"jurisdiction: {jurisdiction}")
    if status := properties.get("status"):
        desc_parts.append(f"status: {status}")
    if type_val := properties.get("type"):
        desc_parts.append(f"type: {type_val}")

    desc_fragment = ". ".join(desc_parts) if desc_parts else ""

    # Key properties: exclude name, description fragment keys, and system fields
    used_keys = _DESCRIPTION_KEYS | _SYSTEM_KEYS
    kv_parts = [
        f"{k}={v}" for k, v in properties.items()
        if k not in used_keys and v is not None
    ]
    kv_str = ", ".join(kv_parts)

    # Assemble: "{Type}: {Name}. {Description}. Key properties: {k=v}."
    parts = [f"{entity_type}: {name}"]
    if desc_fragment:
        parts.append(desc_fragment)
    if kv_str:
        parts.append(f"Key properties: {kv_str}")

    return ". ".join(parts) + "."


def build_text_corpus(entities: list[dict]) -> list[tuple[str, str]]:
    """Build (grace_id, text) pairs for all entities in the graph.

    Used to populate BM25 index and semantic embedding store.
    Returns list of (grace_id, text_representation) tuples.

    Each entity dict is expected to have grace_id, @type (or entity_type), and properties.
    """
    corpus: list[tuple[str, str]] = []
    for entity in entities:
        grace_id = entity.get("grace_id", "")
        entity_type = entity.get("@type", entity.get("entity_type", "Entity"))
        # Build properties dict excluding system fields
        props = {
            k: v
            for k, v in entity.items()
            if k not in _SYSTEM_KEYS and v is not None
        }
        text = entity_to_text(entity_type, props)
        corpus.append((grace_id, text))
    return corpus
