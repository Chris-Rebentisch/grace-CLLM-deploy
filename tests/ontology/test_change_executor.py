"""Tests for KGCL Change Executor (Chunk 48, CP3).

Tests mock DB and CQ gate calls to isolate executor logic.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.ontology.change_executor import (
    ExecutionResult,
    _apply_change_to_schema,
    apply_proposal,
)
from src.ontology.kgcl_models import KGCLCommandKind
from src.ontology.models import (
    ProposalPriority,
    ProposalStatus,
    ProposalType,
    SchemaProposal,
    classify_tier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROPOSAL_ID = uuid4()
_VERSION_ID = uuid4()
_ACTIVE_VERSION_ID = uuid4()

_BASE_SCHEMA = {
    "entity_types": {
        "Person": {
            "type": "object",
            "description": "A person",
            "properties": {"name": {"type": "string"}},
        },
    },
    "relationships": {
        "employs": {"description": "Employment", "domain": "Company", "range": "Person"},
    },
}


def _make_proposal(
    *,
    proposal_id: UUID | None = None,
    status: ProposalStatus = ProposalStatus.APPROVED,
    kgcl_command: str = "create class 'NewEntity'",
    proposal_type: ProposalType = ProposalType.ADD_ENTITY_TYPE,
) -> SchemaProposal:
    from src.ontology.evidence_bundle import EvidenceBundle

    return SchemaProposal(
        id=proposal_id or _PROPOSAL_ID,
        proposal_type=proposal_type,
        change_tier=classify_tier(proposal_type),
        kgcl_command=kgcl_command,
        proposed_diff={},
        evidence=EvidenceBundle(
            source_signal_ids=[],
            signal_type="A",
            signal_strength=0.8,
            affected_entity_types=["Person"],
            ontology_module="default",
        ),
        raw_confidence=1.0,
        status=status,
        current_schema_version_id=_ACTIVE_VERSION_ID,
    )


def _make_active_version():
    from src.ontology.models import OntologyVersion, VersionSource

    return OntologyVersion(
        id=_ACTIVE_VERSION_ID,
        version_number=1,
        schema_json=_BASE_SCHEMA,
        schema_modules={"default": _BASE_SCHEMA},
        hash_chain="abc123",
        source=VersionSource.MANUAL,
    )


def _make_gate_result(passed: bool = True):
    mock = MagicMock()
    mock.gate_passed = passed
    mock.pass_rate = 0.95 if passed else 0.80
    mock.model_dump.return_value = {"gate_passed": passed, "pass_rate": mock.pass_rate}
    return mock


def _make_ratified_version():
    from src.ontology.models import OntologyVersion, VersionSource

    return OntologyVersion(
        id=uuid4(),
        version_number=2,
        schema_json=_BASE_SCHEMA,
        schema_modules={"default": _BASE_SCHEMA},
        hash_chain="def456",
        source=VersionSource.ADAPTIVE_EVOLUTION,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_DB_MOD = "src.ontology.database"
_CQ_MOD = "src.ontology.cq_test_runner"
_STORE_MOD = "src.ontology.schema_store"
_MIGRATE_MOD = "src.graph.schema_migration"
_EXEC_MOD = "src.ontology.change_executor"


class TestApplyProposalHappyPath:
    def test_approved_to_applied(self) -> None:
        """Happy path: approved → applied with version created."""
        proposal = _make_proposal()
        active = _make_active_version()
        gate = _make_gate_result(passed=True)
        ratified = _make_ratified_version()

        with (
            patch(f"{_DB_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_DB_MOD}.get_active_version", return_value=active),
            patch(f"{_CQ_MOD}.run_non_regression_gate", new_callable=AsyncMock, return_value=gate),
            patch(f"{_STORE_MOD}.ratify_version", return_value=ratified),
            patch(f"{_MIGRATE_MOD}.migrate_schema", new_callable=AsyncMock),
            patch(f"{_DB_MOD}.update_proposal_status", return_value=proposal),
            patch(f"{_EXEC_MOD}._record_counter"),
        ):
            result = asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        assert result.success is True
        assert result.version_id == ratified.id
        assert result.diff_summary is not None
        assert result.error is None


class TestCQGateRejection:
    def test_gate_failure_keeps_approved(self) -> None:
        """CQ gate refusal keeps the HITL decision (F2-08).

        The old contract flipped approved → REJECTED, destroying the human
        reviewer's record whenever the gate refused for an environmental
        reason (stale baseline, judge variance). A refusal is an EXECUTION
        outcome: the proposal stays APPROVED with the refusal in metadata.
        """
        proposal = _make_proposal()
        active = _make_active_version()
        gate = _make_gate_result(passed=False)

        with (
            patch(f"{_DB_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_DB_MOD}.get_active_version", return_value=active),
            patch(f"{_CQ_MOD}.run_non_regression_gate", new_callable=AsyncMock, return_value=gate),
            patch(f"{_DB_MOD}.update_proposal_status", return_value=proposal) as mock_update,
            patch(f"{_EXEC_MOD}._record_counter"),
        ):
            result = asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        assert result.success is False
        assert "gate failed" in (result.error or "").lower()
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][2] == ProposalStatus.APPROVED
        assert "cq_gate_refusal" in (call_args[1].get("metadata_extra") or {})


class TestPartialDDL:
    def test_partial_ddl_record_and_alert(self) -> None:
        """Partial DDL handling: record_and_alert policy still marks applied."""
        proposal = _make_proposal()
        active = _make_active_version()
        gate = _make_gate_result(passed=True)
        ratified = _make_ratified_version()

        with (
            patch(f"{_DB_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_DB_MOD}.get_active_version", return_value=active),
            patch(f"{_CQ_MOD}.run_non_regression_gate", new_callable=AsyncMock, return_value=gate),
            patch(f"{_STORE_MOD}.ratify_version", return_value=ratified),
            patch(f"{_MIGRATE_MOD}.migrate_schema", new_callable=AsyncMock, side_effect=RuntimeError("DDL sync failed")),
            patch(f"{_DB_MOD}.update_proposal_status", return_value=proposal) as mock_update,
            patch(f"{_EXEC_MOD}._record_counter"),
        ):
            result = asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        assert result.success is True
        assert result.version_id is not None
        # Should have recorded partial_ddl in metadata_extra.
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("metadata_extra", {}).get("partial_ddl") is not None


class TestGuards:
    def test_wrong_status_returns_error(self) -> None:
        """Only approved proposals can be executed."""
        proposal = _make_proposal(status=ProposalStatus.PENDING)

        with patch(f"{_DB_MOD}.get_proposal_by_id", return_value=proposal):
            result = asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        assert result.success is False
        assert "expected 'approved'" in (result.error or "").lower()

    def test_proposal_not_found(self) -> None:
        with patch(f"{_DB_MOD}.get_proposal_by_id", return_value=None):
            result = asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        assert result.success is False
        assert "not found" in (result.error or "").lower()

    def test_idempotency_guard_applied(self) -> None:
        """Re-executing an applied proposal returns error, not a status flip."""
        proposal = _make_proposal(status=ProposalStatus.APPLIED)

        with patch(f"{_DB_MOD}.get_proposal_by_id", return_value=proposal):
            result = asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        assert result.success is False
        assert "already applied" in (result.error or "").lower()

    def test_no_active_version(self) -> None:
        proposal = _make_proposal()

        with (
            patch(f"{_DB_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_DB_MOD}.get_active_version", return_value=None),
            patch(f"{_EXEC_MOD}._record_counter"),
        ):
            result = asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        assert result.success is False
        assert "no active" in (result.error or "").lower()


class TestSchemaMutation:
    def test_split_class_atomicity(self) -> None:
        """Split-class: delete original + add two new types atomically."""
        schema = {
            "entity_types": {
                "Entity": {
                    "type": "object",
                    "description": "Original",
                    "properties": {"name": {"type": "string"}},
                },
            },
            "relationships": {},
        }
        from src.ontology.kgcl_models import ProposedSchemaChange

        change = ProposedSchemaChange(
            command_kind=KGCLCommandKind.SPLIT_CLASS,
            target_name="Entity",
            split_into=["PersonEntity", "OrgEntity"],
        )
        result = _apply_change_to_schema(schema, change)
        assert "Entity" not in result["entity_types"]
        assert "PersonEntity" in result["entity_types"]
        assert "OrgEntity" in result["entity_types"]
        # Property carryover from original.
        assert "name" in result["entity_types"]["PersonEntity"]["properties"]
        assert "name" in result["entity_types"]["OrgEntity"]["properties"]

    def test_change_relationship_vs_change_domain_range(self) -> None:
        """CHANGE_RELATIONSHIP (inferred) vs CHANGE_DOMAIN_RANGE (explicit) produce different results."""
        from src.ontology.kgcl_models import ProposedSchemaChange

        schema = {
            "entity_types": {},
            "relationships": {
                "employs": {"description": "Employment", "domain": "Company", "range": "Person"},
            },
        }

        # CHANGE_RELATIONSHIP — only updates description.
        change_rel = ProposedSchemaChange(
            command_kind=KGCLCommandKind.CHANGE_RELATIONSHIP,
            target_name="employs",
        )
        result_rel = _apply_change_to_schema(schema, change_rel)
        assert result_rel["relationships"]["employs"]["domain"] == "Company"
        assert "Updated" in result_rel["relationships"]["employs"]["description"]

        # CHANGE_DOMAIN_RANGE — updates domain.
        change_dr = ProposedSchemaChange(
            command_kind=KGCLCommandKind.CHANGE_DOMAIN_RANGE,
            target_name="employs",
            to_type="Organization",
            change_target="domain",
        )
        result_dr = _apply_change_to_schema(schema, change_dr)
        assert result_dr["relationships"]["employs"]["domain"] == "Organization"


class TestBatchSequential:
    def test_per_proposal_versioning(self) -> None:
        """D392: batch of 2 proposals → 2 separate versions."""
        proposals = [
            _make_proposal(proposal_id=uuid4(), kgcl_command="create class 'TypeA'"),
            _make_proposal(proposal_id=uuid4(), kgcl_command="create class 'TypeB'"),
        ]
        active = _make_active_version()
        gate = _make_gate_result(passed=True)

        version_ids = []
        call_count = 0

        def _make_new_version(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            v = _make_ratified_version()
            version_ids.append(v.id)
            return v

        for p in proposals:
            with (
                patch(f"{_DB_MOD}.get_proposal_by_id", return_value=p),
                patch(f"{_DB_MOD}.get_active_version", return_value=active),
                patch(f"{_CQ_MOD}.run_non_regression_gate", new_callable=AsyncMock, return_value=gate),
                patch(f"{_STORE_MOD}.ratify_version", side_effect=_make_new_version),
                patch(f"{_MIGRATE_MOD}.migrate_schema", new_callable=AsyncMock),
                patch(f"{_DB_MOD}.update_proposal_status", return_value=p),
                patch(f"{_EXEC_MOD}._record_counter"),
            ):
                result = asyncio.run(apply_proposal(MagicMock(), p.id))
                assert result.success is True

        assert len(version_ids) == 2
        assert version_ids[0] != version_ids[1]


class TestCounterWiring:
    def test_counter_called_on_success(self) -> None:
        """Verify _record_counter is called with correct labels."""
        proposal = _make_proposal()
        active = _make_active_version()
        gate = _make_gate_result(passed=True)
        ratified = _make_ratified_version()

        with (
            patch(f"{_DB_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_DB_MOD}.get_active_version", return_value=active),
            patch(f"{_CQ_MOD}.run_non_regression_gate", new_callable=AsyncMock, return_value=gate),
            patch(f"{_STORE_MOD}.ratify_version", return_value=ratified),
            patch(f"{_MIGRATE_MOD}.migrate_schema", new_callable=AsyncMock),
            patch(f"{_DB_MOD}.update_proposal_status", return_value=proposal),
            patch(f"{_EXEC_MOD}._record_counter") as mock_counter,
        ):
            asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        mock_counter.assert_called_once()
        call_kwargs = mock_counter.call_args[1]
        assert call_kwargs["tier"] == str(classify_tier(proposal.proposal_type))
        assert call_kwargs["outcome"] == "applied"


class TestExecutionResult:
    def test_model_shape(self) -> None:
        r = ExecutionResult(success=True, version_id=uuid4())
        d = r.model_dump(mode="json")
        assert "success" in d
        assert "version_id" in d
        assert "gate_result" in d
        assert "diff_summary" in d
        assert "error" in d


class TestSchemaModulesRepartition:
    """F-0010 / ISS-0046: ratify must repartition modules from the NEW schema."""

    def test_ratify_recomputes_modules_from_new_schema(self) -> None:
        """schema_modules is partition_schema_by_module(new_schema), not the
        predecessor's stale partition (observed drift: module carried 47
        relationships while schema_json carried 49)."""
        from src.ontology.review_ops import partition_schema_by_module

        proposal = _make_proposal()  # create class 'NewEntity'
        active = _make_active_version()
        gate = _make_gate_result(passed=True)
        ratified = _make_ratified_version()

        with (
            patch(f"{_DB_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_DB_MOD}.get_active_version", return_value=active),
            patch(f"{_CQ_MOD}.run_non_regression_gate", new_callable=AsyncMock, return_value=gate),
            patch(f"{_STORE_MOD}.ratify_version", return_value=ratified) as mock_ratify,
            patch(f"{_MIGRATE_MOD}.migrate_schema", new_callable=AsyncMock),
            patch(f"{_DB_MOD}.update_proposal_status", return_value=proposal),
            patch(f"{_EXEC_MOD}._record_counter"),
        ):
            result = asyncio.run(apply_proposal(MagicMock(), _PROPOSAL_ID))

        assert result.success is True
        kwargs = mock_ratify.call_args.kwargs
        new_schema = kwargs["schema_json"]
        # The mutation landed in schema_json...
        assert "NewEntity" in new_schema["entity_types"]
        # ...and schema_modules is recomputed FROM that schema_json,
        # not carried forward from the predecessor version.
        assert kwargs["schema_modules"] == partition_schema_by_module(new_schema)
        assert kwargs["schema_modules"] != active.schema_modules
        # The new entity is present in some module partition.
        assert any(
            "NewEntity" in module["entity_types"]
            for module in kwargs["schema_modules"].values()
        )
