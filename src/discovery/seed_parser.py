"""rdflib parsing of RDF/XML, OWL/XML, Turtle seed files into structured models."""

from pathlib import Path

import rdflib
import structlog
from rdflib import OWL, RDF, RDFS, XSD, Namespace

from src.discovery.seed_models import (
    SeedEntityType,
    SeedProperty,
    SeedReference,
    SeedRelationship,
    SeedSource,
)

logger = structlog.get_logger()

SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
SCHEMA = Namespace("https://schema.org/")

FORMAT_MAP = {
    "rdf_xml": "xml",
    "owl_xml": "xml",
    "turtle": "turtle",
}


def extract_local_name(uri: str) -> str:
    """Extract the local name from a URI (after last # or /).

    Handles fragment identifiers, trailing slashes, and edge cases.
    """
    uri_str = str(uri).rstrip("/")
    if "#" in uri_str:
        return uri_str.rsplit("#", 1)[-1]
    if "/" in uri_str:
        return uri_str.rsplit("/", 1)[-1]
    return uri_str


def _is_xsd_type(uri: rdflib.term.URIRef | None) -> bool:
    """Check if a URI is an XSD datatype."""
    if uri is None:
        return False
    return str(uri).startswith(str(XSD))


def _extract_classes(
    g: rdflib.Graph, source: SeedSource
) -> list[SeedEntityType]:
    """Extract OWL/RDFS classes from the graph."""
    entity_types = []
    seen_uris: set[str] = set()

    # Collect classes from both owl:Class and rdfs:Class
    class_uris = set()
    for cls in g.subjects(RDF.type, OWL.Class):
        class_uris.add(cls)
    for cls in g.subjects(RDF.type, RDFS.Class):
        class_uris.add(cls)

    for cls in class_uris:
        uri_str = str(cls)
        if uri_str in seen_uris:
            continue
        # Skip blank nodes
        if isinstance(cls, rdflib.term.BNode):
            continue
        seen_uris.add(uri_str)

        name = extract_local_name(uri_str)
        if not name:
            continue

        # Get parent type
        parent = g.value(cls, RDFS.subClassOf)
        parent_name = None
        if parent and not isinstance(parent, rdflib.term.BNode):
            parent_name = extract_local_name(str(parent))

        # Get description
        description = ""
        for pred in [RDFS.comment, SKOS.definition]:
            val = g.value(cls, pred)
            if val:
                description = str(val)
                break
        if not description:
            label = g.value(cls, RDFS.label)
            if label and str(label) != name:
                description = str(label)

        entity_types.append(
            SeedEntityType(
                name=name,
                source_ontology=source.source_ontology,
                source_uri=uri_str,
                parent_type=parent_name,
                description=description,
            )
        )

    return entity_types


def _extract_datatype_properties(
    g: rdflib.Graph, source: SeedSource
) -> dict[str, list[SeedProperty]]:
    """Extract datatype properties and map them to their domain classes.

    Returns a dict: domain_uri -> list[SeedProperty].
    """
    props_by_domain: dict[str, list[SeedProperty]] = {}

    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        if isinstance(prop, rdflib.term.BNode):
            continue
        uri_str = str(prop)
        name = extract_local_name(uri_str)
        if not name:
            continue

        range_type = g.value(prop, RDFS.range)
        range_str = str(range_type) if range_type else "xsd:string"
        if range_type and _is_xsd_type(range_type):
            range_str = "xsd:" + extract_local_name(str(range_type))

        desc = ""
        comment = g.value(prop, RDFS.comment)
        if comment:
            desc = str(comment)

        domain = g.value(prop, RDFS.domain)
        domain_key = str(domain) if domain and not isinstance(domain, rdflib.term.BNode) else "__unattached__"

        seed_prop = SeedProperty(
            name=name, uri=uri_str, range_type=range_str, description=desc
        )
        props_by_domain.setdefault(domain_key, []).append(seed_prop)

    return props_by_domain


