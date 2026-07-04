"""Pydantic models and enums for the CQ Test Runner."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CQTestRunStatus(str, Enum):
    """Status of a CQ test run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # F-0035 / ISS-0026 (validation run): a run stuck `running` had no
    # operator escape hatch short of SQL-flipping the row. CANCELLED backs the
    # new POST /api/ontology/cq-test/{run_id}/cancel endpoint and the runner's
    # cooperative-cancel checks.
    CANCELLED = "cancelled"


# F-0035 / ISS-0026: statuses from which a run can never leave. Used by the
# cancel endpoint (409 on terminal) and the runner's failure/cancel guards so a
# late-finishing background task cannot overwrite an operator cancellation.
TERMINAL_CQ_TEST_RUN_STATUSES = frozenset(
    {CQTestRunStatus.COMPLETED, CQTestRunStatus.FAILED, CQTestRunStatus.CANCELLED}
)


class CQTestResult(str, Enum):
    """Result of testing a single CQ against the schema."""

    PASS = "pass"
    FAIL = "fail"
    OUT_OF_SCOPE = "out_of_scope"
    ERROR = "error"


class CQGapType(str, Enum):
    """Type of gap identified when a CQ fails."""

    MISSING_TYPE = "missing_type"
    MISSING_PROPERTY = "missing_property"
    MISSING_CONNECTION = "missing_connection"


class CQGapSeverity(str, Enum):
    """Severity of a CQ gap."""

    MAJOR = "major"
    MINOR = "minor"


class CQTestResultEntry(BaseModel):
    """Result of testing a single CQ against the schema."""

    cq_id: str = Field(description="CQ UUID string")
    cq_text: str = Field(description="The competency question text")
    domain: str = Field(default="other")
    result: CQTestResult = Field(description="pass, fail, out_of_scope, or error")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="LLM confidence in its verdict")
    traced_path: str | None = Field(
        default=None,
        description="Path through schema if passing, e.g. 'Company -> (registered_in) -> Jurisdiction'",
    )
    gap_type: CQGapType | None = Field(default=None, description="Type of gap if failing")
    gap_severity: CQGapSeverity | None = Field(default=None, description="Major or minor gap")
    gap_details: str | None = Field(default=None, description="What specifically is missing")
    reasoning: str = Field(default="", description="LLM explanation of why this passes or fails")
    error_message: str | None = Field(default=None, description="Error details if result is ERROR")


class CQTestRun(BaseModel):
    """A complete CQ test run against an ontology schema version."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    schema_version_id: UUID = Field(description="FK to ontology_versions")
    schema_version_number: int | None = Field(default=None, description="Denormalized version number for display")
    is_proposed_schema: bool = Field(default=False, description="True if testing a proposed schema")
    proposed_schema_json: dict | None = Field(default=None, description="The proposed schema if is_proposed_schema=True")
    total_cqs: int = Field(default=0)
    passing: int = Field(default=0)
    failing: int = Field(default=0)
    out_of_scope: int = Field(default=0)
    errors: int = Field(default=0)
    pass_rate: float = Field(default=0.0, description="passing / (total - out_of_scope). 0.0 if no testable CQs.")
    status: CQTestRunStatus = Field(default=CQTestRunStatus.RUNNING)
    model: str = Field(default="", description="LLM model used for verification")
    provider: str = Field(default="", description="LLM provider used")
    concurrency: int = Field(default=1, description="Number of parallel LLM calls")
    results: list[CQTestResultEntry] = Field(default_factory=list, description="Per-CQ results")
    gap_summary: dict = Field(default_factory=dict, description="Counts by gap_type and gap_severity")
    duration_ms: int = Field(default=0)
    metadata_extra: dict = Field(default_factory=dict)


class CQTestGateResult(BaseModel):
    """Result of the non-regression quality gate."""

    gate_passed: bool = Field(description="Whether the pass rate meets the threshold")
    pass_rate: float = Field(description="Actual pass rate achieved")
    threshold: float = Field(description="Required pass rate (default 0.90)")
    # F-38 — differential gating fields (populated only in differential mode).
    baseline_pass_rate: float | None = Field(
        default=None,
        description="Pass rate of the current/active schema (differential mode)",
    )
    gate_mode: str = Field(
        default="absolute",
        description="'absolute' (pass_rate>=threshold) or 'differential' (pass_rate>=baseline-epsilon)",
    )
    total_cqs: int = Field(default=0)
    passing: int = Field(default=0)
    failing: int = Field(default=0)
    failing_cqs: list[CQTestResultEntry] = Field(default_factory=list, description="Details of failing CQs")
    test_run_id: UUID = Field(description="FK to the test run that produced this result")
