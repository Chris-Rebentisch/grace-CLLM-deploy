"""CP7 sweep: verify GrACE-Doc-Map.md indexes new docs from chunks 73–75b."""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Parent-repo doc contract: GrACE-Doc-Map.md is intentionally not shipped in
# the Claude-as-the-LLM deploy repo, so this contract only applies when present.
pytestmark = pytest.mark.skipif(
    not (ROOT / "docs" / "GrACE-Doc-Map.md").exists(),
    reason="GrACE-Doc-Map.md not shipped in the CLLM deploy repo (parent-repo doc contract)",
)

# New docs created in chunks 73–75b that must appear in Doc-Map.
EXPECTED_DOCS = [
    "pytest-db-safety.md",
    "cumulative-counter-correction",
    "backup-file-retention-policy.md",
    "reviewer-provider-recovery.md",
    "operator-first-chunk-walkthrough.md",
]


def test_doc_map_indexes_new_docs():
    doc_map = (ROOT / "docs" / "GrACE-Doc-Map.md").read_text()
    missing = [d for d in EXPECTED_DOCS if d not in doc_map]
    assert not missing, f"Docs missing from GrACE-Doc-Map.md: {missing}"
