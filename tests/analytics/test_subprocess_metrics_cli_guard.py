"""F-0049/ISS-0040 guard: every D246 CLI entry calls init_subprocess_metrics.

Validation run F-0049 (+F-0023): 8 of 13 golden metric families never
reached /metrics because most D246 out-of-process CLIs never installed the
F-15 multiproc write-through — only extraction_bridge, image_pipeline, and
voice_tone did. This AST guard (precedent: the D246 route-isolation guards,
e.g. tests/extraction/test_extraction_bridge_route_isolation.py) asserts a
``init_subprocess_metrics(...)`` call exists somewhere in each required CLI
module so future CLIs cannot silently regress into Prometheus-invisible.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every D246 out-of-process CLI entry module that records (or will record)
# OTel metrics. Extending this list is the ONLY sanctioned way to add a new
# D246 CLI pipeline — a new CLI that emits metrics must call
# init_subprocess_metrics() at its entry point and be listed here.
REQUIRED_CLI_MODULES = [
    "src/analytics/signal_pipeline/cli.py",
    "src/analytics/correlation_engine/cli.py",
    "src/ingestion/communications/corroboration_scorer.py",
    "src/ingestion/communications/retriage.py",
    "src/ingestion/communications/sensitivity_tagger.py",
    "src/ingestion/communications/bootstrap_pipe.py",
    "src/ingestion/communications/thread_reconstructor.py",
    "src/ingestion/communications/voice_tone/__main__.py",
    "src/ingestion/__main__.py",
    "src/ontology/calibration_updater.py",
    "src/ontology/agent_daemon.py",
    "src/ontology/change_executor.py",
    "src/extraction/confidence_decay.py",
    "src/extraction/embedding_backfill.py",
    "src/extraction/backfill_document_chunks.py",
    "src/extraction/eval_checkpoint.py",
    "src/extraction/extraction_bridge.py",
    "src/extraction/image_pipeline.py",
    "src/permissions/hypothesis_generator.py",
    "src/permissions/drift_detector.py",
    # F-0049 / ISS-0040 deferral closure: the real argv entry for hypothesis
    # generation (python -m src.permissions.cli) — init at main(), not just
    # the generate() interim workaround.
    "src/permissions/cli.py",
    "src/decomposition/pipeline/cli.py",
]


def _calls_init_subprocess_metrics(tree: ast.AST) -> bool:
    """True when the module contains a call to ``init_subprocess_metrics``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "init_subprocess_metrics":
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "init_subprocess_metrics"
        ):
            return True
    return False


@pytest.mark.parametrize("rel_path", REQUIRED_CLI_MODULES)
def test_d246_cli_calls_init_subprocess_metrics(rel_path: str) -> None:
    path = REPO_ROOT / rel_path
    assert path.is_file(), f"required D246 CLI module missing on disk: {rel_path}"
    tree = ast.parse(path.read_text(), filename=rel_path)
    assert _calls_init_subprocess_metrics(tree), (
        f"F-0049/ISS-0040 violation: {rel_path} never calls "
        "init_subprocess_metrics() — its OTel metrics die with the subprocess "
        "and never reach /metrics. Add the one-line init at the CLI entry "
        "(pattern: src/extraction/extraction_bridge.py main())."
    )
