"""CP7 sweep: verify GrACE-Decisions.md has rows for D472+ and pointer is advanced."""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Parent-repo doc contract: GrACE-Decisions.md is intentionally not shipped in
# the Claude-as-the-LLM deploy repo, so this contract only applies when present.
pytestmark = pytest.mark.skipif(
    not (ROOT / "docs" / "GrACE-Decisions.md").exists(),
    reason="GrACE-Decisions.md not shipped in the CLLM deploy repo (parent-repo doc contract)",
)

# D472–D487 (chunks 73–75a) + D489–D494 (chunk 75b).
# D488 intentionally unused (collapsed to apply-only per §1).
EXPECTED_D_NUMBERS = list(range(472, 488)) + list(range(489, 495))


def test_decisions_md_d_number_coverage():
    text = (ROOT / "docs" / "GrACE-Decisions.md").read_text()

    # Check §3 rows — each expected D-number should appear as "| D<N> |"
    missing = []
    for d in EXPECTED_D_NUMBERS:
        pattern = rf"\|\s*D{d}\s*\|"
        if not re.search(pattern, text):
            missing.append(f"D{d}")
    assert not missing, f"D-number rows missing from §3: {missing}"

    # Check pointer is at least D495
    pointer_match = re.search(r"Next free D-number:\*\*\s*D(\d+)", text)
    assert pointer_match, "Could not find 'Next free D-number' in §1"
    pointer_val = int(pointer_match.group(1))
    assert pointer_val >= 495, f"Pointer is D{pointer_val}, expected >= D495"
