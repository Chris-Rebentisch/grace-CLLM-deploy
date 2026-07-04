"""Tokenizer + recursive-descent parser for the OpenCypher subset
covered by GrACE's 28 ``arcade_client.execute_cypher()`` call sites
(D335 N1 inventory).

The parser keeps the AST shallow on purpose: each statement decomposes
into clause segments preserving the original token stream so the
rewriter (``rewriter.inject_where_clause``) can splice in additional
WHERE predicates without re-emitting the whole query. This avoids
correctness regressions in pre-existing callers.

Covered surface (per spec §4 D335 N1 inventory):

* ``MATCH`` — node patterns, relationship patterns
  ``(a)-[r:TYPE]->(b)``, comma-join, anonymous end-nodes
  ``(a)-[r]->()``.
* ``WHERE`` — property equality / inequality, ``IS NULL`` / ``IS NOT
  NULL``, ``CONTAINS``, ``IN [list]``, ``AND``.
* ``RETURN`` (with ``AS`` aliases), ``CREATE`` (vertex, relationship),
  ``SET``, ``SKIP``, ``LIMIT``, ``ORDER BY``.
* ``type(r)`` function, ``count(*)`` aggregation.
* ``$param`` parameter binding.
* Variable-length paths ``[*1..N]``.

Explicitly NOT covered (raises ``CypherSyntaxError``):

* ``OPTIONAL MATCH``, ``WITH``, ``UNWIND``, ``MERGE``, ``DELETE``,
  ``DETACH DELETE``, subqueries (``CALL { ... }``), list
  comprehensions.

The parser is lossless for unknown-but-not-banned tokens — those
flow through to the rewriter as opaque strings. The goal is
*permission-clause injection*, not *static analysis*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Reserved keyword set — uppercase canonical form. Matched
# case-insensitively in the tokenizer.
_RESERVED = frozenset(
    {
        "MATCH",
        "WHERE",
        "RETURN",
        "CREATE",
        "SET",
        "SKIP",
        "LIMIT",
        "ORDER",
        "BY",
        "ASC",
        "DESC",
        "AND",
        "OR",
        "NOT",
        "IS",
        "NULL",
        "IN",
        "CONTAINS",
        "AS",
        "DISTINCT",
    }
)

# Banned clauses — anything in this set causes ``CypherSyntaxError``.
# These are NOT in the GrACE caller surface; encountering them at
# parse time means a caller has drifted. The rewriter's contract is
# "everything that parses can be safely permission-gated"; banned
# constructs do NOT parse.
_BANNED = frozenset(
    {
        "OPTIONAL",
        "WITH",
        "UNWIND",
        "MERGE",
        "DELETE",
        "DETACH",
        "CALL",
    }
)


class CypherSyntaxError(ValueError):
    """Raised when a query contains tokens outside the supported subset.

    Includes the offending token so callers can write the call site to
    the v1 opt-in allowlist (``check-cypher-principal-context.sh``) or
    expand the rewriter coverage when D-numbered.
    """


@dataclass
class _Token:
    kind: str  # "keyword" | "ident" | "param" | "string" | "number" |
    # "punct" | "op"
    value: str
    pos: int


# Tokenizer regex — order matters (string before ident; double-arrow
# before single-arrow; etc.).
_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
    | (?P<comment>//[^\n]*)
    | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<param>\$[A-Za-z_][A-Za-z_0-9]*)
    | (?P<arrow_l>< - \[ | <-\[ | <- )
    | (?P<arrow_r>\] - > | \]-> | -> )
    | (?P<number>-?\d+(?:\.\d+)?)
    | (?P<ident>[A-Za-z_][A-Za-z_0-9]*)
    | (?P<op>>=|<=|<>|!=|=|<|>)
    | (?P<punct>[\(\)\[\]\{\},\.\:\;\*\-\+])
    """,
    re.VERBOSE,
)


