"""WHERE-clause injection for the parsed AST (Chunk 42, D335).

Pure function — no DB / network. Given a parsed ``CypherAST``, splice
in additional permission predicates either:

* by appending to an existing ``WHERE`` clause (joined with ``AND``);
* by inserting a fresh ``WHERE`` clause immediately after the last
  ``MATCH`` clause; or
* by emitting the query unchanged when no ``MATCH`` clause exists
  (e.g. pure ``CREATE`` statements that the caller intends to gate at
  the API layer instead).
"""

from __future__ import annotations

from src.permissions._cypher_ast.parser import (
    CypherAST,
    CypherSyntaxError,
    _emit_tokens,
    _tokenize,
)


def inject_where_clause(
    ast: CypherAST,
    predicate: str,
    *,
    _allow_system_literals: bool = False,
) -> str:
    """Re-emit ``ast`` with ``predicate`` AND-joined into the WHERE.

    ``predicate`` is itself a Cypher fragment (e.g.
    ``n.ontology_module IN $allowed_modules``). It is tokenized and
    re-emitted to ensure consistency with the parser's spacing rules
    and to reject string literals inlined in the predicate too.

    Args:
        _allow_system_literals: internal flag — when ``True``, string
            literals in the predicate are accepted. Used ONLY for
            system-generated sensitivity-tag predicates (D521) whose
            ``CONTAINS '|tag|'`` patterns require inline bar-form
            delimiters. User-supplied predicates MUST NOT set this.
    """
    if not predicate.strip():
        # No-op rewrite.
        return ast.emit()

    pred_tokens = _tokenize(predicate)
    if not _allow_system_literals:
        for tok in pred_tokens:
            if tok.kind == "string":
                raise CypherSyntaxError(
                    "rewriter predicate contains string literal; "
                    "use $param binding instead"
                )

    pred_text = _emit_tokens(pred_tokens)

    out_parts: list[str] = []
    where_consumed = False
    last_match_idx: int | None = None
    for idx, (name, _) in enumerate(ast.clauses):
        if name == "MATCH":
            last_match_idx = idx

    for idx, (name, tokens) in enumerate(ast.clauses):
        body = _emit_tokens(tokens)
        if name == "WHERE" and not where_consumed:
            # AND-join into the existing WHERE.
            out_parts.append(f"WHERE {body} AND {pred_text}".strip())
            where_consumed = True
        elif (
            name == "MATCH"
            and not where_consumed
            and not ast.has_where
            and idx == last_match_idx
        ):
            # Insert a fresh WHERE right after the final MATCH.
            out_parts.append(f"MATCH {body}".strip())
            out_parts.append(f"WHERE {pred_text}")
            where_consumed = True
        else:
            out_parts.append(f"{name} {body}".strip() if body else name)

    if not where_consumed and ast.has_match:
        # Defensive fallthrough — should not be reachable given the
        # last-MATCH logic above.
        out_parts.append(f"WHERE {pred_text}")

    return " ".join(p for p in out_parts if p)
