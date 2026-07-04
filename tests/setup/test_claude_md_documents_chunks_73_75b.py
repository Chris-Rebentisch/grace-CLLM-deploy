"""CP7 sweep: verify CLAUDE.md + AGENTS.md document env vars from chunks 73–75b."""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Parent-repo doc contract: AGENTS.md is intentionally not shipped in the
# Claude-as-the-LLM deploy repo, so this contract only applies when present.
pytestmark = pytest.mark.skipif(
    not (ROOT / "AGENTS.md").exists(),
    reason="AGENTS.md not shipped in the CLLM deploy repo (parent-repo doc contract)",
)

ENV_VARS = [
    "GRACE_TEST_DB",
    "GRACE_PYTEST_DATABASE_URL",
    "GRACE_PYTEST_MODE",
    "GRACE_SKIP_CUMULATIVE_COUNT_CHECK",
    "GRACE_SKIP_AUDIT_PWD_GUARD",
    "GRACE_SKIP_PRE_CODE_INDEPENDENT_REVIEW",
    "GRACE_GOLD_DUMP_FORCE",
]


def test_claude_md_and_agents_md_contain_env_vars():
    claude_md = (ROOT / "CLAUDE.md").read_text()
    agents_md = (ROOT / "AGENTS.md").read_text()
    combined = claude_md + agents_md
    missing = [v for v in ENV_VARS if v not in combined]
    assert not missing, f"Env vars missing from CLAUDE.md + AGENTS.md: {missing}"