def _tokenize(src: str) -> list[_Token]:
    """Lossy-but-faithful tokenizer.

    String literals are preserved verbatim (the rewriter rejects them
    later — parameter-bind enforcement). Whitespace + comments are
    dropped; the rewriter re-emits canonical single-space separators.
    """
    out: list[_Token] = []
    pos = 0
    while pos < len(src):
        match = _TOKEN_RE.match(src, pos)
        if match is None:
            raise CypherSyntaxError(
                f"unexpected character at offset {pos}: {src[pos]!r}"
            )
        group = match.lastgroup
        text = match.group()
        if group in ("ws", "comment"):
            pos = match.end()
            continue
        if group == "ident":
            upper = text.upper()
            if upper in _BANNED:
                raise CypherSyntaxError(
                    f"unsupported clause '{text}' at offset {pos}"
                )
            kind = "keyword" if upper in _RESERVED else "ident"
            out.append(_Token(kind, upper if kind == "keyword" else text, pos))
        elif group == "string":
            out.append(_Token("string", text, pos))
        elif group == "param":
            out.append(_Token("param", text, pos))
        elif group == "number":
            out.append(_Token("number", text, pos))
        elif group in ("arrow_l", "arrow_r"):
            # Normalize spaces inside arrow tokens.
            out.append(
                _Token(
                    "op",
                    text.replace(" ", ""),
                    pos,
                )
            )
        elif group == "op":
            out.append(_Token("op", text, pos))
        elif group == "punct":
            out.append(_Token("punct", text, pos))
        pos = match.end()
    return out


@dataclass
class CypherAST:
    """Lossless-on-clause-boundaries AST.

    The clause list preserves original-order ``(name, body_tokens)``
    pairs. ``body_tokens`` are the raw token stream for that clause
    (between this keyword and the next top-level keyword), which the
    rewriter rebroadcasts unchanged when emitting the rewritten query.
    """

    clauses: list[tuple[str, list[_Token]]] = field(default_factory=list)

    @property
    def has_where(self) -> bool:
        return any(name == "WHERE" for name, _ in self.clauses)

    @property
    def has_match(self) -> bool:
        return any(name == "MATCH" for name, _ in self.clauses)

    def emit(self) -> str:
        """Re-emit the query as a single canonical-spaced string."""
        parts: list[str] = []
        for name, tokens in self.clauses:
            parts.append(name)
            parts.append(_emit_tokens(tokens))
        return " ".join(p for p in parts if p)


def _emit_tokens(tokens: list[_Token]) -> str:
    """Re-emit token list with conservative spacing.

    The intent is round-trip fidelity for the rewriter; not pretty
    printing. Punctuation that conventionally hugs its neighbor is
    emitted without leading whitespace.
    """
    out: list[str] = []
    for tok in tokens:
        if tok.kind == "punct" and tok.value in {")", "]", ",", ".", ":"}:
            if out:
                out[-1] = out[-1] + tok.value
            else:
                out.append(tok.value)
        elif tok.kind == "punct" and tok.value in {"(", "["}:
            out.append(tok.value)
        else:
            if out and out[-1].endswith(("(", "[", ":", ".")):
                out[-1] = out[-1] + tok.value
            else:
                out.append(tok.value)
    return " ".join(out)


# Top-level clause keywords — ORDER BY is a two-word keyword handled
# specially in the parser.
_TOP_CLAUSES = frozenset(
    {"MATCH", "WHERE", "RETURN", "CREATE", "SET", "SKIP", "LIMIT"}
)


def parse_cypher(query: str) -> CypherAST:
    """Parse ``query`` into a clause-segmented AST.

    Raises ``CypherSyntaxError`` on banned constructs or malformed
    queries.
    """
    if not query or not query.strip():
        raise CypherSyntaxError("empty query")

    tokens = _tokenize(query)
    if not tokens:
        raise CypherSyntaxError("query produced no tokens after tokenization")

    ast = CypherAST()
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.kind != "keyword":
            raise CypherSyntaxError(
                f"expected clause keyword at offset {tok.pos}, "
                f"got {tok.kind} {tok.value!r}"
            )

        # ORDER BY two-word keyword.
        if tok.value == "ORDER":
            if i + 1 >= n or tokens[i + 1].value != "BY":
                raise CypherSyntaxError(
                    f"expected 'BY' after 'ORDER' at offset {tok.pos}"
                )
            clause_name = "ORDER BY"
            body_start = i + 2
        elif tok.value in _TOP_CLAUSES:
            clause_name = tok.value
            body_start = i + 1
        else:
            raise CypherSyntaxError(
                f"unexpected keyword '{tok.value}' at offset {tok.pos}"
            )

        # Walk forward until the next top-level keyword (or ORDER).
        j = body_start
        while j < n:
            t = tokens[j]
            if t.kind == "keyword" and (
                t.value in _TOP_CLAUSES or t.value == "ORDER"
            ):
                break
            j += 1
        body = tokens[body_start:j]
        ast.clauses.append((clause_name, body))
        i = j

    return ast


def has_string_literal(ast: CypherAST) -> bool:
    """Return True iff any clause body contains a string literal token.

    Parameter-bind enforcement: the rewriter rejects queries where
    callers inlined string values instead of binding ``$params``.
    """
    for _, body in ast.clauses:
        for tok in body:
            if tok.kind == "string":
                return True
    return False
