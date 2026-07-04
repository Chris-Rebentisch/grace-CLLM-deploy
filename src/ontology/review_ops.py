"""Review operations: schema assembly, CQ impact, session lifecycle."""

from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from src.ontology.models import VersionSource
from src.ontology.review_database import (
    create_review_decision,
    create_review_session,
    get_decision_summary,
    get_review_session_by_id,
    increment_reviewed_count,
    list_decisions_for_session,
    update_review_session_status,
)
from src.ontology.review_models import (
    ReviewDecision,
    ReviewDecisionType,
    ReviewElementType,
    ReviewSession,
    ReviewSessionStatus,
)
from src.ontology.schema_store import ratify_version

log = structlog.get_logger()


def assemble_ratified_schema(
    seed_schema_snapshot: dict,
    decisions: list[ReviewDecision],
) -> dict:
    """Assemble the ratified schema from seed schema + review decisions.

    Processes all decisions against the seed schema to produce the final
    ratified schema_json. Handles all 9 decision types.
    """
    # Build decision lookup: (element_type, element_name) -> decision
    decision_map: dict[tuple[str, str], ReviewDecision] = {}
    for d in decisions:
        key = (d.element_type.value, d.element_name)
        # Keep most recent decision per element
        if key not in decision_map or d.created_at > decision_map[key].created_at:
            decision_map[key] = d

    # Track consumed types (merged into another type)
    consumed_types: set[str] = set()
    for d in decision_map.values():
        if d.decision == ReviewDecisionType.MERGED and d.merged_with:
            consumed_types.add(d.merged_with)

    # Process entity types
    entity_types: dict[str, dict] = {}
    seed_entity_types = seed_schema_snapshot.get("entity_types", [])

    for et in seed_entity_types:
        name = et["name"]

        # Skip consumed types (merged into another)
        if name in consumed_types:
            log.info("entity_type_consumed_by_merge", name=name)
            continue

        key = (ReviewElementType.ENTITY_TYPE.value, name)
        decision = decision_map.get(key)

        if decision is None:
            # No decision — include as-is (auto-approve behavior)
            entity_types[name] = _entity_type_to_schema(et)
            continue

        dt = decision.decision

        if dt in (ReviewDecisionType.APPROVED, ReviewDecisionType.AUTO_APPROVED):
            entity_types[name] = _entity_type_to_schema(et)

        elif dt == ReviewDecisionType.RENAMED:
            new_name = decision.modified_data["name"]
            entity_types[new_name] = _entity_type_to_schema(
                {**et, **(decision.modified_data or {})}
            )

        elif dt == ReviewDecisionType.EDITED:
            merged = {**et, **(decision.modified_data or {})}
            entity_types[merged["name"]] = _entity_type_to_schema(merged)

        elif dt == ReviewDecisionType.SPLIT:
            # Remove original, add subtypes from split_into
            if decision.split_into:
                for sub in decision.split_into:
                    entity_types[sub["name"]] = _entity_type_to_schema(sub)
            log.info("entity_type_split", original=name, into=[s["name"] for s in (decision.split_into or [])])

        elif dt == ReviewDecisionType.MERGED:
            # Keep this type with merged properties
            merged = {**et, **(decision.modified_data or {})}
            entity_types[merged["name"]] = _entity_type_to_schema(merged)

        elif dt == ReviewDecisionType.REJECTED:
            log.info("entity_type_rejected", name=name)
            # Excluded from schema

        else:
            # Fallback for other decision types on entity types — include as-is
            entity_types[name] = _entity_type_to_schema(et)

    # Track all included type names for relationship validation
    included_types = set(entity_types.keys())

    # Process relationships
    relationships: dict[str, dict] = {}
    seed_relationships = seed_schema_snapshot.get("relationships", [])

    for rel in seed_relationships:
        name = rel["name"]
        key = (ReviewElementType.RELATIONSHIP.value, name)
        decision = decision_map.get(key)

        if decision is None:
            # No decision — include as-is
            rel_data = _relationship_to_schema(rel)
        else:
            dt = decision.decision

            if dt in (ReviewDecisionType.APPROVED, ReviewDecisionType.AUTO_APPROVED):
                rel_data = _relationship_to_schema(rel)

            elif dt == ReviewDecisionType.RENAMED:
                new_name = decision.modified_data["name"]
                rel_data = _relationship_to_schema({**rel, **(decision.modified_data or {})})
                name = new_name

            elif dt == ReviewDecisionType.EDITED:
                merged = {**rel, **(decision.modified_data or {})}
                rel_data = _relationship_to_schema(merged)
                name = merged.get("name", name)

            elif dt == ReviewDecisionType.REDIRECTED:
                merged = {**rel, **(decision.modified_data or {})}
                rel_data = _relationship_to_schema(merged)
                name = merged.get("name", name)

            elif dt == ReviewDecisionType.RECLASSIFIED:
                merged = {**rel, **(decision.modified_data or {})}
                rel_data = _relationship_to_schema(merged)
                name = merged.get("name", name)

            elif dt == ReviewDecisionType.REJECTED:
                log.info("relationship_rejected", name=name)
                continue

            elif dt == ReviewDecisionType.MERGED:
                merged = {**rel, **(decision.modified_data or {})}
                rel_data = _relationship_to_schema(merged)
                name = merged.get("name", name)

            elif dt == ReviewDecisionType.SPLIT:
                if decision.split_into:
                    for sub in decision.split_into:
                        sub_data = _relationship_to_schema(sub)
                        relationships[sub["name"]] = sub_data
                continue

            else:
                rel_data = _relationship_to_schema(rel)

        # Validate source_type and target_type exist
        source_type = rel_data.get("source_type", "")
        target_type = rel_data.get("target_type", "")
        if source_type and source_type not in included_types:
            log.warning(
                "relationship_orphaned_source",
                relationship=name,
                source_type=source_type,
            )
            continue
        if target_type and target_type not in included_types:
            log.warning(
                "relationship_orphaned_target",
                relationship=name,
                target_type=target_type,
            )
            continue

        relationships[name] = rel_data

    return {
        "entity_types": entity_types,
        "relationships": relationships,
    }


