"""Tests for the hand-authored golden dataset loader (Chunk 34, D258)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from src.eval.golden_loader import (
    GoldenCase,
    GoldenDatasetValidationError,
    default_golden_dir,
    load_golden_dataset,
)


# Modules mirror the loader's private constants by intent — kept in sync via tests.
_OVERLAP_MODULES = {
    "corporate_structure",
    "insurance",
    "real_estate",
    "legal",
    "vendors",
}


def _build_balanced(
    *,
    n_per_overlap: int = 10,
    n_additional: int = 4,
    additional_module: str = "operations",
    case_factory,
) -> list[dict]:
    """Build a synthetic dataset that satisfies all loader assertions."""
    cases: list[dict] = []
    counter = 0

    def _make(module: str, complexity: str) -> dict:
        nonlocal counter
        counter += 1
        return case_factory(
            case_id=f"{module}-{counter:03d}",
            module=module,
            complexity=complexity,
            query_text=f"Synthetic query {counter}",
        )

    # Per-module complexity recipe: 10 = 3 simple / 4 multi_hop / 2 aggregate / 1 semantic.
    overlap_recipe = (["simple"] * 3) + (["multi_hop"] * 4) + (["aggregate"] * 2) + (["semantic"] * 1)
    assert len(overlap_recipe) == n_per_overlap

    for module in sorted(_OVERLAP_MODULES):
        for complexity in overlap_recipe:
            cases.append(_make(module, complexity))

    # Additional module: 4 = 1 simple / 2 multi_hop / 1 aggregate / 0 semantic.
    additional_recipe = ["simple", "multi_hop", "multi_hop", "aggregate"]
    assert len(additional_recipe) == n_additional
    for complexity in additional_recipe:
        cases.append(_make(additional_module, complexity))

    return cases


def test_packaged_golden_dataset_loads_and_validates() -> None:
    """The hand-authored dataset shipped under src/eval/golden_dataset
    must load cleanly and satisfy every distribution / coverage assertion."""
    cases = load_golden_dataset(default_golden_dir())
    assert len(cases) >= 50
    by_complexity = Counter(c.query_complexity for c in cases)
    total = len(cases)
    targets = {
        "simple": 0.30,
        "multi_hop": 0.40,
        "aggregate": 0.20,
        "semantic": 0.10,
    }
    for bucket, target in targets.items():
        actual = by_complexity[bucket] / total
        assert abs(actual - target) <= 0.05, (
            f"complexity bucket {bucket!r} at {actual:.3f} (target {target:.2f})"
        )
    by_module = Counter(c.ontology_module for c in cases)
    for module in _OVERLAP_MODULES:
        assert 8 <= by_module[module] <= 12, f"{module}: {by_module[module]}"
    assert all(isinstance(c, GoldenCase) for c in cases)


def test_schema_rejects_extra_fields(make_dataset_dir, case_factory) -> None:
    """Pydantic ConfigDict(extra='forbid') must reject unknown keys (D258)."""
    bad = case_factory(
        case_id="x-001", module="corporate_structure", complexity="simple"
    )
    bad["unexpected_field"] = "not allowed"
    directory = make_dataset_dir([bad])
    with pytest.raises(Exception):  # pydantic.ValidationError subclass
        load_golden_dataset(directory)


def test_schema_rejects_invalid_enum(make_dataset_dir, case_factory) -> None:
    """Literal-typed fields must reject out-of-domain values."""
    bad = case_factory(
        case_id="x-001", module="corporate_structure", complexity="simple"
    )
    bad["expected_retrieval_path"] = "vector_only"  # not in {graph, semantic, keyword, fused}
    directory = make_dataset_dir([bad])
    with pytest.raises(Exception):
        load_golden_dataset(directory)


def test_dedup_by_query_text_and_module(tmp_path, case_factory) -> None:
    """Duplicate (query_text, ontology_module) pairs are removed; loader
    raises validation error when dedup pushes total below the floor."""
    dup = case_factory(
        case_id="dup-001",
        module="corporate_structure",
        complexity="simple",
        query_text="exact same text",
    )
    cases = [dup, dict(dup, case_id="dup-002")]  # same query_text + module
    (tmp_path / "module.json").write_text(json.dumps(cases))
    with pytest.raises(GoldenDatasetValidationError) as exc_info:
        load_golden_dataset(tmp_path)
    assert "<50" in str(exc_info.value) or "required" in str(exc_info.value)


def test_total_floor_enforced(make_dataset_dir, case_factory) -> None:
    """Loader rejects datasets with fewer than 50 cases."""
    cases = [
        case_factory(
            case_id=f"corporate_structure-{i:03d}",
            module="corporate_structure",
            complexity="simple",
        )
        for i in range(10)
    ]
    directory = make_dataset_dir(cases)
    with pytest.raises(GoldenDatasetValidationError, match="<50"):
        load_golden_dataset(directory)


def test_overlap_module_missing_rejected(
    tmp_path: Path, case_factory
) -> None:
    """All five overlap modules must be present, even with >=50 total cases."""
    # 4 overlap modules x 12 cases + 1 additional x 8 cases = 56 total,
    # but the vendors overlap module is intentionally absent.
    cases: list[dict] = []
    counter = 0
    overlap_recipe = (
        (["simple"] * 4)
        + (["multi_hop"] * 5)
        + (["aggregate"] * 2)
        + (["semantic"] * 1)
    )
    assert len(overlap_recipe) == 12
    for module in ("corporate_structure", "insurance", "real_estate", "legal"):
        for complexity in overlap_recipe:
            counter += 1
            cases.append(
                case_factory(
                    case_id=f"{module}-{counter:03d}",
                    module=module,
                    complexity=complexity,
                    query_text=f"Synthetic {counter}",
                )
            )
    additional_recipe = (
        (["simple"] * 2) + (["multi_hop"] * 3) + (["aggregate"] * 2) + (["semantic"] * 1)
    )
    assert len(additional_recipe) == 8
    for complexity in additional_recipe:
        counter += 1
        cases.append(
            case_factory(
                case_id=f"operations-{counter:03d}",
                module="operations",
                complexity=complexity,
                query_text=f"Synthetic {counter}",
            )
        )
    (tmp_path / "module.json").write_text(json.dumps(cases))
    with pytest.raises(GoldenDatasetValidationError, match="Overlap modules missing"):
        load_golden_dataset(tmp_path)


def test_complexity_distribution_outside_tolerance_rejected(
    make_dataset_dir, case_factory
) -> None:
    """Skewed complexity must be rejected at >5% absolute delta."""
    raw = _build_balanced(case_factory=case_factory)
    # Flip every multi_hop into simple — pushes simple way above 0.30 and
    # multi_hop way below 0.40.
    for entry in raw:
        if entry["query_complexity"] == "multi_hop":
            entry["query_complexity"] = "simple"
    directory = make_dataset_dir(raw)
    with pytest.raises(GoldenDatasetValidationError, match="Complexity bucket"):
        load_golden_dataset(directory)


def test_balanced_synthetic_dataset_passes(make_dataset_dir, case_factory) -> None:
    """The balanced synthetic builder used by other tests is itself valid."""
    raw = _build_balanced(case_factory=case_factory)
    directory = make_dataset_dir(raw)
    cases = load_golden_dataset(directory)
    assert len(cases) == 54


def test_directory_must_exist(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    with pytest.raises(GoldenDatasetValidationError, match="missing"):
        load_golden_dataset(missing)