def _extract_object_properties(
    g: rdflib.Graph, source: SeedSource
) -> list[SeedRelationship]:
    """Extract object properties as relationships."""
    relationships = []

    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        if isinstance(prop, rdflib.term.BNode):
            continue
        uri_str = str(prop)
        name = extract_local_name(uri_str)
        if not name:
            continue

        domain = g.value(prop, RDFS.domain)
        range_val = g.value(prop, RDFS.range)

        # Skip if domain or range is missing or is a blank node or XSD type
        if not domain or isinstance(domain, rdflib.term.BNode):
            continue
        if not range_val or isinstance(range_val, rdflib.term.BNode):
            continue
        if _is_xsd_type(range_val):
            continue

        desc = ""
        comment = g.value(prop, RDFS.comment)
        if comment:
            desc = str(comment)

        relationships.append(
            SeedRelationship(
                name=name,
                source_ontology=source.source_ontology,
                source_uri=uri_str,
                domain_type=extract_local_name(str(domain)),
                range_type=extract_local_name(str(range_val)),
                description=desc,
            )
        )

    return relationships


def _extract_restriction_relationships(
    g: rdflib.Graph, source: SeedSource, entity_type_uris: set[str]
) -> list[SeedRelationship]:
    """Extract relationships from OWL Restrictions (FIBO/LKIF pattern).

    Walks named classes with rdfs:subClassOf pointing to blank nodes that are
    owl:Restriction with owl:onProperty + owl:someValuesFrom.
    """
    relationships = []
    seen: set[tuple[str, str, str]] = set()

    # Collect all named classes
    named_classes = set()
    for cls in g.subjects(RDF.type, OWL.Class):
        if not isinstance(cls, rdflib.term.BNode):
            named_classes.add(cls)

    for cls in named_classes:
        for parent in g.objects(cls, RDFS.subClassOf):
            if not isinstance(parent, rdflib.term.BNode):
                continue
            on_prop = g.value(parent, OWL.onProperty)
            some_vals = g.value(parent, OWL.someValuesFrom)
            if on_prop and some_vals and not isinstance(some_vals, rdflib.term.BNode):
                domain_name = extract_local_name(str(cls))
                range_name = extract_local_name(str(some_vals))
                prop_name = extract_local_name(str(on_prop))

                key = (prop_name, domain_name, range_name)
                if key in seen:
                    continue
                seen.add(key)

                desc = ""
                comment = g.value(on_prop, RDFS.comment)
                if comment:
                    desc = str(comment)

                relationships.append(
                    SeedRelationship(
                        name=prop_name,
                        source_ontology=source.source_ontology,
                        source_uri=str(on_prop),
                        domain_type=domain_name,
                        range_type=range_name,
                        description=desc,
                    )
                )

    return relationships


# Schema.org primitive types to skip as relationship ranges
_SCHEMA_ORG_PRIMITIVES = {
    "Text", "Boolean", "Number", "Integer", "Float",
    "Date", "DateTime", "Time", "URL", "DataType", "QuantitativeValue",
}


def _extract_schema_org_properties(
    g: rdflib.Graph, source: SeedSource, included_type_names: set[str]
) -> list[SeedRelationship]:
    """Extract Schema.org properties using domainIncludes/rangeIncludes pattern.

    Only keeps relationships where both domain and range are in our extracted types.
    Caps at 30 relationships.
    """
    relationships = []

    for prop in g.subjects(RDF.type, RDF.Property):
        if isinstance(prop, rdflib.term.BNode):
            continue

        domains = list(g.objects(prop, SCHEMA.domainIncludes))
        ranges = list(g.objects(prop, SCHEMA.rangeIncludes))

        if not domains or not ranges:
            continue

        # Find first domain that's in our included types
        domain_name = None
        for d in domains:
            name = extract_local_name(str(d))
            if name in included_type_names:
                domain_name = name
                break

        if not domain_name:
            continue

        # Find first non-primitive range that's in our included types
        range_name = None
        for r in ranges:
            name = extract_local_name(str(r))
            if name in _SCHEMA_ORG_PRIMITIVES:
                continue
            if name in included_type_names:
                range_name = name
                break

        if not range_name:
            continue

        prop_name = extract_local_name(str(prop))
        desc = ""
        comment = g.value(prop, RDFS.comment)
        if comment:
            desc = str(comment)

        relationships.append(
            SeedRelationship(
                name=prop_name,
                source_ontology=source.source_ontology,
                source_uri=str(prop),
                domain_type=domain_name,
                range_type=range_name,
                description=desc,
            )
        )

    # Cap at 30, sorted by name for determinism
    relationships.sort(key=lambda r: r.name)
    return relationships[:30]