def _entity_type_to_schema(et: dict) -> dict:
    """Convert a seed schema entity type dict to ratified schema format."""
    result = {}
    for key in ("description", "properties", "parent_type", "domain", "provenance", "confidence"):
        if key in et:
            result[key] = et[key]
    return result


def _relationship_to_schema(rel: dict) -> dict:
    """Convert a seed schema relationship dict to ratified schema format."""
    result = {}
    for key in (
        "source_type", "target_type", "description", "richness_tier",
        "edge_properties", "domain", "provenance", "confidence",
    ):
        if key in rel:
            result[key] = rel[key]
    return result


def partition_schema_by_module(schema_json: dict) -> dict:
    """Partition the ratified schema into per-module subsets.

    Groups entity types and relationships by their 'domain' field.
    """
    modules: dict[str, dict] = {}

    # F-0012 / ISS-0045: `.get("domain") or "general"` (not a .get default)
    # — elements with a PRESENT-but-null domain (key exists, value None)
    # otherwise fall through into a `None` module key that serializes as
    # a bogus "null" module. Applies to entity types AND relationships.
    for type_name, type_data in schema_json.get("entity_types", {}).items():
        domain = type_data.get("domain") or "general"
        if domain not in modules:
            modules[domain] = {"entity_types": {}, "relationships": {}}
        modules[domain]["entity_types"][type_name] = type_data

    for rel_name, rel_data in schema_json.get("relationships", {}).items():
        domain = rel_data.get("domain") or "general"
        if domain not in modules:
            modules[domain] = {"entity_types": {}, "relationships": {}}
        modules[domain]["relationships"][rel_name] = rel_data

    return modules


def compute_cq_impact_preview(
    seed_schema_snapshot: dict,
    current_decisions: list[ReviewDecision],
    target_element_name: str,
    hypothetical_decision: ReviewDecisionType,
) -> dict:
    """Preview CQ impact of a hypothetical decision without recording it."""
    coverage_matrix = seed_schema_snapshot.get("coverage_matrix", [])
    if not coverage_matrix:
        return {
            "element_name": target_element_name,
            "hypothetical_decision": hypothetical_decision.value,
            "cqs_affected": [],
            "coverage_before": 0.0,
            "coverage_after": 0.0,
            "cqs_that_lose_coverage": 0,
            "cqs_that_gain_coverage": 0,
        }

    # Build set of currently included elements from decisions
    included_types, included_rels = _compute_included_elements(
        seed_schema_snapshot, current_decisions
    )

    # Coverage before
    coverage_before = _compute_coverage_rate(coverage_matrix, included_types, included_rels)

    # Apply hypothetical decision
    hyp_types = set(included_types)
    hyp_rels = set(included_rels)
    is_rejection = hypothetical_decision == ReviewDecisionType.REJECTED

    # Check if target is an entity type or relationship
    is_entity_type = any(
        et.get("name") == target_element_name
        for et in seed_schema_snapshot.get("entity_types", [])
    )

    if is_entity_type:
        if is_rejection:
            hyp_types.discard(target_element_name)
        else:
            hyp_types.add(target_element_name)
    else:
        if is_rejection:
            hyp_rels.discard(target_element_name)
        else:
            hyp_rels.add(target_element_name)

    # Coverage after
    coverage_after = _compute_coverage_rate(coverage_matrix, hyp_types, hyp_rels)

    # Find affected CQs
    cqs_affected = []
    loses = 0
    gains = 0
    for entry in coverage_matrix:
        covered_before = _is_cq_covered(entry, included_types, included_rels)
        covered_after = _is_cq_covered(entry, hyp_types, hyp_rels)
        if covered_before and not covered_after:
            cqs_affected.append({
                "cq_id": entry["cq_id"],
                "cq_text": entry.get("cq_text", ""),
                "impact": "loses_coverage",
            })
            loses += 1
        elif not covered_before and covered_after:
            cqs_affected.append({
                "cq_id": entry["cq_id"],
                "cq_text": entry.get("cq_text", ""),
                "impact": "gains_coverage",
            })
            gains += 1

    return {
        "element_name": target_element_name,
        "hypothetical_decision": hypothetical_decision.value,
        "cqs_affected": cqs_affected,
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
        "cqs_that_lose_coverage": loses,
        "cqs_that_gain_coverage": gains,
    }


