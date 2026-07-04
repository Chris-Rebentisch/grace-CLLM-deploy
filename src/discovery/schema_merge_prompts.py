"""Prompts for schema merge LLM calls: canonical naming, hierarchy, Stage D edge detection."""

import json


ENTITY_CANONICALIZATION_SYSTEM = """You are an ontology engineer merging entity types proposed by three different analysis passes
and seed reference patterns. For each cluster of equivalent types, you must:
1. Choose the best canonical name (prefer seed-aligned names when quality is equal)
2. Resolve hierarchy: determine the correct parent_type for each type
3. Merge properties: union all properties from all passes, resolve naming conflicts
4. Resolve naming conflicts between passes
Respond with ONLY valid JSON. Do NOT wrap your response in markdown code fences (no ```json blocks)."""

EDGE_DETECTION_SYSTEM = """You are an ontology engineer classifying relationship types by their richness tier.
For each relationship, evaluate:
1. VARIABILITY TEST: If the same source connects to multiple targets via this relationship,
   do qualifying values differ per connection? If yes → edge properties exist.
2. COUNT TEST: 0 properties = Simple. 1-3 = Attributed. 4+ = likely Reified.
3. REFERENCE TEST: Would someone refer to this relationship instance independently?
   If yes → Reified.

DOMAIN EXAMPLES — apply these patterns to the relationships you classify:
- Simple: "incorporated_in (Legal_Entity → Jurisdiction)" — just a connection, no qualifying
  data varies per instance. The entity is either incorporated there or not.
- Attributed: "covers (Insurance_Policy → Development_Project)" — coverage_amount, deductible,
  effective_date vary per coverage arrangement. The same policy may cover multiple projects at
  different amounts. 1-3 qualifying properties belong on the edge.
- Reified: "Employment (Person → Organization)" — title, start_date, end_date, compensation,
  department. This relationship has enough properties to be its own entity. Someone would say
  "the employment arrangement between X and Y."

PITFALLS — avoid these common mistakes:
- Do NOT classify all relationships as simple. If a relationship connects two entities where
  qualifying values could differ per connection, it is attributed.
- If you are unsure, classify as attributed rather than simple. It is easier to demote during
  review than to discover missing edge properties later.
- Look at the source and target entity properties provided. If the source has properties that
  only make sense in the context of a specific target connection (e.g., coverage_amount on a
  policy that covers multiple things), those properties likely belong on the edge.

Classify each relationship as: simple, attributed, or reified.
For attributed/reified, list the specific edge properties with their data types.
Respond with ONLY valid JSON. Do NOT wrap your response in markdown code fences (no ```json blocks)."""


def _format_cluster_for_entity_prompt(cluster_label: int, members: list[dict]) -> str:
    """Format a single entity type cluster for the canonicalization prompt."""
    lines = [f"  Cluster {cluster_label}:"]
    for m in members:
        source = m.get("source_pass", "unknown")
        name = m.get("name", "?")
        parent = m.get("parent_type") or "(none)"
        seed_align = m.get("seed_alignment") or "(none)"
        props = m.get("properties", [])
        prop_names = [p.get("name", "?") if isinstance(p, dict) else getattr(p, "name", "?") for p in props[:5]]
        lines.append(f"    - [{source}] {name} (parent: {parent}, seed: {seed_align}, props: {prop_names})")
    return "\n".join(lines)


ENTITY_RESPONSE_SCHEMA = json.dumps(
    {
        "resolved_types": [
            {
                "cluster_label": 0,
                "canonical_name": "Legal_Entity",
                "parent_type": None,
                "description": "An organization or person with legal standing",
                "properties": [
                    {
                        "name": "jurisdiction",
                        "data_type": "string",
                        "description": "Legal jurisdiction",
                        "required": True,
                    }
                ],
                "hierarchy_rationale": "Legal_Entity is the broadest category containing all legal persons",
            }
        ]
    },
    indent=2,
)


