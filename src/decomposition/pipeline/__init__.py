"""Decomposition pipeline subpackage (Chunk 40).

Public surface:

* :func:`run_decomposition` — async orchestrator wiring Layers 1→2→3→4.
* :mod:`cli` — Typer CLI (``python -m src.decomposition.pipeline``).
"""

from src.decomposition.pipeline.orchestrator import run_decomposition

__all__ = ["run_decomposition"]