def compute_cq_impact_for_decision(
    seed_schema_snapshot: dict,
    current_decisions: list[ReviewDecision],
    new_decision: ReviewDecision,
) -> dict:
    """Compute actual CQ impact of a recorded decision."""
    return compute_cq_impact_preview(
        seed_schema_snapshot,
        current_decisions,
        new_decision.element_name,
        new_decision.decision,
    )


def _compute_included_elements(
    seed_schema_snapshot: dict,
    decisions: list[ReviewDecision],
) -> tuple[set[str], set[str]]:
    """Compute which elements are currently included based on decisions.

    Elements with no decision are considered included (auto-approve).
    """
    decision_map: dict[tuple[str, str], ReviewDecision] = {}
    for d in decisions:
        key = (d.element_type.value, d.element_name)
        if key not in decision_map or d.created_at > decision_map[key].created_at:
            decision_map[key] = d

    included_types: set[str] = set()
    for et in seed_schema_snapshot.get("entity_types", []):
        name = et["name"]
        key = (ReviewElementType.ENTITY_TYPE.value, name)
        decision = decision_map.get(key)
        if decision is None or decision.decision != ReviewDecisionType.REJECTED:
            included_types.add(name)

    included_rels: set[str] = set()
    for rel in seed_schema_snapshot.get("relationships", []):
        name = rel["name"]
        key = (ReviewElementType.RELATIONSHIP.value, name)
        decision = decision_map.get(key)
        if decision is None or decision.decision != ReviewDecisionType.REJECTED:
            included_rels.add(name)

    return included_types, included_rels


def _compute_coverage_rate(
    coverage_matrix: list[dict],
    included_types: set[str],
    included_rels: set[str],
) -> float:
    """Compute coverage rate: fraction of CQs that have at least one covering element."""
    if not coverage_matrix:
        return 0.0
    covered = sum(
        1 for entry in coverage_matrix
        if _is_cq_covered(entry, included_types, included_rels)
    )
    return covered / len(coverage_matrix)


def _is_cq_covered(
    entry: dict,
    included_types: set[str],
    included_rels: set[str],
) -> bool:
    """Check if a CQ is covered by at least one included element."""
    for t in entry.get("covered_by_types", []):
        if t in included_types:
            return True
    for r in entry.get("covered_by_relationships", []):
        if r in included_rels:
            return True
    return False


# --- Session Lifecycle ---


def start_review_session(
    db: Session,
    merge_run_id: str,
    reviewer: str,
    seed_schema_data: dict,
) -> ReviewSession:
    """Start a new review session from a SeedSchema."""
    entity_types = seed_schema_data.get("entity_types", [])
    relationships = seed_schema_data.get("relationships", [])

    session = ReviewSession(
        reviewer=reviewer,
        seed_schema_merge_run_id=merge_run_id,
        seed_schema_snapshot=seed_schema_data,
        total_entity_types=len(entity_types),
        total_relationships=len(relationships),
    )
    return create_review_session(db, session)


