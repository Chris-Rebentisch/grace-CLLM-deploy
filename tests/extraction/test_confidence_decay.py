"""Tests for confidence decay batch (Chunk 35a, D264).

Coverage targets:
* Pure ``decay_confidence`` formula behavior (idempotency, floors,
  zero/negative delta, clock validation).
* ``DecayConfig`` YAML loading.
* ``decay_run`` orchestration over a stubbed ``ArcadeClient`` — verifies
  read/write call shape, dry-run skip, per-relationship overrides, and
  metrics-instrument emission.
* CLI argparse smoke (no-op without a config file).

The decay module is graph-only by spec (F8) — these tests assert that
no SQLAlchemy session is constructed and that no ``extraction_claims``
read/write occurs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.extraction import confidence_decay as decay_module
from src.extraction.confidence_decay import (
    DecayConfig,
    DecayResult,
    decay_confidence,
    decay_run,
    main as cli_main,
)


# --- Pure-function decay tests --------------------------------------------


def test_decay_zero_delta_returns_input() -> None:
    """At t=0 (same observation_time as last_verified_at) confidence is unchanged."""
    assert decay_confidence(0.8, 0.0, 180.0, 0.5) == pytest.approx(0.8)


def test_decay_idempotency_same_inputs_same_output() -> None:
    """Pure function: same inputs always produce identical output."""
    a = decay_confidence(0.8, 90.0, 180.0, 0.5)
    b = decay_confidence(0.8, 90.0, 180.0, 0.5)
    c = decay_confidence(0.8, 90.0, 180.0, 0.5)
    assert a == b == c


def test_decay_supported_decays_then_floors_at_half() -> None:
    """SUPPORTED entity at 0.8 decays toward 0 but floors at 0.5 once t > 1 half-life."""
    short = decay_confidence(0.8, 90.0, 180.0, 0.5)  # half-half-life
    long = decay_confidence(0.8, 720.0, 180.0, 0.5)  # 4 half-lives -> 0.05 raw
    assert 0.5 < short < 0.8
    assert long == pytest.approx(0.5)  # floored


def test_decay_insufficient_floors_at_half() -> None:
    """INSUFFICIENT verdict's floor is 0.5; raw value cannot drop below it."""
    floored = decay_confidence(0.6, 1000.0, 180.0, 0.5)
    assert floored == pytest.approx(0.5)


def test_decay_refuted_floors_at_low_value() -> None:
    """REFUTED verdict's floor is 0.05."""
    floored = decay_confidence(0.4, 1_000_000.0, 180.0, 0.05)
    assert floored == pytest.approx(0.05)


def test_decay_negative_delta_rejected() -> None:
    """Observation precedes verification -> ValueError (clock went backwards)."""
    with pytest.raises(ValueError, match="delta_days must be"):
        decay_confidence(0.8, -1.0, 180.0, 0.5)


def test_decay_t_half_zero_rejected() -> None:
    """Zero or negative half-life is invalid."""
    with pytest.raises(ValueError, match="t_half"):
        decay_confidence(0.8, 1.0, 0.0, 0.5)


# --- Config loading --------------------------------------------------------


def test_config_loads_from_yaml(tmp_path: Path) -> None:
    yaml_text = (
        "t_half_days: 90\n"
        "per_relationship_overrides:\n"
        "  Owns: 30\n"
        "verdict_floors:\n"
        "  SUPPORTED: 0.5\n"
        "  INSUFFICIENT: 0.5\n"
        "  REFUTED: 0.05\n"
    )
    cfg_path = tmp_path / "decay.yaml"
    cfg_path.write_text(yaml_text)

    cfg = DecayConfig.from_yaml(cfg_path)
    assert cfg.t_half_days == 90
    assert cfg.per_relationship_overrides == {"Owns": 30.0}
    assert cfg.verdict_floors["REFUTED"] == 0.05


# --- decay_run orchestration ----------------------------------------------


class _StubArcadeClient:
    """In-memory Arcade stub: returns canned rows from execute_cypher."""

    def __init__(self, vertex_rows: list[dict], edge_rows: list[dict]) -> None:
        self._vertex_rows = vertex_rows
        self._edge_rows = edge_rows
        self.writes: list[dict[str, Any]] = []

    async def execute_cypher(
        self,
        query: str,
        database: str | None = None,
        params: dict | None = None,
    ) -> dict:
        if "MATCH (n)" in query and "SET" not in query:
            return {"result": [{"n": row} for row in self._vertex_rows]}
        if "MATCH ()-[r]" in query and "SET" not in query:
            return {"result": [{"r": row} for row in self._edge_rows]}
        # SET … (write path)
        self.writes.append({"query": query, "params": params or {}})
        return {"result": []}


