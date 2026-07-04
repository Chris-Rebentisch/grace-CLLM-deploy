#!/usr/bin/env bash
# Contract enforcement:
#   D204 — regeneration target (Chunk 23 / 27)
#   D213 — retrieval + graph-read-models targets (Chunk 28)
#   D283 — recon-api target (Chunk 36) — diffs Python Pydantic models in
#          src/api/recon_models.py against TypeScript mirror in
#          frontend/lib/api/recon-types.ts.
# Each target independently diffs Python Pydantic field names against
# the TypeScript mirror. All must pass.

set -euo pipefail

python3 - <<'PY'
import ast
import re
import sys
from pathlib import Path

repo = Path(".").resolve()
frontend = repo / "frontend/lib/api/types.ts"


def extract_backend_fields(path: Path, targets: set[str]) -> dict[str, list[str]]:
    src = path.read_text(encoding="utf-8")
    mod = ast.parse(src)
    backend_fields: dict[str, list[str]] = {}
    for node in mod.body:
        if isinstance(node, ast.ClassDef) and node.name in targets:
            fields = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.append(stmt.target.id)
            backend_fields[node.name] = sorted(fields)
    return backend_fields


def extract_frontend_fields(
    targets: set[str], frontend_path: Path = frontend
) -> dict[str, list[str] | None]:
    ts = frontend_path.read_text(encoding="utf-8")
    out: dict[str, list[str] | None] = {}
    for name in targets:
        m = re.search(rf"export\s+type\s+{name}\s*=\s*\{{(.*?)\n\}}", ts, re.S)
        if not m:
            m = re.search(rf"export\s+interface\s+{name}\s*\{{(.*?)\n\}}", ts, re.S)
        if not m:
            out[name] = None
            continue
        body = m.group(1)
        fields: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            key = re.split(r"[:?]", line, maxsplit=1)[0].strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                fields.append(key)
        out[name] = sorted(set(fields))
    return out


def run_target(
    label: str,
    backend_path: Path,
    targets: set[str],
    frontend_path: Path = frontend,
) -> list[str]:
    errors: list[str] = []
    backend_fields = extract_backend_fields(backend_path, targets)
    frontend_fields = extract_frontend_fields(targets, frontend_path)
    for name in sorted(targets):
        if name not in backend_fields:
            errors.append(f"[{label}] missing backend model: {name}")
            continue
        ff = frontend_fields.get(name)
        if ff is None:
            errors.append(f"[{label}] missing frontend type/interface: {name}")
            continue
        bf = backend_fields[name]
        if bf != ff:
            errors.append(
                f"[{label}] {name} mismatch\n  backend: {bf}\n  front:   {ff}"
            )
    return errors


# D204 — regeneration
D204_ERRORS = run_target(
    "D204 regeneration",
    repo / "src/regeneration/regeneration_models.py",
    {
        "RegenerationQuery",
        "RegenOverrides",
        "RegenerationResponse",
        "ClaimSpan",
        "ResponseMetadata",
        "RegenerationError",
        "RegenerationConfigResponse",
    },
)

# D213 — retrieval
D213_RETRIEVAL_ERRORS = run_target(
    "D213 retrieval",
    repo / "src/retrieval/retrieval_models.py",
    {
        "RetrievalQuery",
        "RetrievalCandidate",
        "FusedCandidate",
        "RankedResult",
        "RetrievalResponse",
    },
)

# D213 — graph read models
D213_GRAPH_READ_ERRORS = run_target(
    "D213 graph-read-models",
    repo / "src/graph/graph_read_models.py",
    {
        "EntityRecord",
        "RelationshipRecord",
        "PagedEntitiesResponse",
        "PagedRelationshipsResponse",
        "NeighborhoodResponse",
    },
)

# D283 — recon-api (Chunk 36 + Chunk 37). Backend in src/api/recon_models.py;
# frontend mirror in recon-types.ts. Chunk 37 D284/D286/D287 add the
# Cross-Executive Divergence Map and Documented Reality Report models.
D283_RECON_ERRORS = run_target(
    "D283 recon-api",
    repo / "src/api/recon_models.py",
    {
        "EmphasizedWithEvidenceItem",
        "EmphasizedWithoutEvidenceItem",
        "UnemphasizedInEvidenceItem",
        "GapReportResponse",
        # Chunk 37 amendments.
        "DivergenceMapEntry",
        "DivergenceMapBucket",
        "DivergenceMapResponse",
        "DocumentedRealityAggregations",
        "DocumentedRealityReportResponse",
        "DocumentedRealityScheduleResponse",
        "DocumentedRealityScheduleRequest",
        "DocumentedRealityScheduleUpdateRequest",
    },
    frontend_path=repo / "frontend/lib/api/recon-types.ts",
)