def complete_review_session(
    db: Session,
    session_id: UUID,
    reviewer: str,
    force: bool = False,
) -> dict:
    """Complete a review session and ratify the schema."""
    session = get_review_session_by_id(db, session_id)
    if session is None:
        raise ValueError(f"Review session {session_id} not found")

    if session.status != ReviewSessionStatus.IN_PROGRESS:
        raise ValueError(f"Session is {session.status.value}, not in_progress")

    decisions = list_decisions_for_session(db, session_id)
    snapshot = session.seed_schema_snapshot

    # Check for un-reviewed elements
    decided_elements = _get_decided_element_names(decisions)
    un_reviewed = _find_un_reviewed_elements(snapshot, decided_elements)

    if un_reviewed and not force:
        raise ValueError(
            f"Un-reviewed elements remain: {un_reviewed}. "
            "Set force=True to auto-approve them."
        )

    if un_reviewed and force:
        # Auto-approve un-reviewed elements
        for element_type, element_name, original_data in un_reviewed:
            auto_decision = ReviewDecision(
                session_id=session_id,
                element_type=element_type,
                element_name=element_name,
                decision=ReviewDecisionType.AUTO_APPROVED,
                original_data=original_data,
                reviewer="system:auto_approve",
            )
            create_review_decision(db, auto_decision)
            increment_reviewed_count(db, session_id, element_type)
        # Reload decisions
        decisions = list_decisions_for_session(db, session_id)

    # Assemble ratified schema
    schema_json = assemble_ratified_schema(snapshot, decisions)
    schema_modules = partition_schema_by_module(schema_json)

    # Generate changelog from decision summary
    summary = get_decision_summary(db, session_id)
    changelog = _generate_changelog(summary)

    # Ratify the version
    version = ratify_version(
        db=db,
        schema_json=schema_json,
        schema_modules=schema_modules,
        source=VersionSource.GUIDED_REVIEW,
        reviewer=reviewer,
        changelog=changelog,
    )

    # Update session status
    updated_session = update_review_session_status(
        db,
        session_id,
        ReviewSessionStatus.COMPLETED,
        agent=reviewer,
        reason="Review completed",
        resulting_version_id=version.id,
    )

    return {
        "session": updated_session.model_dump(mode="json"),
        "version": version.model_dump(mode="json"),
        "decision_summary": summary,
    }


def abandon_review_session(
    db: Session,
    session_id: UUID,
    agent: str,
    reason: str | None = None,
) -> ReviewSession | None:
    """Abandon a review session without ratifying."""
    return update_review_session_status(
        db,
        session_id,
        ReviewSessionStatus.ABANDONED,
        agent=agent,
        reason=reason,
    )


def _build_cq_text_map(db: Session) -> dict[str, str]:
    """Map short CQ IDs (first 8 chars of UUID, as emitted by extraction) to text.

    Best-effort: lets the review screen show the actual business questions a type
    helps answer instead of opaque ``cq_001`` tokens. Returns an empty map (and the
    caller falls back to raw IDs) if CQs can't be loaded.
    """
    try:
        from src.discovery.cq_database import list_cqs
        from src.discovery.cq_models import CQStatus

        cq_map: dict[str, str] = {}
        for status in (CQStatus.ACCEPTED, CQStatus.DRAFT):
            for cq in list_cqs(db, status=status, limit=10000):
                cq_map[str(cq.id)[:8]] = cq.canonical_text
        return cq_map
    except Exception:  # pragma: no cover - resilience only
        log.warning("cq_text_map_unavailable", exc_info=True)
        return {}


def _resolve_questions(cq_ids: list, cq_map: dict[str, str], limit: int = 3) -> list[str]:
    """Resolve answerable-CQ IDs to human-readable question text (best-effort)."""
    questions: list[str] = []
    for raw in cq_ids or []:
        short = str(raw)[:8]
        text = cq_map.get(short)
        if text and text not in questions:
            questions.append(text)
        if len(questions) >= limit:
            break
    return questions


def _answerable_cq_index(snapshot: dict) -> dict[tuple[str, str], list]:
    """Invert the snapshot's coverage matrix into per-element answerable CQs.

    F-0011 / ISS-0044: the review-elements listing showed
    ``answerable_cq_count: 0`` for elements that CQs explicitly pull for —
    the coverage matrix (which drives the upstream coverage_rate of 1.0)
    was never carried into the per-element counts when the snapshot's
    elements lack their own ``answerable_cqs`` field. This index maps
    ``(element_type, element_name) -> [cq_id, ...]`` from
    ``coverage_matrix`` entries' ``covered_by_types`` /
    ``covered_by_relationships`` so the listing can fall back to it.
    """
    index: dict[tuple[str, str], list] = {}
    for entry in snapshot.get("coverage_matrix", []) or []:
        cq_id = entry.get("cq_id")
        if cq_id is None:
            continue
        for t in entry.get("covered_by_types", []) or []:
            index.setdefault(
                (ReviewElementType.ENTITY_TYPE.value, t), []
            ).append(cq_id)
        for r in entry.get("covered_by_relationships", []) or []:
            index.setdefault(
                (ReviewElementType.RELATIONSHIP.value, r), []
            ).append(cq_id)
    return index


