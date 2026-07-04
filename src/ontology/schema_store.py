"""Schema store operations for ontology version management.

High-level operations: ratify_version, hash chaining, module extraction, version history.
"""

import hashlib
import json
import os
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from src.analytics import metrics as grace_metrics
from src.ontology.database import (
    create_version,
    get_active_version,
    get_next_version_number,
    get_version_by_number,
    list_versions,
    set_active_version,
)
from src.ontology.diff_engine import compute_entity_level_diff, compute_schema_diff
from src.ontology.models import OntologyVersion, VersionSource

log = structlog.get_logger()

# Tracks (ontology_module, entity_type) pairs emitted on the last promotion
# so retired labels can be explicitly cleared (spec §5.3 "replaced in full").
_ENTITY_TYPE_GAUGE_STATE: set[tuple[str, str]] = set()


def _emit_ontology_entity_type_count(schema_modules: dict) -> None:
    """Chunk 25 §5.3: replace-in-full gauge per ``(module, entity_type)``.

    Applies top-N + ``_other_`` cap via ``ONTOLOGY_METRIC_TOPN`` (default 20).
    Clears retired labels (previously emitted but no longer present) by
    setting them to 0 so dashboards do not read stale values.
    """
    global _ENTITY_TYPE_GAUGE_STATE
    topn = int(os.environ.get("ONTOLOGY_METRIC_TOPN", "20"))

    new_state: set[tuple[str, str]] = set()
    for module_name, module_content in (schema_modules or {}).items():
        entity_types = sorted(
            (module_content or {}).get("entity_types", {}).keys()
        )
        if topn > 0 and len(entity_types) > topn:
            head = entity_types[:topn]
            tail_count = len(entity_types) - topn
            for et in head:
                grace_metrics.ontology_entity_type_count.set(
                    1,
                    attributes={
                        "ontology_module": module_name,
                        "entity_type": et,
                    },
                )
                new_state.add((module_name, et))
            grace_metrics.ontology_entity_type_count.set(
                tail_count,
                attributes={
                    "ontology_module": module_name,
                    "entity_type": "_other_",
                },
            )
            new_state.add((module_name, "_other_"))
        else:
            for et in entity_types:
                grace_metrics.ontology_entity_type_count.set(
                    1,
                    attributes={
                        "ontology_module": module_name,
                        "entity_type": et,
                    },
                )
                new_state.add((module_name, et))

    retired = _ENTITY_TYPE_GAUGE_STATE - new_state
    for module_name, entity_type in retired:
        grace_metrics.ontology_entity_type_count.set(
            0,
            attributes={
                "ontology_module": module_name,
                "entity_type": entity_type,
            },
        )

    _ENTITY_TYPE_GAUGE_STATE = new_state