# Chunk 38 — change-directives-api target (D283 continuity)
D38_CD_ERRORS = run_target(
    "Chunk38 change-directives-api",
    repo / "src/change_directives/models.py",
    {
        "EvidenceCriterion",
        "ChangeDirectivePatchBody",
        "CoveringDirective",
        "CriterionCounterEvidence",
        "CriterionEvidenceResult",
        "RealizationSnapshotPayload",
        "TransitionRequest",
        "CriterionCreateRequest",
        "CriterionPatchRequest",
        "ChangeDirectiveCreateRequest",
    },
)

# Chunk 47 — proposal EvidenceBundle vs frontend/lib/api/proposals.ts (CP8).
D47_PROPOSAL_ERRORS = run_target(
    "Chunk47 proposal-api",
    repo / "src/ontology/evidence_bundle.py",
    {"EvidenceBundle"},
    frontend_path=repo / "frontend/lib/api/proposals.ts",
)

# CF1 — elicitation union (Chunk 29)
# Verify EventType Literal + _PAYLOAD_MODELS keys match frontend
# ElicitationEventType union and ELICITATION_EVENT_TYPES Set.

CF1_ERRORS: list[str] = []

# 1. Extract EventType Literal from Python
models_path = repo / "src/elicitation/models.py"
models_src = models_path.read_text(encoding="utf-8")
import re as _re
event_type_match = _re.search(
    r'EventType\s*=\s*Literal\[(.*?)\]',
    models_src,
    _re.S,
)
py_event_types: set[str] = set()
if event_type_match:
    for m in _re.finditer(r'"([^"]+)"', event_type_match.group(1)):
        py_event_types.add(m.group(1))

# 2. Extract _PAYLOAD_MODELS keys
payload_match = _re.search(
    r'_PAYLOAD_MODELS.*?=\s*\{(.*?)\}',
    models_src,
    _re.S,
)
py_payload_keys: set[str] = set()
if payload_match:
    for m in _re.finditer(r'"([^"]+)"', payload_match.group(1)):
        py_payload_keys.add(m.group(1))

# 3. Extract ElicitationEventType union from TS
ts_src = frontend.read_text(encoding="utf-8")
ts_event_match = _re.search(
    r'export\s+type\s+ElicitationEventType\s*=(.*?);',
    ts_src,
    _re.S,
)
ts_event_types: set[str] = set()
if ts_event_match:
    for m in _re.finditer(r'"([^"]+)"', ts_event_match.group(1)):
        ts_event_types.add(m.group(1))

# 4. Extract ELICITATION_EVENT_TYPES Set from bridge.ts
bridge_path = repo / "frontend/lib/telemetry/bridge.ts"
bridge_src = bridge_path.read_text(encoding="utf-8")
bridge_set_match = _re.search(
    r'ELICITATION_EVENT_TYPES.*?=.*?new Set.*?\(\[(.*?)\]\)',
    bridge_src,
    _re.S,
)
bridge_event_types: set[str] = set()
if bridge_set_match:
    for m in _re.finditer(r'"([^"]+)"', bridge_set_match.group(1)):
        bridge_event_types.add(m.group(1))

# Compare all four
if py_event_types != py_payload_keys:
    CF1_ERRORS.append(
        f"[CF1] EventType Literal ({len(py_event_types)}) != _PAYLOAD_MODELS keys ({len(py_payload_keys)})\n"
        f"  missing from PAYLOAD_MODELS: {py_event_types - py_payload_keys}\n"
        f"  extra in PAYLOAD_MODELS: {py_payload_keys - py_event_types}"
    )
if py_event_types != ts_event_types:
    CF1_ERRORS.append(
        f"[CF1] Python EventType ({len(py_event_types)}) != TS ElicitationEventType ({len(ts_event_types)})\n"
        f"  missing from TS: {py_event_types - ts_event_types}\n"
        f"  extra in TS: {ts_event_types - py_event_types}"
    )
if py_event_types != bridge_event_types:
    CF1_ERRORS.append(
        f"[CF1] Python EventType ({len(py_event_types)}) != bridge ELICITATION_EVENT_TYPES ({len(bridge_event_types)})\n"
        f"  missing from bridge: {py_event_types - bridge_event_types}\n"
        f"  extra in bridge: {bridge_event_types - py_event_types}"
    )

all_errors = (
    D204_ERRORS
    + D213_RETRIEVAL_ERRORS
    + D213_GRAPH_READ_ERRORS
    + CF1_ERRORS
    + D283_RECON_ERRORS
    + D38_CD_ERRORS
    + D47_PROPOSAL_ERRORS
)

if all_errors:
    print("Contract check FAILED:")
    print("\n".join(all_errors))
    sys.exit(1)

print(
    "Contract check OK (D204 + D213 retrieval + D213 graph-read-models + "
    "CF1 elicitation union + D283 recon-api + Chunk38 change-directives-api + "
    "Chunk47 proposal-api)"
)
PY
