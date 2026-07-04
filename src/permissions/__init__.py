"""Guided Permissions engine — Chunk 42 (D331–D339).

Pre-Chunk-42, only ``resolve_visibility`` was exported (D296 stub).
Chunk 42 adds the full Permission Matrix engine. The on-row visibility
enum literals on ``change_directives`` persist unchanged across the
transition (D285 forward-guarantee).

Public API (selective re-exports):

* ``PermissionMatrix`` — operator-ratified policy artifact.
* ``EvidenceBundle`` — six-source evidence aggregation output.
* ``resolve_visibility`` — change-directive visibility resolver
  (interim until Chunk 44 activates the full enforcer path).
"""

from src.permissions.change_directive_visibility import resolve_visibility
from src.permissions.drift_detector import (
    DriftConfig,
    DriftRunReport,
    PersonFeature,
    classify as classify_drift,
    run_once as run_drift_once,
)
from src.permissions.enforcer import (
    Enforcer,
    Resource,
    get_enforcer,
    rebuild_enforcer,
)
from src.permissions.models import (
    EvidenceBundle,
    PermissionMatrix,
)
from src.permissions.principal_context import (
    PrincipalContext,
    User,
    UserActingViaAgent,
)

__all__ = [
    "DriftConfig",
    "DriftRunReport",
    "Enforcer",
    "EvidenceBundle",
    "PermissionMatrix",
    "PersonFeature",
    "PrincipalContext",
    "Resource",
    "User",
    "UserActingViaAgent",
    "classify_drift",
    "get_enforcer",
    "rebuild_enforcer",
    "resolve_visibility",
    "run_drift_once",
]