def _present_element(
    raw: dict,
    element_type: ReviewElementType,
    decision_map: dict[tuple[str, str], "ReviewDecision"],
    cq_map: dict[str, str],
    coverage_cq_index: dict[tuple[str, str], list] | None = None,
) -> dict:
    """Shape one snapshot element into the reviewer-facing, plain-language payload.

    Carries the new D522-session presentation fields (display_label, plain_description,
    example_snippet, evidence_document_count) when present, and degrades gracefully for
    pre-existing snapshots that predate them. ``name`` (the technical type name) is kept
    so the decision verbs still address the right element.
    """
    name = raw["name"]
    d = decision_map.get((element_type.value, name))
    # F-0011 / ISS-0044: prefer the element's own answerable_cqs; when the
    # snapshot element doesn't carry them, fall back to the CQs the
    # snapshot's coverage matrix says this element covers.
    answerable = raw.get("answerable_cqs", []) or []
    if not answerable and coverage_cq_index:
        answerable = coverage_cq_index.get((element_type.value, name), [])
    return {
        "name": name,
        "display_label": raw.get("display_label") or "",
        "description": raw.get("description") or "",
        "plain_description": raw.get("plain_description") or "",
        "example_snippet": raw.get("example_snippet"),
        "evidence_document_count": raw.get("evidence_document_count", 0),
        "answerable_questions": _resolve_questions(answerable, cq_map),
        "answerable_cq_count": len(answerable),
        "status": "decided" if d else "pending",
        "decision": d.decision.value if d else None,
    }


def get_element_review_status(
    db: Session,
    session_id: UUID,
) -> dict:
    """Get the review status of every element in the session.

    Enriched (D522 session) with plain-language presentation fields so a
    non-technical reviewer can confirm types without graph knowledge.
    """
    session = get_review_session_by_id(db, session_id)
    if session is None:
        return {"entity_types": [], "relationships": []}

    decisions = list_decisions_for_session(db, session_id)
    decision_map: dict[tuple[str, str], ReviewDecision] = {}
    for d in decisions:
        key = (d.element_type.value, d.element_name)
        if key not in decision_map or d.created_at > decision_map[key].created_at:
            decision_map[key] = d

    cq_map = _build_cq_text_map(db)
    # F-0011 / ISS-0044: coverage-matrix fallback for answerable-CQ counts.
    coverage_cq_index = _answerable_cq_index(session.seed_schema_snapshot)

    entity_types = [
        _present_element(
            et, ReviewElementType.ENTITY_TYPE, decision_map, cq_map,
            coverage_cq_index,
        )
        for et in session.seed_schema_snapshot.get("entity_types", [])
    ]
    relationships = [
        _present_element(
            rel, ReviewElementType.RELATIONSHIP, decision_map, cq_map,
            coverage_cq_index,
        )
        for rel in session.seed_schema_snapshot.get("relationships", [])
    ]

    return {"entity_types": entity_types, "relationships": relationships}


def _get_decided_element_names(decisions: list[ReviewDecision]) -> set[tuple[str, str]]:
    """Get set of (element_type, element_name) that have decisions."""
    return {(d.element_type.value, d.element_name) for d in decisions}


def _find_un_reviewed_elements(
    snapshot: dict,
    decided: set[tuple[str, str]],
) -> list[tuple[ReviewElementType, str, dict]]:
    """Find elements in the seed schema that don't have decisions."""
    un_reviewed = []
    for et in snapshot.get("entity_types", []):
        key = (ReviewElementType.ENTITY_TYPE.value, et["name"])
        if key not in decided:
            un_reviewed.append((ReviewElementType.ENTITY_TYPE, et["name"], et))
    for rel in snapshot.get("relationships", []):
        key = (ReviewElementType.RELATIONSHIP.value, rel["name"])
        if key not in decided:
            un_reviewed.append((ReviewElementType.RELATIONSHIP, rel["name"], rel))
    return un_reviewed


def _generate_changelog(summary: dict) -> str:
    """Generate a human-readable changelog from decision summary."""
    parts = []
    by_decision = summary.get("by_decision", {})
    for decision_type, count in sorted(by_decision.items()):
        parts.append(f"{decision_type}: {count}")
    total = summary.get("total", 0)
    return f"Guided Review: {total} decisions ({', '.join(parts)})"