def _filter_schema_org(
    g: rdflib.Graph, base_type_names: list[str]
) -> set[rdflib.term.URIRef]:
    """Filter Schema.org classes to only those under configured base types.

    Returns the set of class URIs to include (capped at 30).
    """
    # Resolve base type URIs
    base_uris = set()
    for name in base_type_names:
        base_uris.add(SCHEMA[name])

    # Build parent map: child -> set of parents
    parent_map: dict[rdflib.term.URIRef, set[rdflib.term.URIRef]] = {}
    for cls in g.subjects(RDF.type, RDFS.Class):
        if isinstance(cls, rdflib.term.BNode):
            continue
        parents = set()
        for parent in g.objects(cls, RDFS.subClassOf):
            if not isinstance(parent, rdflib.term.BNode):
                parents.add(parent)
        parent_map[cls] = parents

    # Walk subClassOf chains to find classes under base types
    cache: dict[rdflib.term.URIRef, bool] = {}

    def is_under_base(uri: rdflib.term.URIRef, visited: set | None = None) -> bool:
        if uri in cache:
            return cache[uri]
        if uri in base_uris:
            cache[uri] = True
            return True
        if visited is None:
            visited = set()
        if uri in visited:
            cache[uri] = False
            return False
        visited.add(uri)
        parents = parent_map.get(uri, set())
        result = any(is_under_base(p, visited) for p in parents)
        cache[uri] = result
        return result

    included = set()
    for cls in parent_map:
        if is_under_base(cls):
            included.add(cls)

    # Cap at 30 — if too many, take only direct subclasses of base types
    if len(included) > 30:
        logger.warning(
            "schema_org_cap_exceeded",
            total=len(included),
            message="Falling back to direct subclasses only",
        )
        direct = set()
        for cls, parents in parent_map.items():
            if parents & base_uris:
                direct.add(cls)
        # Also include the base types themselves
        direct |= {uri for uri in base_uris if uri in parent_map}
        included = direct
        # Still cap at 30 if direct subclasses exceed it
        if len(included) > 30:
            included = set(list(included)[:30])

    return included


