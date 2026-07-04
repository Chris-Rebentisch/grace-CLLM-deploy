"""Golden dataset loader (Chunk 34, D258).

Hand-authored only. No LLM-bootstrapped or RAGAS-synthetic content.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# D258: 5 overlap modules + 1 additional = >=50 total.
_OVERLAP_MODULES: frozenset[str] = frozenset(
    {"corporate_structure", "insurance", "real_estate", "legal", "vendors"}
)
_ADDITIONAL_CANDIDATES: frozenset[str] = frozenset(
    {"operations", "tax", "hr", "culinary", "education", "other"}
)

# 30/40/20/10 distribution per Testing Standards §9.1, ±5% tolerance.
_TARGET_DISTRIBUTION: dict[str, float] = {
    "simple": 0.30,
    "multi_hop": 0.40,
    "aggregate": 0.20,
    "semantic": 0.10,
}
_DISTRIBUTION_TOLERANCE = 0.05

_OVERLAP_MIN, _OVERLAP_MAX = 8, 12
_ADDITIONAL_MIN, _ADDITIONAL_MAX = 4, 8
_MIN_TOTAL_CASES = 50


class GoldenCase(BaseModel):
    """A single hand-authored evaluation case (D258).

    `expected_output` is hand-authored prose ground truth. No LLM-generated
    or RAGAS-synthetic content (D258 forbids).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(description="Stable {module}-{N:03d} identifier.")
    query_text: str = Field(description="Natural-language query.")
    expected_output: str = Field(description="Hand-authored prose ground truth.")
    expected_retrieval_path: Literal["graph", "semantic", "keyword", "fused"] = Field(
        description="Strategy expected to dominate."
    )
    query_complexity: Literal["simple", "multi_hop", "aggregate", "semantic"] = Field(
        description="Complexity bucket per Testing Standards §9.1."
    )
    ontology_module: str = Field(description="Active production module name.")
    source_documents: list[str] = Field(
        default_factory=list,
        description="Relative paths under data/discovery-sample/.",
    )
    notes: str | None = Field(default=None, description="Optional authoring note.")


class GoldenDatasetValidationError(AssertionError):
    """Raised when the golden dataset fails distribution / coverage assertions."""


def _load_manifest(directory: Path) -> dict | None:
    """Load an optional per-deployment dataset manifest (F-46).

    Returns the parsed manifest dict, or None when no ``manifest.json`` exists
    (strict GOLD directory contract). Raises on malformed manifest.
    """
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text())
    if not isinstance(data, dict) or "files" not in data:
        raise GoldenDatasetValidationError(
            "manifest.json must be an object with a 'files' list."
        )
    if not isinstance(data["files"], list) or not data["files"]:
        raise GoldenDatasetValidationError("manifest 'files' must be a non-empty list.")
    return data


