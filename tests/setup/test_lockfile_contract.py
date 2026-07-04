"""D495–D497: lockfile contract enforcement tests — 5 tests."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ci_uses_uv_sync():
    """CI bootstrap-postgres job must use uv sync and astral-sh/setup-uv,
    not pip install or setup-python."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    content = ci_path.read_text()

    assert "uv sync" in content, "CI must contain 'uv sync'"
    assert "astral-sh/setup-uv" in content, "CI must use astral-sh/setup-uv"

    # Check bootstrap-postgres section specifically
    lines = content.splitlines()
    in_bootstrap = False
    for line in lines:
        if "bootstrap-postgres" in line:
            in_bootstrap = True
        elif in_bootstrap and line.strip().startswith("grafana-preflight"):
            break
        elif in_bootstrap and "pip install" in line:
            raise AssertionError(
                f"bootstrap-postgres still contains pip install: {line.strip()}"
            )


def test_first_boot_uses_uv():
    """first-boot.md must contain uv sync instruction."""
    fb_path = REPO_ROOT / "docs" / "runbooks" / "first-boot.md"
    content = fb_path.read_text()
    assert "uv sync" in content, "first-boot.md must mention 'uv sync'"


def test_claude_md_tech_debt_flag_removed():
    """CLAUDE.md must NOT contain the 'lockfile pending' tech-debt flag."""
    claude_path = REPO_ROOT / "CLAUDE.md"
    content = claude_path.read_text()
    assert "lockfile pending" not in content, (
        "CLAUDE.md still contains 'lockfile pending' tech-debt flag"
    )


def test_migration_runbook_documents_rollback():
    """Migration runbook must document rollback procedure."""
    runbook_path = REPO_ROOT / "docs" / "runbooks" / "dependency-lockfile-migration.md"
    assert runbook_path.exists(), "Migration runbook does not exist"
    content = runbook_path.read_text()
    assert "rollback" in content.lower(), "Runbook must document rollback"
    assert "UV_OFFLINE" in content, "Runbook must document UV_OFFLINE for airgap"


def test_decisions_contain_d495_d496_d497():
    """GrACE-Decisions.md must contain D495, D496, D497."""
    decisions_path = REPO_ROOT / "docs" / "GrACE-Decisions.md"
    if not decisions_path.exists():
        # Parent-repo doc contract: GrACE-Decisions.md is intentionally not
        # shipped in the Claude-as-the-LLM deploy repo.
        import pytest

        pytest.skip("GrACE-Decisions.md not shipped in the CLLM deploy repo")
    content = decisions_path.read_text()
    for d_num in ["D495", "D496", "D497"]:
        assert d_num in content, f"GrACE-Decisions.md missing {d_num}"
