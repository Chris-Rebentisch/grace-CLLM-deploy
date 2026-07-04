"""Regression guard for EC-7 airgap (Defect 6).

`sentence-transformers` / `huggingface_hub` phone home to verify cache
revisions unless HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE are set at
load time. `src/api/main.py` sets both at the very top of the module,
before any `src/` imports. If someone later adds an earlier import that
pulls in sentence_transformers, that load would happen before these
guards are in place and the airgap would silently break.

This test imports main and asserts the env vars are set.
"""

from __future__ import annotations

import os


def test_airgap_env_set_after_main_import():
    # importlib avoids stale module state if another test already imported main.
    import importlib

    import src.api.main as main_module

    importlib.reload(main_module)

    assert os.environ.get("HF_HUB_OFFLINE") == "1", (
        "HF_HUB_OFFLINE must be '1' after importing src.api.main (EC-7 / Defect 6)"
    )
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1", (
        "TRANSFORMERS_OFFLINE must be '1' after importing src.api.main "
        "(EC-7 / Defect 6)"
    )
