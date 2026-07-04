"""Six fixed correlation pattern detectors (Chunk 33, D250; D535).

Each module exports exactly one ``CorrelationDetector`` subclass that
emits at most one ``DiagnosticRecord`` per ``(run_id, ontology_module)``.
"""

from src.analytics.correlation_engine.patterns.cq_regression_pre_extraction import (
    CQRegressionPreExtractionDetector,
)
from src.analytics.correlation_engine.patterns.extraction_quality_problem import (
    ExtractionQualityProblemDetector,
)
from src.analytics.correlation_engine.patterns.graph_or_index_problem import (
    GraphOrIndexProblemDetector,
)
from src.analytics.correlation_engine.patterns.ontology_constraint_conflict import (
    OntologyConstraintConflictDetector,
)
from src.analytics.correlation_engine.patterns.relationship_gap_propagation import (
    RelationshipGapPropagationDetector,
)
from src.analytics.correlation_engine.patterns.schema_drift_per_module import (
    SchemaDriftPerModuleDetector,
)

__all__ = [
    "CQRegressionPreExtractionDetector",
    "ExtractionQualityProblemDetector",
    "GraphOrIndexProblemDetector",
    "OntologyConstraintConflictDetector",
    "RelationshipGapPropagationDetector",
    "SchemaDriftPerModuleDetector",
]


# Default ordering for the orchestrator. The catalog was locked at five
# (D250); D535 amends D250 to add the sixth pattern
# (``ontology_constraint_conflict``). Any further pattern requires a new
# D-series amendment.
DEFAULT_DETECTOR_CLASSES = [
    ExtractionQualityProblemDetector,
    GraphOrIndexProblemDetector,
    SchemaDriftPerModuleDetector,
    CQRegressionPreExtractionDetector,
    RelationshipGapPropagationDetector,
    OntologyConstraintConflictDetector,
]
