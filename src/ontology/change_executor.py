"""KGCL Change Executor — apply approved proposals (Chunk 48, D392/D393).

Pipeline: parse KGCL → mutate schema JSON → diff → CQ gate → ratify →
DDL sync → flip status. Per-proposal versioning (D392).

Public API: ``async def apply_proposal(db, proposal_id) -> ExecutionResult``.
CLI entry: ``python -m src.ontology.change_executor parse|apply|batch``.
"""

from __future__ import annotations

import copy
import time
from pathlib import Path
from uuid import UUID

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.ontology.diff_engine import compute_om4ov_diff
from src.ontology.kgcl_models import KGCLCommandKind, KGCLParseError, ProposedSchemaChange
from src.ontology.kgcl_parser import parse_kgcl
from src.ontology.models import (
    ProposalStatus,
    ProposalType,
    VersionSource,
    classify_tier,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "change_executor.yaml"


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class ExecutionResult(BaseModel):
    """Outcome of a single proposal execution attempt."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(description="Whether the proposal was applied successfully")
    version_id: UUID | None = Field(default=None, description="Created ontology version ID")
    gate_result: dict | None = Field(default=None, description="CQ gate result dict")
    diff_summary: dict | None = Field(default=None, description="OM4OV diff summary")
    error: str | None = Field(default=None, description="Error message if failed")


# ---------------------------------------------------------------------------
# Schema mutation — apply parsed command to schema JSON
# ---------------------------------------------------------------------------


def _apply_change_to_schema(
    schema: dict,
    change: ProposedSchemaChange,
) -> dict:
    """Apply a parsed KGCL command to a schema dict, returning a new dict.

    Operates on top-level keys of ``schema`` representing entity types,
    relationships, and properties.
    """
    new_schema = copy.deepcopy(schema)
    entity_types: dict = new_schema.get("entity_types", {})
    relationships: dict = new_schema.get("relationships", {})

    kind = change.command_kind

    if kind == KGCLCommandKind.CREATE_CLASS:
        entity_types[change.target_name] = {
            "type": "object",
            "description": f"Entity type {change.target_name}",
            "properties": {},
        }

    elif kind == KGCLCommandKind.OBSOLETE_CLASS:
        entity_types.pop(change.target_name, None)

    elif kind == KGCLCommandKind.CHANGE_DESCRIPTION:
        if change.target_name in entity_types:
            entity_types[change.target_name]["description"] = (
                f"Updated description for {change.target_name}"
            )

    elif kind == KGCLCommandKind.CREATE_RELATIONSHIP:
        relationships[change.target_name] = {
            "description": f"Relationship {change.target_name}",
        }

    elif kind == KGCLCommandKind.OBSOLETE_RELATIONSHIP:
        relationships.pop(change.target_name, None)

    elif kind == KGCLCommandKind.CHANGE_RELATIONSHIP:
        if change.target_name in relationships:
            relationships[change.target_name]["description"] = (
                f"Updated relationship {change.target_name}"
            )

    elif kind == KGCLCommandKind.ADD_PROPERTY:
        entity = change.entity_name or change.target_name
        if entity in entity_types:
            props = entity_types[entity].setdefault("properties", {})
            prop_name = change.property_name or change.target_name
            props[prop_name] = {"type": "string"}

    elif kind == KGCLCommandKind.REMOVE_PROPERTY:
        entity = change.entity_name or change.target_name
        if entity in entity_types:
            props = entity_types[entity].get("properties", {})
            prop_name = change.property_name or change.target_name
            props.pop(prop_name, None)

    elif kind == KGCLCommandKind.CHANGE_PROPERTY:
        entity = change.entity_name or change.target_name
        if entity in entity_types:
            props = entity_types[entity].get("properties", {})
            prop_name = change.property_name or change.target_name
            if prop_name in props:
                props[prop_name]["description"] = f"Updated property {prop_name}"

    elif kind == KGCLCommandKind.ADD_SYNONYM:
        if change.target_name in entity_types:
            synonyms = entity_types[change.target_name].setdefault("synonyms", [])
            if change.synonym and change.synonym not in synonyms:
                synonyms.append(change.synonym)

    elif kind == KGCLCommandKind.RENAME_PROPERTY:
        entity = change.entity_name or change.target_name
        if entity in entity_types:
            props = entity_types[entity].get("properties", {})
            old_name = change.property_name or change.target_name
            new_name = change.new_name
            if old_name in props and new_name:
                props[new_name] = props.pop(old_name)

    elif kind == KGCLCommandKind.SPLIT_CLASS:
        # Split-class atomicity (R2 mitigation): delete original + add new
        # types in a single mutation pass. Property data sourced from
        # pre-mutation schema (not from diff output — _diff_properties()
        # at diff_engine.py:183 only examines common_types intersection
        # at line 189, so deleted types are invisible).
        original_props = {}
        if change.target_name in entity_types:
            original_props = entity_types[change.target_name].get("properties", {})
            del entity_types[change.target_name]
        for new_type in change.split_into or []:
            entity_types[new_type] = {
                "type": "object",
                "description": f"Split from {change.target_name}",
                "properties": copy.deepcopy(original_props),
            }

    elif kind == KGCLCommandKind.MOVE_CLASS:
        # Move class just updates a parent reference if tracked.
        if change.target_name in entity_types:
            entity_types[change.target_name]["parent"] = change.new_parent

    elif kind == KGCLCommandKind.CHANGE_DOMAIN_RANGE:
        if change.target_name in relationships and change.change_target and change.to_type:
            relationships[change.target_name][change.change_target] = change.to_type

    new_schema["entity_types"] = entity_types
    new_schema["relationships"] = relationships
    return new_schema


# ---------------------------------------------------------------------------
# Core executor
# ---------------------------------------------------------------------------


async def apply_proposal(
    db: Session,
    proposal_id: UUID,
) -> ExecutionResult:
    """Execute a single approved proposal.

    D392 per-proposal versioning: each execution reads the latest active
    schema, applies the change, and ratifies a new version. D393 synchronous
    execute — called directly from the API route (scoped D246 exception).
    """
    from src.ontology.database import (
        get_active_version,
        get_proposal_by_id,
        update_proposal_status,
    )

    config = _load_config()
    threshold = config.get("cq_gate_threshold", 0.90)
    # F-38 (default flipped): the gate is DIFFERENTIAL by default — a proposal
    # passes when it does not regress the active schema's CQ pass rate by more
    # than epsilon. The absolute 0.90 gate froze evolution on any honest
    # gap-rich schema (measured 0.567 with a good judge on the validation
    # corpus). `cq_gate_mode: absolute` restores the legacy behavior.
    gate_mode = config.get("cq_gate_mode", "differential")
    # F2-09: default epsilon raised 0.05 -> 0.10. A repeat validation run measured the CQ
    # judge's run-to-run variance at +/-2 CQs on identical schemas (0.233 vs
    # 0.300 across 15 runs at n=30); 0.05 is only 1.5 CQs at n=30, INSIDE the
    # noise floor, so a no-regression change could be refused whenever the
    # baseline landed on a high draw (observed: the revert was refused once).
    # 0.10 = 3 CQs at n=30, above the measured 2-CQ noise band.
    gate_epsilon = float(config.get("cq_gate_epsilon", 0.10))
    enable_ddl = config.get("enable_graph_ddl_sync", True)
    partial_ddl_policy = config.get("partial_ddl_policy", "record_and_alert")

    start_time = time.monotonic()
    tier_label = "2"  # Default; overridden below.

    # --- Load proposal ---
    proposal = get_proposal_by_id(db, proposal_id)
    if proposal is None:
        return ExecutionResult(success=False, error="Proposal not found")

    tier_label = str(classify_tier(proposal.proposal_type))

    if proposal.status == ProposalStatus.APPLIED:
        return ExecutionResult(
            success=False,
            error="Proposal already applied (idempotency guard)",
        )

    if proposal.status != ProposalStatus.APPROVED:
        return ExecutionResult(
            success=False,
            error=f"Proposal status is '{proposal.status.value}', expected 'approved'",
        )

    # --- Parse KGCL ---
    try:
        parsed = parse_kgcl(proposal.kgcl_command)
    except KGCLParseError as e:
        _record_counter(tier=tier_label, outcome="error", start=start_time)
        return ExecutionResult(success=False, error=f"KGCL parse error: {e.message}")

    # --- Read active schema ---
    active_version = get_active_version(db)
    if active_version is None:
        _record_counter(tier=tier_label, outcome="error", start=start_time)
        return ExecutionResult(success=False, error="No active ontology version found")

    # --- Apply change ---
    old_schema = active_version.schema_json
    new_schema = _apply_change_to_schema(old_schema, parsed)

    # --- Compute diff ---
    diff_summary = compute_om4ov_diff(old_schema, new_schema)

    # --- CQ gate ---
    # F2-09 revert exemption: an inverse proposal created by the revert route
    # (reviewer == "system:revert") RESTORES a previously-gated state — gating
    # it re-litigates a schema the gate already approved, and a validation run showed the
    # judge's run-to-run variance can (and did) refuse the SAFETY path itself.
    is_revert = getattr(proposal, "reviewer", None) == "system:revert"
    if is_revert:
        log.info(
            "change_executor.cq_gate_skipped_for_revert",
            proposal_id=str(proposal_id),
        )

    try:
        from src.ontology.cq_test_runner import run_non_regression_gate

        # F-38 baseline wiring, F2-08 rework: in differential mode the
        # baseline is the latest completed CQ run on the active version OR ANY
        # ANCESTOR (previous_version_id walk). Fetching only the active
        # version's runs meant every ratification erased the baseline and
        # bricked all subsequent autonomy (4 deterministic occurrences
        # observed: executor, daemon apply x2, revert). Absolute fallback remains
        # for a truly fresh deployment with no runs anywhere in the chain.
        baseline_pass_rate: float | None = None
        if gate_mode == "differential":
            # Baseline fetch is best-effort: an unreadable/absent baseline
            # degrades to the absolute threshold (never errors the execute).
            try:
                from src.ontology.cq_test_runner import (
                    get_latest_test_run_in_ancestry,
                )

                baseline_run = get_latest_test_run_in_ancestry(
                    db, active_version.id
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "change_executor.cq_gate_baseline_fetch_failed",
                    error=str(exc),
                )
                baseline_run = None
            rate = getattr(baseline_run, "pass_rate", None)
            if isinstance(rate, (int, float)):
                baseline_pass_rate = float(rate)
                log.info(
                    "change_executor.cq_gate_baseline",
                    baseline_pass_rate=baseline_pass_rate,
                    baseline_run_id=str(baseline_run.id),
                    epsilon=gate_epsilon,
                )
            else:
                log.warning(
                    "change_executor.cq_gate_no_baseline_falling_back_absolute",
                    active_version_id=str(active_version.id),
                    threshold=threshold,
                )

        if is_revert:
            gate_result = None
            gate_dict = {"skipped": "revert_exemption"}
        else:
            gate_result = await run_non_regression_gate(
                db,
                proposed_schema_json=new_schema,
                threshold=threshold,
                baseline_pass_rate=baseline_pass_rate,
                epsilon=gate_epsilon,
            )
            gate_dict = gate_result.model_dump(mode="json") if hasattr(gate_result, "model_dump") else {}
    except Exception as exc:
        log.error("change_executor.cq_gate_error", error=str(exc))
        _record_counter(tier=tier_label, outcome="error", start=start_time)
        return ExecutionResult(
            success=False,
            error=f"CQ gate error: {exc}",
            diff_summary=diff_summary,
        )

    if gate_result is not None and not gate_result.gate_passed:
        # --- CQ gate refusal → proposal STAYS APPROVED (F2-08) ---
        # The old flip to REJECTED destroyed the human reviewer's decision
        # record whenever the gate refused for an environmental reason (stale
        # baseline, judge variance). A gate refusal is an EXECUTION outcome,
        # not a review outcome: keep the HITL status, record the refusal in
        # metadata, and let the operator (or the next daemon tick, once the
        # environment recovers) retry the same approved proposal.
        update_proposal_status(
            db,
            proposal_id,
            ProposalStatus.APPROVED,
            metadata_extra={
                "cq_gate_refusal": "CQ non-regression gate failed",
                "gate_result": gate_dict,
            },
        )
        _record_counter(tier=tier_label, outcome="gate_failed", start=start_time)
        return ExecutionResult(
            success=False,
            gate_result=gate_dict,
            diff_summary=diff_summary,
            error="CQ non-regression gate failed (proposal remains approved for retry)",
        )

    # --- CQ gate pass → ratify ---
    from src.ontology.review_ops import partition_schema_by_module
    from src.ontology.schema_store import ratify_version

    # F-0010 / ISS-0046: recompute schema_modules from the NEW schema_json
    # instead of carrying the predecessor's partition forward. The stale
    # carry-forward left modules disagreeing with schema_json (observed:
    # a module holding 47 relationships while schema_json carried 49)
    # because adaptive_evolution mutations never re-partitioned.
    new_version = ratify_version(
        db,
        schema_json=new_schema,
        schema_modules=partition_schema_by_module(new_schema),
        source=VersionSource.ADAPTIVE_EVOLUTION,
        kgcl_commands=[proposal.kgcl_command],
        proposal_id=proposal_id,
        promotion_gate_passed=True,
        promotion_gate_details=gate_dict,
    )

    # --- DDL sync ---
    ddl_error: str | None = None
    if enable_ddl:
        try:
            from src.graph.schema_migration import migrate_schema

            await migrate_schema(
                db,
                client=_get_arcade_client(),
                from_version=active_version.version_number,
                to_version=new_version.version_number,
            )
        except Exception as exc:
            ddl_error = str(exc)
            log.error(
                "change_executor.partial_ddl_failure",
                policy=partial_ddl_policy,
                error=ddl_error,
            )
            # Per spec: partial DDL does NOT roll back the schema version.
            if partial_ddl_policy == "record_and_alert":
                update_proposal_status(
                    db,
                    proposal_id,
                    ProposalStatus.APPLIED,
                    resulting_version_id=new_version.id,
                    metadata_extra={"partial_ddl": ddl_error},
                )
                _record_counter(tier=tier_label, outcome="applied", start=start_time)
                return ExecutionResult(
                    success=True,
                    version_id=new_version.id,
                    gate_result=gate_dict,
                    diff_summary=diff_summary,
                )

    # --- Flip status to APPLIED ---
    update_proposal_status(
        db,
        proposal_id,
        ProposalStatus.APPLIED,
        resulting_version_id=new_version.id,
    )

    _record_counter(tier=tier_label, outcome="applied", start=start_time)

    return ExecutionResult(
        success=True,
        version_id=new_version.id,
        gate_result=gate_dict,
        diff_summary=diff_summary,
    )


# ---------------------------------------------------------------------------
# ArcadeDB client helper
# ---------------------------------------------------------------------------

def _get_arcade_client():
    """Lazy import to avoid import-time dependency on ArcadeDB config."""
    from src.graph.arcade_client import ArcadeClient
    from src.graph.config import ArcadeConfig

    config = ArcadeConfig()
    return ArcadeClient(config)


# ---------------------------------------------------------------------------
# OTel helpers
# ---------------------------------------------------------------------------

def _record_counter(*, tier: str, outcome: str, start: float) -> None:
    """Best-effort OTel recording — never fails the executor."""
    try:
        from src.analytics.metrics import (
            record_proposal_executed,
            record_proposal_execution_duration,
        )

        record_proposal_executed(tier=tier, outcome=outcome)
        record_proposal_execution_duration(
            tier=tier, duration=time.monotonic() - start
        )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# CLI entry point (D246 — batch runs out-of-process only)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import asyncio
    import json
    import sys

    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    from src.ontology.kgcl_parser import parse_kgcl as _cli_parse

    parser = argparse.ArgumentParser(
        prog="python -m src.ontology.change_executor",
        description="KGCL Change Executor CLI (Chunk 48, D246)",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # --- parse ---
    parse_sub = subparsers.add_parser("parse", help="Parse a KGCL command string")
    parse_sub.add_argument("command", help="KGCL command string to parse")

    # --- apply ---
    apply_sub = subparsers.add_parser("apply", help="Execute a single approved proposal")
    apply_sub.add_argument("--proposal-id", required=True, help="UUID of the proposal")
    apply_sub.add_argument("--dry-run", action="store_true", help="Print plan without persisting")

    # --- batch ---
    batch_sub = subparsers.add_parser("batch", help="Process all approved proposals sequentially")
    batch_sub.add_argument("--dry-run", action="store_true", help="Print batch plan without persisting")
    batch_sub.add_argument("--limit", type=int, default=None, help="Max proposals to process")

    args = parser.parse_args()

    if args.subcommand == "parse":
        try:
            result = _cli_parse(args.command)
            print(json.dumps(result.model_dump(mode="json"), indent=2))
        except KGCLParseError as e:
            print(f"Parse error: {e.message}", file=sys.stderr)
            sys.exit(1)

    elif args.subcommand == "apply":
        from src.shared.database import get_session_factory

        proposal_uuid = UUID(args.proposal_id)
        if args.dry_run:
            session_factory = get_session_factory()
            db = session_factory()
            try:
                from src.ontology.database import get_proposal_by_id

                proposal = get_proposal_by_id(db, proposal_uuid)
                if proposal is None:
                    print("Proposal not found", file=sys.stderr)
                    sys.exit(1)
                print(f"[dry-run] Would execute proposal {proposal_uuid}")
                print(f"  status: {proposal.status.value}")
                print(f"  kgcl_command: {proposal.kgcl_command}")
                print(f"  tier: {classify_tier(proposal.proposal_type)}")
            finally:
                db.close()
        else:
            session_factory = get_session_factory()
            db = session_factory()
            try:
                result = asyncio.run(apply_proposal(db, proposal_uuid))
                print(json.dumps(result.model_dump(mode="json"), indent=2))
                if not result.success:
                    sys.exit(1)
            finally:
                db.close()

    elif args.subcommand == "batch":
        from src.ontology.database import list_proposals as _list_proposals
        from src.shared.database import get_session_factory

        session_factory = get_session_factory()
        db = session_factory()
        try:
            approved = _list_proposals(
                db,
                status=ProposalStatus.APPROVED,
                limit=args.limit or 1000,
                offset=0,
            )
            if not approved:
                print("No approved proposals to process.")
                sys.exit(0)

            if args.dry_run:
                print(f"[dry-run] Would process {len(approved)} approved proposal(s):")
                for p in approved:
                    print(f"  - {p.id}: {p.kgcl_command} (tier {classify_tier(p.proposal_type)})")
            else:
                for i, p in enumerate(approved, 1):
                    print(f"[{i}/{len(approved)}] Executing {p.id}...")
                    # D392: per-proposal versioning — reads fresh active version
                    # after each successful execution.
                    result = asyncio.run(apply_proposal(db, p.id))
                    print(f"  success={result.success}, version_id={result.version_id}")
                    if not result.success:
                        print(f"  error: {result.error}")
        finally:
            db.close()

    else:
        parser.print_help()
        sys.exit(1)
