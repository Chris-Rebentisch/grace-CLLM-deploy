"""D495/D496: pyproject.toml completeness tests — 5 unit tests."""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"


def _load_pyproject() -> dict:
    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)


def test_pyproject_parses_valid_toml():
    """pyproject.toml must parse without error."""
    data = _load_pyproject()
    assert "project" in data


def test_dev_section_exists_and_nonempty():
    """[project.optional-dependencies.dev] must exist and be non-empty."""
    data = _load_pyproject()
    dev = data["project"]["optional-dependencies"]["dev"]
    assert len(dev) > 0, "dev dependency group is empty"


def test_requires_python_matches():
    """requires-python must be >=3.14."""
    data = _load_pyproject()
    assert data["project"]["requires-python"] == ">=3.14"


def test_uv_lock_parses_valid_toml():
    """uv.lock must exist and parse as valid TOML."""
    assert UV_LOCK.exists(), "uv.lock does not exist"
    with open(UV_LOCK, "rb") as f:
        data = tomllib.load(f)
    assert isinstance(data, dict)


def test_dependency_inventory_coverage():
    """Every CLAUDE.md Tech Stack package directly imported in src/ must appear
    in pyproject.toml dependencies or an extras group."""
    data = _load_pyproject()
    # Collect all declared dependency names (lowercased, normalized)
    all_deps: set[str] = set()
    for dep in data["project"].get("dependencies", []):
        name = dep.split("==")[0].split(">=")[0].split("<")[0].split(";")[0].strip()
        all_deps.add(name.lower().replace("-", "_").replace(".", "_"))
    for group in data["project"].get("optional-dependencies", {}).values():
        for dep in group:
            name = dep.split("==")[0].split(">=")[0].split("<")[0].split(";")[0].strip()
            all_deps.add(name.lower().replace("-", "_").replace(".", "_"))

    # Key packages from CLAUDE.md Tech Stack that src/ imports
    key_packages = [
        "fastapi", "pydantic", "sqlalchemy", "alembic", "httpx",
        "structlog", "rdflib", "deepdiff", "jsonpatch", "numpy",
        "scikit_learn", "hdbscan", "bm25s", "pymannkendall", "pysbd",
        "mcp", "opentelemetry_api", "prometheus_client",
    ]
    missing = [p for p in key_packages if p not in all_deps]
    assert not missing, f"Missing from pyproject.toml: {missing}"