def load_golden_dataset(directory: Path) -> list[GoldenCase]:
    """Load and validate the golden dataset under `directory`.

    Asserts:
      (a) >=50 total cases,
      (b) 8–12 per overlap module / 4–8 for additional,
      (c) 30/40/20/10 complexity distribution within ±5% tolerance.

    Deduplicates by `(query_text, ontology_module)`.

    Raises:
        GoldenDatasetValidationError: on any assertion failure.
    """
    directory = Path(directory)
    if not directory.exists():
        raise GoldenDatasetValidationError(f"Golden dataset directory missing: {directory}")

    # F-46 (validation run, 2026-07-01): the loader validated the WHOLE
    # directory against the GOLD corpus contract (>=50 cases, fixed overlap-module
    # list, 30/40/20/10 distribution), so a new deployment could not evaluate its
    # own corpus and simply ADDING a per-deployment file (e.g. my_domain.json)
    # broke run-suite entirely. An optional per-deployment `manifest.json` now
    # selects the dataset files and its contract; when absent, the strict GOLD
    # directory contract is unchanged (backward compatible).
    manifest = _load_manifest(directory)
    if manifest is not None:
        json_paths = [directory / f for f in manifest.get("files", [])]
    else:
        json_paths = sorted(directory.glob("*.json"))

    cases: list[GoldenCase] = []
    for json_path in json_paths:
        if json_path.name == "manifest.json":
            continue
        if not json_path.exists():
            raise GoldenDatasetValidationError(
                f"Manifest references missing file: {json_path}"
            )
        raw = json.loads(json_path.read_text())
        if not isinstance(raw, list):
            raise GoldenDatasetValidationError(
                f"Golden dataset file must be a JSON array: {json_path}"
            )
        for entry in raw:
            cases.append(GoldenCase.model_validate(entry))

    # Deduplicate by (query_text, ontology_module).
    seen: set[tuple[str, str]] = set()
    deduped: list[GoldenCase] = []
    for c in cases:
        key = (c.query_text, c.ontology_module)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    total = len(deduped)

    # F-46: a per-deployment manifest with contract="relaxed" validates each
    # file (well-formed JSON array of GoldenCase) and a configurable minimum
    # count, but skips the GOLD-specific module + distribution contract so a
    # deployment can evaluate its own corpus. Strict mode (no manifest, or
    # contract="strict") keeps the full Testing-Standards §9.1 assertions.
    if manifest is not None and manifest.get("contract", "relaxed") == "relaxed":
        min_total = int(manifest.get("min_total_cases", 1))
        if total < min_total:
            raise GoldenDatasetValidationError(
                f"Dataset has {total} cases (< manifest min_total_cases {min_total})."
            )
        return deduped

    if total < _MIN_TOTAL_CASES:
        raise GoldenDatasetValidationError(
            f"Golden dataset has {total} cases (<{_MIN_TOTAL_CASES} required)."
        )

    # Per-module coverage.
    by_module: dict[str, int] = {}
    for c in deduped:
        by_module[c.ontology_module] = by_module.get(c.ontology_module, 0) + 1

    overlap_present = _OVERLAP_MODULES & set(by_module.keys())
    if overlap_present != _OVERLAP_MODULES:
        missing = _OVERLAP_MODULES - overlap_present
        raise GoldenDatasetValidationError(
            f"Overlap modules missing from golden dataset: {sorted(missing)}"
        )
    for module in _OVERLAP_MODULES:
        n = by_module[module]
        if not (_OVERLAP_MIN <= n <= _OVERLAP_MAX):
            raise GoldenDatasetValidationError(
                f"Overlap module {module!r} has {n} cases (need "
                f"{_OVERLAP_MIN}-{_OVERLAP_MAX})."
            )

    additional_modules = set(by_module.keys()) - _OVERLAP_MODULES
    if not additional_modules:
        raise GoldenDatasetValidationError(
            "No additional module present (need 1 to clear >=50 total)."
        )
    for module in additional_modules:
        if module not in _ADDITIONAL_CANDIDATES:
            raise GoldenDatasetValidationError(
                f"Additional module {module!r} not in candidate list "
                f"{sorted(_ADDITIONAL_CANDIDATES)}."
            )
        n = by_module[module]
        if not (_ADDITIONAL_MIN <= n <= _ADDITIONAL_MAX):
            raise GoldenDatasetValidationError(
                f"Additional module {module!r} has {n} cases (need "
                f"{_ADDITIONAL_MIN}-{_ADDITIONAL_MAX})."
            )

    # Complexity distribution within tolerance.
    by_complexity: dict[str, int] = {k: 0 for k in _TARGET_DISTRIBUTION}
    for c in deduped:
        by_complexity[c.query_complexity] = by_complexity.get(c.query_complexity, 0) + 1
    for bucket, target in _TARGET_DISTRIBUTION.items():
        actual = by_complexity[bucket] / total
        delta = abs(actual - target)
        if delta > _DISTRIBUTION_TOLERANCE:
            raise GoldenDatasetValidationError(
                f"Complexity bucket {bucket!r} at {actual:.3f} (target "
                f"{target:.2f} ±{_DISTRIBUTION_TOLERANCE:.2f})."
            )

    return deduped


def default_golden_dir() -> Path:
    """Default golden dataset directory under the package."""
    return Path(__file__).resolve().parent / "golden_dataset"