def canonical_json(obj: dict) -> str:
    """Produce deterministic JSON string for hashing.

    sort_keys=True ensures key ordering is consistent.
    separators=(',', ':') removes whitespace for compact representation.
    ensure_ascii=False preserves unicode characters.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(schema_json: dict, previous_hash: str | None = None) -> str:
    """Compute SHA-256 hash for tamper-evidence chain.

    Hash = SHA256(canonical_json(schema_json) + previous_hash)
    For version 1 (no predecessor): SHA256(canonical_json(schema_json))
    """
    data = canonical_json(schema_json)
    if previous_hash:
        data = data + previous_hash
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def count_schema_elements(schema_json: dict) -> tuple[int, int]:
    """Count entity types and relationship types in a JSON Schema.

    Handles both flat GrACE structure and Pydantic $defs structure.
    Returns: (entity_type_count, relationship_type_count)
    """
    entity_count = 0
    rel_count = 0

    # Structure A: flat GrACE format
    if "entity_types" in schema_json:
        entity_count = len(schema_json["entity_types"])
    elif "$defs" in schema_json:
        # Structure B: Pydantic $defs — count all definitions as entity types
        entity_count = len(schema_json["$defs"])

    if "relationships" in schema_json:
        rel_count = len(schema_json["relationships"])

    return entity_count, rel_count


def validate_child_ontology_submission(
    child_schema: dict,
    mother_schema: dict,
) -> "ValidationResult":
    """Validate a child ontology schema against the mother schema (D405).

    Delegates to ``src/federation/scope_validator.validate_child_schema``.
    Child schemas must be a superset of mother-defined types: they may
    add new properties but cannot remove or change the type of mother
    properties.

    Returns:
        ValidationResult with per-type pass/fail.
    """
    from src.federation.scope_validator import ValidationResult, validate_child_schema  # noqa: F811

    return validate_child_schema(child_schema, mother_schema)


def _canonicalize_properties(properties) -> dict:
    """Normalize a ``properties`` field into a dict keyed by name.

    Discovery's ratified output ships ``properties`` as a LIST
    (``[{"name": ..., "data_type": ...}]``) while the rest of the
    codebase (DDL generator, constraint validator, extraction prompts,
    schema migration, etc.) expects a DICT (``{"name": {"data_type":
    ...}}``). Normalizing once at the ratification boundary keeps the
    persisted schema canonical and eliminates a cluster of
    ``'list' object has no attribute 'items'`` failures downstream.

    Phase-4 finding (Chunk 4 testing log Step 4.6).
    """
    if isinstance(properties, dict):
        return properties
    if isinstance(properties, list):
        return {p["name"]: p for p in properties if isinstance(p, dict) and "name" in p}
    return {}


def _list_or_dict_of_named_to_dict(items, name_key: str = "name") -> dict:
    """Coerce a list-of-{name,...} or dict-keyed-by-name into a dict-keyed-by-name.

    Handles both Discovery's native shape (list) and the canonical shape (dict).
    """
    if isinstance(items, dict):
        return items
    if isinstance(items, list):
        return {
            it[name_key]: it
            for it in items
            if isinstance(it, dict) and name_key in it
        }
    return {}


def _canonicalize_schema(schema_json: dict) -> dict:
    """Return a copy of ``schema_json`` with all collections in canonical dict shape.

    Discovery emits both ``entity_types``/``relationships`` and their nested
    ``properties`` as LISTS of ``{"name": ..., ...}`` dicts. The rest of the
    codebase (DDL generator, constraint validator, extraction prompts,
    schema migration) expects DICTS keyed by name. Normalize at the
    ratification boundary so every downstream consumer sees the canonical
    shape regardless of caller path (review flow OR direct ratify).
    Phase-4 finding.
    """
    if not isinstance(schema_json, dict):
        return schema_json
    out = dict(schema_json)
    ets = _list_or_dict_of_named_to_dict(out.get("entity_types"))
    out["entity_types"] = {
        name: {**td, "properties": _canonicalize_properties(td.get("properties", {}))}
        for name, td in ets.items()
        if isinstance(td, dict)
    }
    rels = _list_or_dict_of_named_to_dict(out.get("relationships"))
    out["relationships"] = {
        name: {**rd, "properties": _canonicalize_properties(rd.get("properties", {}))}
        for name, rd in rels.items()
        if isinstance(rd, dict)
    }
    return out


def ratify_version(
    db: Session,
    schema_json: dict,
    schema_modules: dict,
    source: VersionSource,
    reviewer: str | None = None,
    changelog: str | None = None,
    kgcl_commands: list[str] | None = None,
    proposal_id: UUID | None = None,
    cq_coverage_snapshot: dict | None = None,
    promotion_gate_passed: bool | None = None,
    promotion_gate_details: dict | None = None,
    metadata_extra: dict | None = None,
    segment_id: str | None = None,
    ontology_scope: str | None = None,
    activate: bool = True,
) -> OntologyVersion:
    """Create a new ratified ontology version with full provenance.

    D278 (Chunk 36): when both ``segment_id`` and ``reviewer`` are
    provided, predecessor lookup and active-flag swap are scoped to the
    ``(segment_id, reviewer)`` partition so per-executive ontologies can
    coexist. Legacy callers (omitting either argument) preserve the
    pre-Chunk-36 global active-flag semantics.

    Chunk 51 (D405): ``ontology_scope`` gates child-scope ratification.
    When ``ontology_scope="child"``, the mother schema (global active
    version) is fetched and the child schema is validated via
    ``validate_child_ontology_submission``. Violations raise
    ``ValueError``. Mother-scope and single-scope ratification follow
    the existing path unchanged.

    F-0045 / ISS-0025: ``activate`` (default ``True`` — today's
    behavior) lets callers persist a version WITHOUT flipping the
    deployment-active flag. Independent of the caller's choice, a hard
    guard forces ``activate=False`` for child-scoped and
    ``connector_sync``-sourced versions (see inline capture-the-why).

    This is the primary entry point for creating schema versions. It:
    1. Gets the next version number
    2. Loads the current active version (predecessor)
    3. Computes RFC 6902 patch from predecessor (if exists)
    4. Computes OM4OV diff from predecessor (if exists)
    5. Computes hash chain value
    6. Counts entity types and relationship types
    7. Creates the OntologyVersion record
    8. Inserts via create_version()
    9. Calls set_active_version() to swap active flag — SKIPPED when
       ``activate=False`` or the F-0045 guard fires
    10. Returns the new active version (or the inactive created version
        when activation was skipped)
    """
    # D405: child-scope ontology validation against mother schema.
    effective_scope = ontology_scope or "single"

    # --- F-0045 / ISS-0025 hard guard (capture-the-why) ---------------------
    # Invariant: the deployment's globally-active ontology version (the
    # "mother" ontology) may only be replaced by deployment-scope
    # ratifications (discovery / guided_review / adaptive_evolution / manual /
    # KGCL executor). Carve-out: child-scoped versions — including every
    # connector-sync-sourced version (D405 child semantics) — are persisted
    # for provenance but MUST NOT become or replace the active version.
    # Why: three consecutive validation runs (F-0045, 3rd occurrence; ISS-0025)
    # saw `python -m src.connectors run --connector-type synthetic` ratify a
    # 0-entity-type child schema (module keys ['synthetic']) as the
    # deployment-active ontology, instantly breaking every module-scoped
    # consumer ("Schema not found for module_name='my_domain'").
    # Authorization: ISS-0025 remediation; belt-and-braces on top of the
    # sync_pipeline call site passing ``activate=False`` explicitly.
    source_value = source.value if isinstance(source, VersionSource) else str(source)
    if effective_scope == "child" or source_value == VersionSource.CONNECTOR_SYNC.value:
        if activate:
            log.warning(
                "ratify_activation_refused",
                reason="child-scoped/connector_sync versions may never become "
                "the deployment-active ontology (F-0045 / ISS-0025)",
                source=source_value,
                ontology_scope=effective_scope,
            )
        activate = False
    if effective_scope == "child":
        mother_version = get_active_version(db)
        if mother_version:
            result = validate_child_ontology_submission(
                schema_json, mother_version.schema_json
            )
            if not result.passed:
                errors = []
                for tr in result.type_results:
                    if not tr.passed:
                        errors.extend(tr.errors)
                raise ValueError(
                    f"Child schema violates mother-type contract: {'; '.join(errors)}"
                )

    # Canonicalize property shapes BEFORE hash compute so the persisted
    # schema_json matches the hash and so all downstream consumers
    # (DDL generator, constraint validator, extraction prompts, schema
    # migration, etc.) see dict-shaped properties regardless of whether
    # the caller passed list-shape (Discovery's native output) or
    # dict-shape (Pydantic $defs / hand-authored). Phase-4 finding.
    schema_json = _canonicalize_schema(schema_json)

    version_number = get_next_version_number(db)
    predecessor = get_active_version(db, segment_id=segment_id, reviewer=reviewer)

    patch_json = None
    diff_summary = None
    previous_version_id = None
    previous_hash = None

    if predecessor:
        previous_version_id = predecessor.id
        previous_hash = predecessor.hash_chain
        patch_json, diff_summary = compute_schema_diff(predecessor.schema_json, schema_json)

    hash_chain = compute_hash(schema_json, previous_hash)
    entity_type_count, relationship_type_count = count_schema_elements(schema_json)

    version = OntologyVersion(
        version_number=version_number,
        schema_json=schema_json,
        schema_modules=schema_modules,
        patch_json=patch_json,
        diff_summary=diff_summary,
        previous_version_id=previous_version_id,
        hash_chain=hash_chain,
        source=source,
        proposal_id=proposal_id,
        reviewer=reviewer,
        changelog=changelog,
        kgcl_commands=kgcl_commands,
        cq_coverage_snapshot=cq_coverage_snapshot,
        entity_type_count=entity_type_count,
        relationship_type_count=relationship_type_count,
        promotion_gate_passed=promotion_gate_passed,
        promotion_gate_details=promotion_gate_details,
        is_active=False,
        # F-0045 / ISS-0025: the c51c `ontology_scope` DB column is not mapped
        # by the ORM row model, so record the effective scope in
        # metadata_extra (only when non-default) so operators can distinguish
        # child/connector versions in version history without a schema change.
        metadata_extra=(
            {**(metadata_extra or {}), "ontology_scope": effective_scope}
            if effective_scope != "single"
            else (metadata_extra or {})
        ),
    )

    created = create_version(db, version)
    if activate:
        activated = set_active_version(
            db, created.id, segment_id=segment_id, reviewer=reviewer
        )
        # Gauge is replace-in-full keyed to the ACTIVE ontology's modules;
        # emitting it for a non-activated child version would zero out the
        # mother's entity-type labels (F-0045 / ISS-0025 — same corruption
        # class, metrics surface). Only emit on real promotion.
        _emit_ontology_entity_type_count(schema_modules)
    else:
        # F-0045 / ISS-0025: version persisted for provenance only — the
        # current active (mother) version remains untouched.
        activated = created

    log.info(
        "version_ratified",
        version_number=version_number,
        hash_chain=hash_chain[:16],
        entity_types=entity_type_count,
        relationships=relationship_type_count,
        activated=activate,
        ontology_scope=effective_scope,
    )
    return activated


def _is_branch_version(v: OntologyVersion) -> bool:
    """Classify a version as a BRANCH row (vs. a mother-chain row).

    F-0045 / ISS-0025 (hash-chain follow-up): the F-0045 activation guard
    makes child-scoped / ``connector_sync``-sourced versions NON-ACTIVATING.
    Such a version chains its ``previous_hash`` from the then-active mother,
    and the NEXT mother ratification chains from that SAME mother — a
    legitimate branch in governance history, not a tampered chain. Branch
    rows are identified by:

    - ``metadata_extra["ontology_scope"] == "child"`` — written by the
      F-0045 fix in ``ratify_version`` (the c51c ``ontology_scope`` DB
      column is not ORM-mapped, so metadata_extra is the readable marker);
    - ``source == connector_sync`` — every connector-sync version is
      forced non-activating by the same guard, including rows persisted
      before the metadata marker existed.

    D278 segment/reviewer-partitioned versions are the same topological
    class, but the ``segment_id`` row column is not exposed on the
    ``OntologyVersion`` model (and is not populated by ``ratify_version``),
    so they are NOT classified here — their treatment is unchanged from
    the pre-existing linear behavior.
    """
    if (v.metadata_extra or {}).get("ontology_scope") == "child":
        return True
    source_value = v.source.value if isinstance(v.source, VersionSource) else str(v.source)
    return source_value == VersionSource.CONNECTOR_SYNC.value


def verify_hash_chain(db: Session) -> dict:
    """Verify the integrity of the entire version hash chain — scope-aware.

    F-0045 / ISS-0025 (hash-chain follow-up, capture-the-why): the previous
    implementation walked ALL versions linearly (each row's previous_hash
    had to equal the immediately preceding row's hash). The F-0045
    activation guard legitimately produces BRANCHES — a non-activating
    child/connector version chains from the active mother, and the next
    mother ratification chains from that same mother — which the linear
    walk mis-flagged as tampering. A verify-chain that cries wolf on
    legitimate governance history trains operators to ignore an audit
    primitive (GrACE-Product §10). This version verifies the topology as
    the TREE it actually is; nothing is skipped or exempted:

    - MOTHER chain (deployment/activating rows): linear walk exactly as
      before — each mother's previous_hash must equal the prior mother's
      hash (write-time invariant: mothers always chain from the globally
      active version, which is always the last mother).
    - BRANCH rows (child-scoped / connector_sync, see
      ``_is_branch_version``): each row's hash is recomputed against the
      hash of its RECORDED parent (``previous_version_id``). A missing or
      dangling parent is a failure, and a tampered branch payload fails
      its own recomputation — tamper detection holds everywhere.

    Existing response fields keep their meaning; ``mother_chain``,
    ``branches_checked``, and ``branch_failures`` are ADDITIVE.
    """
    # Get all versions in ascending order
    all_versions = list_versions(db, limit=10000, offset=0)
    all_versions.reverse()  # list_versions returns descending, we need ascending

    if not all_versions:
        return {
            "valid": True,
            "versions_checked": 0,
            "first_broken_version": None,
            "details": "No versions exist.",
            "mother_chain": {
                "valid": True,
                "versions_checked": 0,
                "first_broken_version": None,
            },
            "branches_checked": 0,
            "branch_failures": [],
        }

    by_id = {v.id: v for v in all_versions}
    mother_rows = [v for v in all_versions if not _is_branch_version(v)]
    branch_rows = [v for v in all_versions if _is_branch_version(v)]

    # --- Mother chain: linear walk (pre-existing semantics, restricted to
    # activating/deployment rows). -----------------------------------------
    mother_broken_version: int | None = None
    mother_detail: str | None = None
    previous_hash = None
    for v in mother_rows:
        expected = compute_hash(v.schema_json, previous_hash)
        if v.hash_chain != expected:
            mother_broken_version = v.version_number
            mother_detail = (
                f"Hash mismatch at version {v.version_number}. "
                f"Expected {expected[:16]}..., got {v.hash_chain[:16]}..."
            )
            break
        previous_hash = v.hash_chain

    # --- Branch rows: each verified against its RECORDED parent. -----------
    branch_failures: list[dict] = []
    for v in branch_rows:
        parent_hash = None
        parent_version_number = None
        if v.previous_version_id is not None:
            parent = by_id.get(v.previous_version_id)
            if parent is None:
                branch_failures.append({
                    "version_number": v.version_number,
                    "parent_version_number": None,
                    "detail": (
                        f"Branch version {v.version_number} records parent "
                        f"{v.previous_version_id} which does not exist."
                    ),
                })
                continue
            parent_hash = parent.hash_chain
            parent_version_number = parent.version_number
        expected = compute_hash(v.schema_json, parent_hash)
        if v.hash_chain != expected:
            branch_failures.append({
                "version_number": v.version_number,
                "parent_version_number": parent_version_number,
                "detail": (
                    f"Branch hash mismatch at version {v.version_number} "
                    f"(parent version {parent_version_number}). "
                    f"Expected {expected[:16]}..., got {v.hash_chain[:16]}..."
                ),
            })

    mother_chain = {
        "valid": mother_broken_version is None,
        "versions_checked": (
            mother_broken_version
            if mother_broken_version is not None
            else len(mother_rows)
        ),
        "first_broken_version": mother_broken_version,
    }

    if mother_broken_version is not None:
        # Preserve pre-existing top-level failure semantics for mother tamper.
        return {
            "valid": False,
            "versions_checked": mother_broken_version,
            "first_broken_version": mother_broken_version,
            "details": mother_detail,
            "mother_chain": mother_chain,
            "branches_checked": len(branch_rows),
            "branch_failures": branch_failures,
        }

    if branch_failures:
        first_branch_failure = min(f["version_number"] for f in branch_failures)
        return {
            "valid": False,
            "versions_checked": first_branch_failure,
            "first_broken_version": first_branch_failure,
            "details": (
                f"Mother chain OK ({len(mother_rows)} versions); "
                f"{len(branch_failures)} branch failure(s): "
                + "; ".join(f["detail"] for f in branch_failures)
            ),
            "mother_chain": mother_chain,
            "branches_checked": len(branch_rows),
            "branch_failures": branch_failures,
        }

    return {
        "valid": True,
        "versions_checked": len(all_versions),
        "first_broken_version": None,
        "details": (
            f"All {len(all_versions)} versions verified "
            f"({len(mother_rows)} mother-chain, {len(branch_rows)} branch)."
        ),
        "mother_chain": mother_chain,
        "branches_checked": len(branch_rows),
        "branch_failures": [],
    }


def get_schema_for_module(
    db: Session, module_name: str, version_id: UUID | None = None
) -> dict | None:
    """Extract the module-specific schema subset for Extraction prompt injection.

    If version_id is None, uses the active version.
    Returns the module's JSON Schema from schema_modules[module_name].
    Returns None if module not found or no active version.
    """
    if version_id:
        from src.ontology.database import get_version_by_id

        version = get_version_by_id(db, version_id)
    else:
        version = get_active_version(db)

    if version is None:
        return None

    return version.schema_modules.get(module_name)


def get_version_history(db: Session, limit: int = 20) -> list[dict]:
    """Return a summary history of schema versions for display.

    Ordered by version_number descending.
    """
    versions = list_versions(db, limit=limit)
    history = []
    for v in versions:
        summary_counts = None
        if v.diff_summary and "summary" in v.diff_summary:
            summary_counts = v.diff_summary["summary"]

        history.append({
            "version_number": v.version_number,
            "created_at": v.created_at.isoformat(),
            "source": v.source.value,
            "reviewer": v.reviewer,
            "changelog": v.changelog,
            "entity_type_count": v.entity_type_count,
            "relationship_type_count": v.relationship_type_count,
            "is_active": v.is_active,
            "diff_summary": summary_counts,
        })
    return history
