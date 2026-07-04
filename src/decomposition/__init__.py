"""Organizational Decomposition pipeline (Chunk 40, Layers 1–4).

CLI-only invocation surface (D315, D246 mirror). Imports of FastAPI
or APScheduler are forbidden inside this package; see
``tests/decomposition/test_pipeline_invocation_surface.py``.
"""

from src.decomposition.models import (
    DecompositionRunStatus,
    EmbeddingProvenance,
    GmmParams,
    HdbscanParams,
    Hypothesis,
    Layer1FileEntry,
    Layer1FolderSummary,
    Layer1Summary,
    Layer2Decision,
    Layer3Decision,
    Layer4HypothesisSet,
    LeidenSeedRun,
    NullHypothesis,
    ProperNounMention,
    ProperNounMentions,
    ProposedSegment,
    SegmentedHypothesis,
    SynthesisMetadata,
    UmapParams,
)

__all__ = [
    "DecompositionRunStatus",
    "EmbeddingProvenance",
    "GmmParams",
    "HdbscanParams",
    "Hypothesis",
    "Layer1FileEntry",
    "Layer1FolderSummary",
    "Layer1Summary",
    "Layer2Decision",
    "Layer3Decision",
    "Layer4HypothesisSet",
    "LeidenSeedRun",
    "NullHypothesis",
    "ProperNounMention",
    "ProperNounMentions",
    "ProposedSegment",
    "SegmentedHypothesis",
    "SynthesisMetadata",
    "UmapParams",
]
