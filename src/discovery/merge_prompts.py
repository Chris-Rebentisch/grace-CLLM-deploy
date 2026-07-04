"""System and user prompt templates for Tier 3 LLM calls in the CQ merge pipeline."""

import json


# --- Call 1: Canonical phrasing selection ---

CALL1_SYSTEM_PROMPT = """You are a knowledge engineering assistant specializing in competency question (CQ) curation for ontology design.

Your task: For each semantic cluster of CQs, select or synthesize the single best canonical phrasing, and identify any members that are semantically distinct enough to warrant splitting into separate CQs.

Rules:
1. The canonical phrasing should be the clearest, most precise formulation that captures the shared intent of all cluster members.
2. Prefer an existing CQ phrasing when it is already clear and complete. Only synthesize a new phrasing if none of the originals are adequate.
3. A split recommendation means a cluster member asks a meaningfully different question, not just a rephrasing.
4. Return valid JSON matching the schema exactly. No extra keys. Do NOT wrap your response in markdown code fences (no ```json blocks).
5. Your response must be a JSON OBJECT (not a JSON array)."""


CALL2_SYSTEM_PROMPT = """You are a knowledge engineering assistant organizing competency questions into a domain hierarchy for ontology design.

Your task: Organize the provided canonical CQs and singletons into a domain/sub-domain hierarchy, and identify cross-domain relationships.

Rules:
1. Group CQs by their primary domain, then further organize into coherent sub-domains within each domain.
2. Sub-domain names should be descriptive noun phrases (e.g., "ownership_transfers", "policy_coverage", "entity_classification").
3. Cross-domain links connect CQs that span domain boundaries. These are important for ontology integration.
4. Every CQ must appear in exactly one sub-domain.
5. Return valid JSON matching the schema exactly. No extra keys. Do NOT wrap your response in markdown code fences (no ```json blocks).
6. Your response must be a JSON OBJECT (not a JSON array)."""


CALL3_SYSTEM_PROMPT = """You are a knowledge engineering assistant performing gap analysis on a competency question set for ontology design.

Your task: Given the current CQ hierarchy, coverage gap report, and existing CQs, propose new gap-fill CQs and annotate existing CQs with expected ontology paths.

Rules:
1. Gap-fill CQs should address specific coverage gaps identified in the report.
2. Each gap-fill CQ must be a well-formed competency question, not a statement.
3. Path annotations describe the expected ontology traversal to answer each CQ (e.g., "Company -> owns -> Property -> located_in -> Jurisdiction").
4. path_types lists the ontology class names in the path. path_properties lists the property names.
5. Be conservative with gap-fills. Only propose CQs that genuinely fill identified gaps.
6. Return valid JSON matching the schema exactly. No extra keys. Do NOT wrap your response in markdown code fences (no ```json blocks).
7. Your response must be a JSON OBJECT (not a JSON array)."""


def build_call1_prompt(clusters_data: list[dict]) -> tuple[str, str]:
    """Build the prompt pair for Call 1: canonical phrasing selection.

    Args:
        clusters_data: List of dicts, each with 'cluster_label', 'members' (list of
            {'index': int, 'text': str, 'source_pass': str, 'domain': str}).

    Returns:
        (system_prompt, user_prompt_json_string)
    """
    user_data = {
        "task": "canonical_phrasing",
        "instructions": (
            "For each cluster, return: cluster_label, canonical_text (best phrasing), "
            "canonical_index (index of chosen member, or -1 if synthesized), and "
            "split_recommendations (list of {index, reason} for members that should be split out)."
        ),
        "response_schema": {
            "clusters": [
                {
                    "cluster_label": "int",
                    "canonical_text": "string",
                    "canonical_index": "int",
                    "split_recommendations": [{"index": "int", "reason": "string"}],
                }
            ]
        },
        "clusters": clusters_data,
    }
    return (CALL1_SYSTEM_PROMPT, json.dumps(user_data, indent=2))


def build_call2_prompt(
    canonical_cqs: list[dict], singletons: list[dict]
) -> tuple[str, str]:
    """Build the prompt pair for Call 2: hierarchy organization.

    Args:
        canonical_cqs: List of dicts with 'id', 'text', 'domain', 'cq_type'.
        singletons: List of dicts with 'id', 'text', 'domain', 'cq_type'.

    Returns:
        (system_prompt, user_prompt_json_string)
    """
    user_data = {
        "task": "hierarchy_organization",
        "instructions": (
            "Organize all CQs (canonical + singletons) into domain_groups, each with "
            "sub_domains containing cq_ids. Also identify cross_domain_links between "
            "CQs that span domain boundaries."
        ),
        "response_schema": {
            "domain_groups": [
                {
                    "domain": "string",
                    "sub_domains": [
                        {"name": "string", "cq_ids": ["string"]}
                    ],
                }
            ],
            "cross_domain_links": [
                {
                    "source_cq_id": "string",
                    "target_cq_id": "string",
                    "relationship": "string",
                }
            ],
        },
        "canonical_cqs": canonical_cqs,
        "singletons": singletons,
    }
    return (CALL2_SYSTEM_PROMPT, json.dumps(user_data, indent=2))


def build_call3_prompt(
    hierarchy: dict,
    gap_report: dict,
    canonical_cqs: list[dict],
    singletons: list[dict],
    gap_fill_max: int,
) -> tuple[str, str]:
    """Build the prompt pair for Call 3: gap analysis and path annotation.

    Args:
        hierarchy: Call 2 response as dict (domain_groups, cross_domain_links).
        gap_report: GapReport as dict.
        canonical_cqs: List of dicts with 'id', 'text', 'domain', 'cq_type'.
        singletons: List of dicts with 'id', 'text', 'domain', 'cq_type'.
        gap_fill_max: Maximum number of gap-fill CQs to propose.

    Returns:
        (system_prompt, user_prompt_json_string)
    """
    user_data = {
        "task": "gap_analysis_and_paths",
        "instructions": (
            f"Propose up to {gap_fill_max} gap-fill CQs that address the identified coverage gaps. "
            "Also annotate each existing CQ with its expected ontology path. "
            "For path_types, list ontology class names. For path_properties, list property names."
        ),
        "response_schema": {
            "gap_fill_cqs": [
                {
                    "canonical_text": "string",
                    "domain": "string",
                    "cq_type": "string",
                    "gap_addressed": "string",
                    "rationale": "string",
                }
            ],
            "path_annotations": [
                {
                    "cq_id": "string",
                    "expected_path": "string",
                    "path_types": ["string"],
                    "path_properties": ["string"],
                }
            ],
        },
        "gap_fill_max": gap_fill_max,
        "hierarchy": hierarchy,
        "gap_report": gap_report,
        "canonical_cqs": canonical_cqs,
        "singletons": singletons,
    }
    return (CALL3_SYSTEM_PROMPT, json.dumps(user_data, indent=2))
