"""GrACE evaluation package (Chunk 34).

Hand-authored golden dataset, DeepEval regression suite (D257), Bench4KE
CSV export (D261), CLI entry point (D246-inherited, CLI-only).

Out-of-process per D246 — never started from FastAPI lifespan or any
in-process scheduler. Operators schedule via host cron / launchd.
"""

from __future__ import annotations

from src.eval.golden_loader import GoldenCase, load_golden_dataset

__all__ = [
    "GoldenCase",
    "load_golden_dataset",
    "EvalRunner",
    "run_suite",
]


def __getattr__(name: str):  # pragma: no cover - lazy import shim
    # Lazy imports for heavier modules to keep package import cheap.
    if name == "EvalRunner":
        from src.eval.deepeval_runner import EvalRunner

        return EvalRunner
    if name == "run_suite":
        from src.eval.deepeval_runner import run_suite

        return run_suite
    raise AttributeError(f"module 'src.eval' has no attribute {name!r}")