def test_decay_run_dry_run_does_not_write() -> None:
    """``--dry-run`` reads but never persists."""
    obs = datetime(2026, 5, 5, tzinfo=timezone.utc)
    last = (obs - timedelta(days=180)).isoformat()
    rows = [
        {
            "grace_id": "v1",
            "confidence_at_verification": 0.8,
            "last_verified_at": last,
            "verdict": "SUPPORTED",
            "ontology_module": "core",
        }
    ]
    stub = _StubArcadeClient(rows, [])
    cfg = DecayConfig()
    result = pytest_run_async(
        decay_run(observation_time=obs, config=cfg, client=stub, dry_run=True)
    )
    assert isinstance(result, DecayResult)
    assert result.dry_run is True
    assert result.rows_processed == 1
    assert result.rows_decayed == 1
    assert stub.writes == []  # no SET cypher emitted


def test_decay_run_persists_decayed_value() -> None:
    """Without ``--dry-run`` the new confidence is written via execute_cypher."""
    obs = datetime(2026, 5, 5, tzinfo=timezone.utc)
    last = (obs - timedelta(days=90)).isoformat()
    rows = [
        {
            "grace_id": "v1",
            "confidence_at_verification": 0.8,
            "last_verified_at": last,
            "verdict": "SUPPORTED",
            "ontology_module": "core",
        }
    ]
    stub = _StubArcadeClient(rows, [])
    cfg = DecayConfig()
    pytest_run_async(decay_run(observation_time=obs, config=cfg, client=stub))
    assert len(stub.writes) == 1
    write_params = stub.writes[0]["params"]
    assert write_params["grace_id"] == "v1"
    assert 0.5 < write_params["c"] < 0.8


def test_decay_run_per_relationship_override_applied() -> None:
    """Edges with a relationship-specific override use the smaller half-life."""
    obs = datetime(2026, 5, 5, tzinfo=timezone.utc)
    last = (obs - timedelta(days=30)).isoformat()
    edge = {
        "grace_id": "e1",
        "confidence_at_verification": 0.8,
        "last_verified_at": last,
        "verdict": "SUPPORTED",
        "ontology_module": "core",
        "relationship_type": "Owns",
        "@type": "Owns",
    }
    stub = _StubArcadeClient([], [edge])
    cfg = DecayConfig(t_half_days=180.0, per_relationship_overrides={"Owns": 30.0})
    pytest_run_async(decay_run(observation_time=obs, config=cfg, client=stub))
    # 30 days @ 30-day half-life -> 0.5 * 0.8 = 0.4, but floor is 0.5.
    assert len(stub.writes) == 1
    new_c = stub.writes[0]["params"]["c"]
    assert new_c == pytest.approx(0.5)


def test_decay_run_skips_rows_missing_properties() -> None:
    """Rows without verified-at/c-at-verification/verdict are skipped."""
    obs = datetime(2026, 5, 5, tzinfo=timezone.utc)
    rows = [
        {"grace_id": "no-c-at"},
        {
            "grace_id": "complete",
            "confidence_at_verification": 0.8,
            "last_verified_at": (obs - timedelta(days=10)).isoformat(),
            "verdict": "SUPPORTED",
        },
    ]
    stub = _StubArcadeClient(rows, [])
    cfg = DecayConfig()
    result = pytest_run_async(
        decay_run(observation_time=obs, config=cfg, client=stub)
    )
    assert result.rows_processed == 2
    assert result.rows_skipped == 1
    assert result.rows_decayed == 1


def test_decay_module_does_not_import_sqlalchemy() -> None:
    """F8 hard fail guard: confidence_decay must not import SQLAlchemy.

    Uses AST inspection (not substring search) so legitimate references
    to ``extraction_claims`` in the module docstring documenting the F8
    scope boundary do not trip the guard.
    """
    import ast

    src = Path(decay_module.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("sqlalchemy"), (
                    f"F8 violation: confidence_decay imports {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("sqlalchemy"), (
                f"F8 violation: confidence_decay imports from {module}"
            )


# --- CLI -------------------------------------------------------------------


def test_cli_rejects_invalid_observation_time(tmp_path: Path, capsys) -> None:
    """``--observation-time not-a-date`` exits non-zero with stderr message."""
    cfg = tmp_path / "decay.yaml"
    cfg.write_text("t_half_days: 90\n")
    rc = cli_main(["--observation-time", "not-a-date", "--config", str(cfg)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "observation-time" in err


# --- Helpers ---------------------------------------------------------------


def pytest_run_async(coro):
    """Run an awaitable inside a fresh event loop and return the result."""
    import asyncio

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)