def parse_seed_file(
    source: SeedSource, config: dict
) -> tuple[list[SeedEntityType], list[SeedRelationship]]:
    """Parse a single seed file and return extracted entity types and relationships.

    Args:
        source: The SeedSource describing the file to parse.
        config: The discovery config dict (for schema_org_base_types etc).

    Returns:
        Tuple of (entity_types, relationships).
    """
    file_path = Path(source.local_path)
    if not file_path.exists():
        logger.error("seed_file_not_found", path=str(file_path))
        return [], []

    fmt = FORMAT_MAP.get(source.file_format)
    if fmt is None:
        logger.error("unknown_seed_format", format=source.file_format)
        return [], []

    g = rdflib.Graph()
    try:
        g.parse(str(file_path), format=fmt)
    except Exception as e:
        logger.error("seed_parse_error", path=str(file_path), error=str(e))
        return [], []

    logger.info(
        "seed_file_parsed",
        source_id=source.id,
        triples=len(g),
        format=fmt,
    )

    # Schema.org filtering
    schema_org_filter: set | None = None
    if source.source_ontology == "schema_org":
        seed_config = config.get("seed", {})
        base_types = seed_config.get(
            "schema_org_base_types",
            ["Organization", "Person", "Place", "Event", "CreativeWork", "Product"],
        )
        schema_org_filter = _filter_schema_org(g, base_types)

    # Extract classes
    entity_types = _extract_classes(g, source)

    # Apply Schema.org filter
    if schema_org_filter is not None:
        entity_types = [
            et for et in entity_types
            if rdflib.term.URIRef(et.source_uri) in schema_org_filter
        ]

    # Extract datatype properties and attach to entity types
    props_by_domain = _extract_datatype_properties(g, source)
    for et in entity_types:
        et.properties = props_by_domain.get(et.source_uri, [])

    # Strategy 1: Extract standalone owl:ObjectProperty relationships
    relationships = _extract_object_properties(g, source)

    # Strategy 2: Extract from OWL restrictions (FIBO, LKIF)
    entity_type_uris = {et.source_uri for et in entity_types}
    restriction_rels = _extract_restriction_relationships(g, source, entity_type_uris)
    relationships.extend(restriction_rels)

    # Strategy 3: Extract Schema.org properties (only for schema_org sources)
    if source.source_ontology == "schema_org":
        included_names = {et.name for et in entity_types}
        schema_rels = _extract_schema_org_properties(g, source, included_names)
        relationships.extend(schema_rels)

    # If Schema.org filter is active, filter Strategy 1 relationships too
    if schema_org_filter is not None:
        included_names = {et.name for et in entity_types}
        relationships = [
            r for r in relationships
            if r.domain_type in included_names or r.range_type in included_names
        ]

    # Deduplicate all relationships by (name, domain_type, range_type)
    seen_rels: set[tuple[str, str, str]] = set()
    unique_rels = []
    for r in relationships:
        key = (r.name, r.domain_type, r.range_type)
        if key not in seen_rels:
            seen_rels.add(key)
            unique_rels.append(r)
    relationships = unique_rels

    logger.info(
        "seed_extraction_complete",
        source_id=source.id,
        entity_types=len(entity_types),
        relationships=len(relationships),
    )

    return entity_types, relationships


def format_for_llm(seed_ref: SeedReference) -> str:
    """Convert a SeedReference into a formatted text block for LLM prompt injection.

    Groups entity types by source_ontology, includes hierarchy and properties.
    Target: under 4000 tokens when serialized.
    """
    lines: list[str] = []
    lines.append("=== Seed Ontology Reference ===")
    lines.append(f"Industry: {seed_ref.industry_profile}")
    lines.append(f"Entity types: {seed_ref.total_entity_types}, Relationships: {seed_ref.total_relationships}")
    lines.append("")

    # Build set of types referenced in relationships (for filtering low-value types)
    rel_types: set[str] = set()
    for r in seed_ref.relationships:
        rel_types.add(r.domain_type)
        rel_types.add(r.range_type)

    # Group by source ontology
    by_ontology: dict[str, list[SeedEntityType]] = {}
    for et in seed_ref.entity_types:
        by_ontology.setdefault(et.source_ontology, []).append(et)

    for ontology, types in by_ontology.items():
        lines.append(f"--- {ontology.upper()} ---")
        included_count = 0
        for et in types:
            # Slim 3: skip low-value types (no properties, no description, not in relationships)
            has_value = et.properties or et.description or et.name in rel_types
            if not has_value:
                continue
            parent = f" (subclass of {et.parent_type})" if et.parent_type else ""
            props = ""
            if et.properties:
                prop_names = ", ".join(p.name for p in et.properties[:5])
                props = f" [{prop_names}]"
            lines.append(f"  {et.name}{parent}{props}")
            included_count += 1
        if included_count == 0:
            lines.append(f"  ({len(types)} types)")
        lines.append("")

    # Relationships
    rels_by_ontology: dict[str, list[SeedRelationship]] = {}
    for r in seed_ref.relationships:
        rels_by_ontology.setdefault(r.source_ontology, []).append(r)

    if seed_ref.relationships:
        lines.append("--- Relationships ---")
        for ontology, rels in rels_by_ontology.items():
            # Cap at 15 relationships per ontology for brevity
            for r in rels[:15]:
                lines.append(f"  {r.domain_type} --{r.name}--> {r.range_type} [{ontology}]")
            if len(rels) > 15:
                lines.append(f"  ... and {len(rels) - 15} more [{ontology}]")
        lines.append("")

    return "\n".join(lines)
