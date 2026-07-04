"""Public Cypher rewriter API (Chunk 42, D335).

Wraps the internal ``_cypher_ast`` package. v1 ships opt-in-at-call-site;
Chunk 43 flips to mandatory-context (Q5 — hand-rolled parser locked).

Typical use::

    from src.permissions.cypher_rewriter import rewrite

    rewritten = rewrite(
        "MATCH (n:Document) RETURN n",
        principal=principal,
        allow_modules=["finance", "policy"],
    )

    arcade_client.execute_cypher(
        rewritten.query,
        params={**rewritten.params, "user_provided": ...},
    )

The rewriter rejects queries containing inlined string literals; use
``$param`` binding instead. This is parameter-bind enforcement (R6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.permissions._cypher_ast import (
    CypherAST,
    CypherSyntaxError,
    inject_where_clause,
    parse_cypher,
)
from src.permissions._cypher_ast.parser import has_string_literal
from src.permissions.principal_context import PrincipalContext
from src.permissions.sensitivity_resolver import resolve_forbidden_tags

__all__ = [
    "CypherSyntaxError",
    "RewrittenQuery",
    "rewrite",
]


@dataclass
class RewrittenQuery:
    """Result of a rewrite call.

    Attributes:
        query: rewritten OpenCypher source.
        params: additional parameters the caller must merge in.
        ast: parsed AST (exposed for tests + debugging).
    """

    query: str
    params: dict[str, Any] = field(default_factory=dict)
    ast: CypherAST | None = None


def _module_predicate(node_var: str, param_name: str) -> str:
    return f"({node_var}.ontology_module IN ${param_name})"


def _sensitivity_predicates(
    node_var: str,
    forbidden_tags: set[str],
) -> list[str]:
    """Build sensitivity-tag filter predicates for each forbidden tag.

    D521 — injects NOT (n.sensitivity_tags CONTAINS '|{tag}|') for each
    forbidden tag. Multiple forbidden tags are AND-joined (entity must
    not contain ANY forbidden tag).
    """
    predicates = []
    for tag in sorted(forbidden_tags):
        predicates.append(
            f"NOT ({node_var}.sensitivity_tags CONTAINS '|{tag}|')"
        )
    return predicates


def rewrite(
    query: str,
    *,
    principal: PrincipalContext,
    allow_modules: list[str] | None = None,
    node_var: str = "n",
    param_name: str = "_perm_allow_modules",
    active_matrix: object | None = None,
) -> RewrittenQuery:
    """Parse + rewrite ``query`` to enforce ``allow_modules``.

    Args:
        query: OpenCypher source. Must use ``$param`` binding for any
            string values.
        principal: caller principal envelope (mandatory keyword as of
            Chunk 43 D346). HTTP routes resolve via
            ``from_admission_tree(request)``; internal pipelines pass
            ``SYSTEM_PRINCIPAL`` from
            ``src.permissions.system_principal``. D521 activates the
            principal for sensitivity-tag filtering when ``active_matrix``
            is provided.
        allow_modules: list of ontology_module values the principal may
            see. ``None`` means "no rewrite" — pass-through after a
            string-literal safety check. Empty list means "deny all" —
            rewriter still injects so the runtime returns zero rows.
        node_var: variable name in the query to gate. Defaults to ``n``.
        param_name: name of the parameter the rewriter binds the
            allow-list to.

    Returns:
        ``RewrittenQuery`` with ``query`` and any added ``params``.

    Raises:
        ``CypherSyntaxError`` if the query is outside the supported
        subset OR contains inlined string literals.
    """
    ast = parse_cypher(query)
    if has_string_literal(ast):
        raise CypherSyntaxError(
            "query contains inlined string literal; use $param binding"
        )

    # D521 — activate principal hook for domain-entity sensitivity filtering.
    # Extends D270 single-engine coverage (no new engine); D346 mandatory-
    # principal-context reused.
    forbidden_tags = resolve_forbidden_tags(principal, active_matrix)

    if allow_modules is None:
        # No module filter — still check for sensitivity predicates
        if forbidden_tags and ast.has_match:
            sens_preds = _sensitivity_predicates(node_var, forbidden_tags)
            combined = " AND ".join(sens_preds)
            # D521 — system-generated CONTAINS '|tag|' predicates require
            # inline bar-form string literals; _allow_system_literals
            # authorized by D521 capture-the-why.
            rewritten = inject_where_clause(
                ast, combined, _allow_system_literals=True,
            )
            return RewrittenQuery(query=rewritten, params={}, ast=ast)
        return RewrittenQuery(query=ast.emit(), params={}, ast=ast)

    # Even an empty allow-list still emits an injected predicate; the
    # runtime then returns zero rows. Deny-by-zero-list is the correct
    # default-deny posture.
    if not ast.has_match:
        # Pure CREATE / SET — gate at API layer instead.
        return RewrittenQuery(query=ast.emit(), params={}, ast=ast)

    predicates = [_module_predicate(node_var=node_var, param_name=param_name)]
    # Add sensitivity predicates if principal has forbidden tags
    if forbidden_tags:
        predicates.extend(_sensitivity_predicates(node_var, forbidden_tags))

    combined = " AND ".join(predicates)
    # D521 — when sensitivity predicates are present, system-generated
    # CONTAINS '|tag|' patterns require inline string literals;
    # _allow_system_literals authorized by D521 capture-the-why.
    rewritten = inject_where_clause(
        ast, combined,
        _allow_system_literals=bool(forbidden_tags),
    )
    return RewrittenQuery(
        query=rewritten,
        params={param_name: list(allow_modules)},
        ast=ast,
    )
