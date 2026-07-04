"""OpenCypher AST package — Chunk 42, D335.

Hand-rolled subset parser sized to the 28-call-site inventory in
``src/api/*`` and ``src/graph/*`` consuming
``arcade_client.execute_cypher()``. Public API lives at
``src.permissions.cypher_rewriter``; this internal package holds the
tokenizer, AST node types, and recursive-descent parser plus the
WHERE-clause injection rewriter.

NOT a full OpenCypher parser. Features explicitly outside the GrACE
caller surface (``OPTIONAL MATCH``, ``WITH``, ``UNWIND``, ``MERGE``,
``DELETE``, subqueries, list comprehensions) raise
``CypherSyntaxError``.
"""

from src.permissions._cypher_ast.parser import (
    CypherAST,
    CypherSyntaxError,
    parse_cypher,
)
from src.permissions._cypher_ast.rewriter import inject_where_clause

__all__ = [
    "CypherAST",
    "CypherSyntaxError",
    "inject_where_clause",
    "parse_cypher",
]
