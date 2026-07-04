"""EvidenceCriterion NL->OpenCypher compile orchestrator (D293).

Authoring-time only — invokes the local Ollama provider through
``src/shared/llm_provider.py`` ``LLMProvider.generate()`` to propose a
Cypher query for an author-supplied natural-language statement, then
runs two-stage validation against ArcadeDB's ``EXPLAIN`` endpoint
(syntactic parse + semantic compile). On failure the result preserves
the proposal but flags it for manual override.

The compile path NEVER mutates the live graph — Stage 2 uses
``EXPLAIN`` only.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

from src.shared.llm_provider import LLMProvider

logger = structlog.get_logger()


CompilationStatus = Literal["proposed", "approved", "manually_authored"]


class CompileResult(BaseModel):
    """Outcome of an NL->Cypher compile attempt."""

    model_config = ConfigDict(extra="forbid")

    compiled_query: str | None
    compilation_status: CompilationStatus
    error_detail: str | None


_FEW_SHOT_PAIRS: list[tuple[str, str]] = [
    (
        "Count all Legal_Entity nodes in scope.",
        "MATCH (n:Legal_Entity) RETURN count(n) AS count",
    ),
    (
        "List all Insurance_Policy entities with their policy_number.",
        "MATCH (p:Insurance_Policy) RETURN p.policy_number AS policy_number",
    ),
    (
        "Find Legal_Entity nodes that participate in a Funding_Round.",
        "MATCH (e:Legal_Entity)-[:participates_in]->(f:Funding_Round) RETURN e, f",
    ),
]


_SYSTEM_PROMPT = (
    "You are a Cypher query author for the GrACE knowledge graph. "
    "Given a natural-language criterion and the ratified schema for "
    "a segment, produce one OpenCypher query that, when EXPLAIN'd, "
    "reflects the criterion. Use ONLY node labels and relationship types "
    "that appear in the supplied schema vocabulary — never invent new ones. "
    "Respond with strict JSON: "
    '{"compiled_query": "<MATCH ...>"}.'
)


def schema_vocabulary(segment_schema: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Extract ``(entity_type_names, relationship_names)`` from a segment schema.

    Accepts both the ratified ``schema_json`` shape (``entity_types`` /
    ``relationships`` dicts) and the looser legacy segment shapes
    (``types`` / ``relationship_types`` lists).
    """
    def _names(value: Any) -> set[str]:
        if isinstance(value, dict):
            return {str(k) for k in value}
        if isinstance(value, (list, tuple, set)):
            return {str(v) for v in value}
        return set()

    entity_names = _names(segment_schema.get("entity_types")) | _names(
        segment_schema.get("types")
    )
    relationship_names = _names(segment_schema.get("relationships")) | _names(
        segment_schema.get("relationship_types")
    )
    return (entity_names, relationship_names)