def build_entity_canonicalization_prompt(
    entity_clusters: dict[int, list[dict]],
) -> tuple[str, str]:
    """Build prompt for LLM entity type canonicalization and hierarchy resolution.

    Args:
        entity_clusters: Mapping from cluster_label to list of member dicts.

    Returns:
        (system_prompt, user_prompt)
    """
    cluster_texts = []
    for label, members in sorted(entity_clusters.items()):
        cluster_texts.append(_format_cluster_for_entity_prompt(label, members))

    user_prompt = f"""Resolve the following clusters of equivalent entity types.
For each cluster, choose the best canonical name, resolve the hierarchy (parent_type),
and merge all properties from all passes (union strategy — include all, flag conflicts).

{chr(10).join(cluster_texts)}

RESPONSE FORMAT:
{ENTITY_RESPONSE_SCHEMA}"""

    return ENTITY_CANONICALIZATION_SYSTEM, user_prompt


def _format_rel_for_edge_prompt(
    cluster_label: int,
    members: list[dict],
    entity_context: dict[str, dict] | None = None,
) -> str:
    """Format a single relationship cluster for Stage D prompt.

    Args:
        cluster_label: Cluster identifier.
        members: Relationship dicts in the cluster.
        entity_context: Optional mapping from entity name to dict with
            'description' and 'properties' keys for source/target context.
    """
    lines = [f"  Cluster {cluster_label}:"]
    for m in members:
        source = m.get("source_pass", "unknown")
        name = m.get("name", "?")
        src_type = m.get("source_type", "?")
        tgt_type = m.get("target_type", "?")
        hint = m.get("richness_hint", "simple")
        edge_props = m.get("edge_properties", [])
        prop_names = [p.get("name", "?") if isinstance(p, dict) else getattr(p, "name", "?") for p in edge_props[:5]]
        lines.append(
            f"    - [{source}] {name}: {src_type} → {tgt_type} "
            f"(hint: {hint}, edge_props: {prop_names})"
        )

    # Add entity type context if available
    if entity_context:
        # Collect unique source/target types in this cluster
        src_types = {m.get("source_type", "") for m in members if m.get("source_type")}
        tgt_types = {m.get("target_type", "") for m in members if m.get("target_type")}
        for type_name in sorted(src_types | tgt_types):
            ctx = entity_context.get(type_name)
            if ctx:
                desc = ctx.get("description", "")
                props = ctx.get("properties", [])
                prop_names_list = []
                for p in props[:8]:
                    pn = p.get("name", "?") if isinstance(p, dict) else getattr(p, "name", "?")
                    prop_names_list.append(pn)
                lines.append(f"    Context — {type_name}: {desc}")
                if prop_names_list:
                    lines.append(f"      Properties: {', '.join(prop_names_list)}")

    return "\n".join(lines)


EDGE_RESPONSE_SCHEMA = json.dumps(
    {
        "classifications": [
            {
                "cluster_label": 0,
                "canonical_name": "covers",
                "source_type": "Insurance_Policy",
                "target_type": "Property",
                "richness_tier": "attributed",
                "richness_rationale": "Coverage amount varies per connection — attributed",
                "edge_properties": [
                    {
                        "name": "coverage_amount",
                        "data_type": "float",
                        "description": "Maximum coverage for this connection",
                    }
                ],
                "reification_recommendation": None,
            }
        ]
    },
    indent=2,
)


def build_edge_detection_prompt(
    rel_clusters: dict[int, list[dict]],
    entity_context: dict[str, dict] | None = None,
) -> tuple[str, str]:
    """Build prompt for Stage D Edge Property Detection.

    Args:
        rel_clusters: Mapping from cluster_label to list of relationship member dicts.
        entity_context: Optional mapping from entity name to dict with
            'description' and 'properties' for source/target type context.

    Returns:
        (system_prompt, user_prompt)
    """
    cluster_texts = []
    for label, members in sorted(rel_clusters.items()):
        cluster_texts.append(_format_rel_for_edge_prompt(label, members, entity_context))

    user_prompt = f"""Classify each relationship cluster by richness tier.
Apply the variability test, count test, and reference test to determine:
- simple (no edge properties)
- attributed (1-3 edge properties that vary per connection)
- reified (4+ properties or independently referenceable — should become its own entity type)

{chr(10).join(cluster_texts)}

RESPONSE FORMAT:
{EDGE_RESPONSE_SCHEMA}"""

    return EDGE_DETECTION_SYSTEM, user_prompt
