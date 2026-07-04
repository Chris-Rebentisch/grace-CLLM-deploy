"""F-46 regression: a per-deployment dataset with a relaxed manifest can be
evaluated without satisfying the strict GOLD corpus contract (>=50 cases /
fixed modules / 30-40-20-10 distribution). A directory with no manifest keeps
the strict contract."""

from __future__ import annotations

import json

import pytest

from src.eval.golden_loader import GoldenDatasetValidationError, load_golden_dataset


_n = [0]


def _case(module, complexity="simple"):
    _n[0] += 1
    return {
        "case_id": f"c{_n[0]}",
        "query_text": f"q-{module}-{complexity}-{_n[0]}",
        "expected_output": "a",
        "expected_retrieval_path": "semantic",
        "ontology_module": module,
        "query_complexity": complexity,
    }


def test_relaxed_manifest_allows_small_deployment_corpus(tmp_path):
    (tmp_path / "my_domain.json").write_text(
        json.dumps([_case("my_domain") for _ in range(5)])
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {"contract": "relaxed", "min_total_cases": 3, "files": ["my_domain.json"]}
        )
    )
    cases = load_golden_dataset(tmp_path)
    assert len(cases) == 5  # loaded without GOLD-contract rejection


def test_relaxed_manifest_enforces_min_total(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps([_case("my_domain")]))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"contract": "relaxed", "min_total_cases": 10, "files": ["d.json"]})
    )
    with pytest.raises(GoldenDatasetValidationError):
        load_golden_dataset(tmp_path)


def test_no_manifest_keeps_strict_contract(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps([_case("my_domain")]))
    with pytest.raises(GoldenDatasetValidationError):
        load_golden_dataset(tmp_path)  # < 50 cases → strict rejection