# Node label in a node pattern: `(n:Label`, `(:Label`, optional backticks.
_NODE_LABEL_RE = re.compile(r"\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?|\(\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
# Relationship type in a rel pattern: `[r:type`, `[:type`, optional backticks.
_REL_TYPE_RE = re.compile(r"\[\s*[A-Za-z_]?[A-Za-z0-9_]*\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")


def extract_cypher_vocabulary(cypher: str) -> tuple[set[str], set[str]]:
    """Best-effort parse of ``(node_labels, relationship_types)`` from Cypher."""
    labels: set[str] = set()
    for m in _NODE_LABEL_RE.finditer(cypher):
        label = m.group(1) or m.group(2)
        if label:
            labels.add(label)
    rels = {m.group(1) for m in _REL_TYPE_RE.finditer(cypher) if m.group(1)}
    return (labels, rels)


def find_off_schema_tokens(
    cypher: str, segment_schema: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Return ``(unknown_labels, unknown_relationships)`` used by ``cypher``.

    Each side is checked only when the schema actually supplies vocabulary
    for that side (an empty vocab means "unknown schema", not "everything
    is off-schema").
    """
    entity_names, relationship_names = schema_vocabulary(segment_schema)
    labels, rels = extract_cypher_vocabulary(cypher)
    unknown_labels = sorted(labels - entity_names) if entity_names else []
    unknown_rels = sorted(rels - relationship_names) if relationship_names else []
    return (unknown_labels, unknown_rels)


def vocabulary_error_detail(
    cypher: str, segment_schema: dict[str, Any]
) -> str | None:
    """Return the ``vocabulary: off_schema_tokens ...`` error string, or ``None``.

    Capture-the-why (F-0047c / ISS-0054 PATCH follow-up, 2026-07-03):
    factored out of :func:`compile_evidence_criterion` so the PATCH route
    (``approve`` / ``edit`` / ``manual_override``) can run the SAME
    vocabulary membership check the create path runs. The check needs only
    the DB-stored ratified schema — no ArcadeDB round-trip — so it is
    always available, even when the graph server is down.
    """
    unknown_labels, unknown_rels = find_off_schema_tokens(cypher, segment_schema)
    if not (unknown_labels or unknown_rels):
        return None
    logger.warning(
        "evidence_criterion.off_schema_tokens",
        unknown_labels=unknown_labels,
        unknown_relationships=unknown_rels,
    )
    detail_parts = []
    if unknown_labels:
        detail_parts.append(f"labels={unknown_labels}")
    if unknown_rels:
        detail_parts.append(f"relationships={unknown_rels}")
    return "vocabulary: off_schema_tokens " + " ".join(detail_parts)


async def validate_operator_cypher(
    cypher: str,
    segment_schema: dict[str, Any],
    *,
    explain_client: httpx.AsyncClient | None = None,
) -> tuple[bool, str | None]:
    """Full create-path validation ladder for operator-supplied Cypher.

    Returns ``(ok, error_detail)``. Runs the exact stages the create path
    runs, in the same order:

    1. Vocabulary membership vs. the ratified schema (DB-only, no ArcadeDB).
    2. Stage 1 EXPLAIN — syntactic parse.
    3. Stage 2 EXPLAIN — semantic compile (plan-only, never mutates).

    Capture-the-why (F-0047c / ISS-0054 PATCH follow-up, 2026-07-03): the
    PATCH ``edit`` / ``manual_override`` actions accepted operator Cypher
    with NO validation — an operator could hand-write off-schema or
    syntactically broken Cypher into an "approved" criterion that silently
    never matched at snapshot time. This validator closes that gap.
    Graceful degradation mirrors create: if ArcadeDB is unreachable,
    ``_explain_query`` returns ``arcade_unreachable: ...`` and the result
    is a validation FAILURE (criterion stays ``proposed`` with the error
    named) — identical to how a create-time compile degrades.
    """
    vocab_err = vocabulary_error_detail(cypher, segment_schema)
    if vocab_err is not None:
        return (False, vocab_err)

    owns_client = explain_client is None
    client = explain_client or httpx.AsyncClient()
    try:
        ok, err = await _explain_query(cypher, client=client, semantic=False)
        if not ok:
            return (False, f"syntactic: {err}")
        ok2, err2 = await _explain_query(cypher, client=client, semantic=True)
        if not ok2:
            return (False, f"semantic: {err2}")
    finally:
        if owns_client:
            await client.aclose()
    return (True, None)


def _build_user_prompt(natural_language: str, segment_schema: dict[str, Any]) -> str:
    schema_blob = json.dumps(segment_schema, indent=2)[:4000]
    examples = "\n\n".join(
        f"NL: {nl}\nCypher: {cy}" for nl, cy in _FEW_SHOT_PAIRS
    )
    # F-0047c / ISS-0054 (validation run 2026-07-03): the compiler
    # generated OFF-SCHEMA Cypher (`Zoning` label, `has_zoning` edge) that
    # EXPLAIN'd fine but could never be satisfied. Inject an explicit
    # allowed-vocabulary legend so the model is grounded in the ratified
    # schema, not its imagination.
    entity_names, relationship_names = schema_vocabulary(segment_schema)
    vocab_lines = ""
    if entity_names:
        vocab_lines += (
            "Allowed node labels (use ONLY these): "
            + ", ".join(sorted(entity_names))
            + "\n"
        )
    if relationship_names:
        vocab_lines += (
            "Allowed relationship types (use ONLY these): "
            + ", ".join(sorted(relationship_names))
            + "\n"
        )
    return (
        f"Schema (truncated):\n{schema_blob}\n\n"
        f"{vocab_lines}"
        f"Examples:\n{examples}\n\n"
        f"NL: {natural_language}\n"
        "Cypher:"
    )


def _strip_code_fences(payload: str) -> str:
    """Strip markdown ``` / ```json fences if present.

    Phase-11 fix: Haiku (and to a lesser extent other providers) sometimes
    wraps JSON responses in ```json ... ``` even when ``json_mode=True``.
    Without fence-stripping, ``json.loads`` fails and the criterion compile
    falls through to ``llm_returned_no_compiled_query``. Same defensive
    pattern as ``src/decomposition/layer4_synthesize.py:_strip_code_fences``.
    """
    out = payload.strip()
    if out.startswith("```"):
        out = out.strip("`")
        if out.lower().startswith("json"):
            out = out[4:]
        out = out.strip()
    return out


def _extract_compiled_query(text: str) -> str | None:
    """Pull a Cypher string out of an LLM JSON response (best-effort)."""
    stripped = _strip_code_fences(text)
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            value = parsed.get("compiled_query")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _arcade_explain_url() -> str:
    base = os.environ.get("ARCADE_BASE_URL", "http://localhost:2480")
    db = os.environ.get("ARCADE_DATABASE", "grace")
    return f"{base.rstrip('/')}/api/v1/query/{db}"


def _arcade_auth() -> tuple[str, str]:
    user = os.environ.get("ARCADE_USER", "root")
    password = os.environ.get("ARCADE_PASSWORD", "gracedev")
    return (user, password)


async def _explain_query(
    cypher: str,
    *,
    client: httpx.AsyncClient,
    semantic: bool = False,
) -> tuple[bool, str | None]:
    """Run an ``EXPLAIN`` against ArcadeDB. Returns ``(ok, error_detail)``.

    Stage 1 (syntactic) treats a non-2xx response as parse failure.
    Stage 2 (semantic) runs EXPLAIN against the live graph but never
    mutates because ``EXPLAIN`` is plan-only.
    """
    payload = {"language": "opencypher", "command": f"EXPLAIN {cypher}"}
    try:
        resp = await client.post(
            _arcade_explain_url(),
            json=payload,
            auth=_arcade_auth(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        return (False, f"arcade_unreachable: {exc!s}")
    if resp.status_code >= 400:
        return (False, f"explain_http_{resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json()
    except ValueError:
        return (False, "explain_non_json_response")
    if isinstance(body, dict) and "error" in body:
        return (False, str(body["error"])[:300])
    return (True, None)


async def compile_evidence_criterion(
    natural_language: str,
    segment_schema: dict[str, Any],
    llm_provider: LLMProvider,
    *,
    explain_client: httpx.AsyncClient | None = None,
) -> CompileResult:
    """NL -> OpenCypher with two-stage validation (D293).

    Returns a :class:`CompileResult` with ``compilation_status="proposed"``
    when both stages pass. On any failure, returns the proposal (or
    ``None``) with ``compilation_status="proposed"`` plus a populated
    ``error_detail`` so the UI can surface manual-override mode.
    """
    user_prompt = _build_user_prompt(natural_language, segment_schema)
    try:
        response = await llm_provider.generate(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=512,
            json_mode=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence_criterion.compile.llm_error", error=str(exc))
        return CompileResult(
            compiled_query=None,
            compilation_status="proposed",
            error_detail=f"llm_error: {exc!s}",
        )

    proposed = _extract_compiled_query(response.text)
    if proposed is None:
        return CompileResult(
            compiled_query=None,
            compilation_status="proposed",
            error_detail="llm_returned_no_compiled_query",
        )

    # Vocabulary check (third validation stage alongside the two EXPLAIN
    # stages, run first because it needs no ArcadeDB round-trip).
    # Capture-the-why (F-0047c / ISS-0054, validation run 2026-07-03):
    # the compiler emitted `has_zoning` / `Zoning` — neither in the ratified
    # schema. EXPLAIN passes on off-schema tokens (ArcadeDB plans them
    # fine), so the criterion compiled "successfully" but could NEVER be
    # satisfied. Unknown tokens now degrade to compilation_status="proposed"
    # with error_detail NAMING the off-schema tokens — never silently
    # accepted. Each side is enforced only when the schema supplies that
    # side's vocabulary.
    # (ISS-0054 PATCH follow-up: check now shared with the PATCH route via
    # vocabulary_error_detail(); error string unchanged.)
    vocab_err = vocabulary_error_detail(proposed, segment_schema)
    if vocab_err is not None:
        return CompileResult(
            compiled_query=proposed,
            compilation_status="proposed",
            error_detail=vocab_err,
        )

    owns_client = explain_client is None
    client = explain_client or httpx.AsyncClient()
    try:
        # Stage 1: syntactic parse via EXPLAIN.
        ok, err = await _explain_query(proposed, client=client, semantic=False)
        if not ok:
            return CompileResult(
                compiled_query=proposed,
                compilation_status="proposed",
                error_detail=f"syntactic: {err}",
            )
        # Stage 2: semantic compile (EXPLAIN is plan-only).
        ok2, err2 = await _explain_query(proposed, client=client, semantic=True)
        if not ok2:
            return CompileResult(
                compiled_query=proposed,
                compilation_status="proposed",
                error_detail=f"semantic: {err2}",
            )
    finally:
        if owns_client:
            await client.aclose()

    return CompileResult(
        compiled_query=proposed,
        compilation_status="proposed",
        error_detail=None,
    )
