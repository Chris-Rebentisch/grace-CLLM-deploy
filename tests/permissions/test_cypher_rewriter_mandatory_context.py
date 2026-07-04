"""Mandatory-context contract on ``cypher_rewriter.rewrite()`` (Chunk 43, CP4, D346).

Chunk 42 shipped ``principal: PrincipalContext | None = None``. Chunk 43
flips it to a mandatory keyword (``*, principal: PrincipalContext``).
These tests are the runtime contract for the flip; the static lint at
``scripts/lint/check-cypher-principal-context.sh`` enforces the call-
site posture across the source tree.
"""

from __future__ import annotations

import pytest

from src.permissions.cypher_rewriter import rewrite
from src.permissions.principal_context import User
from src.permissions.system_principal import SYSTEM_PRINCIPAL


# ----- TypeError when principal is omitted -------------------------------


def test_missing_principal_raises_typeerror():
    """No ``principal=`` keyword → ``TypeError`` (mandatory)."""
    with pytest.raises(TypeError):
        rewrite("MATCH (n) RETURN n")  # type: ignore[call-arg]


def test_missing_principal_with_other_kwargs_raises_typeerror():
    """Other kwargs do not satisfy the mandatory-principal requirement."""
    with pytest.raises(TypeError):
        rewrite(  # type: ignore[call-arg]
            "MATCH (n:Doc) RETURN n",
            allow_modules=["finance"],
        )


def test_principal_is_keyword_only_not_positional():
    """The signature uses ``*`` to enforce keyword-only after ``query``
    so passing the principal positionally is a ``TypeError``."""
    with pytest.raises(TypeError):
        rewrite("MATCH (n) RETURN n", SYSTEM_PRINCIPAL)  # type: ignore[misc]


# ----- Acceptance with the system sentinel ------------------------------


def test_explicit_system_principal_accepted():
    """``SYSTEM_PRINCIPAL`` is a valid principal; the call returns a
    rewritten query."""
    result = rewrite("MATCH (n:Doc) RETURN n", principal=SYSTEM_PRINCIPAL)
    assert "MATCH" in result.query


def test_explicit_user_principal_accepted():
    """A plain ``User`` instance is also accepted (HTTP-route path)."""
    user = User()
    result = rewrite("MATCH (n) RETURN n", principal=user)
    assert "MATCH" in result.query


# ----- Behavior parity with v1 ------------------------------------------


def test_passthrough_when_no_allow_modules():
    """With ``principal`` provided but ``allow_modules`` absent, the
    rewriter still passes the query through after the safety check —
    no predicate injected."""
    result = rewrite("MATCH (n:Doc) RETURN n", principal=SYSTEM_PRINCIPAL)
    assert "ontology_module" not in result.query
    assert result.params == {}


def test_allow_modules_predicate_injected_with_principal():
    """When ``allow_modules`` is supplied alongside ``principal=``, the
    rewriter injects the WHERE clause exactly as in v1."""
    result = rewrite(
        "MATCH (n:Doc) RETURN n",
        principal=SYSTEM_PRINCIPAL,
        allow_modules=["finance"],
    )
    assert "ontology_module" in result.query
    assert result.params == {"_perm_allow_modules": ["finance"]}


def test_empty_allow_modules_still_injects_with_principal():
    """``allow_modules=[]`` is the default-deny posture (R6 mitigation);
    rewriter injects the predicate so the runtime returns zero rows."""
    result = rewrite(
        "MATCH (n:Doc) RETURN n",
        principal=SYSTEM_PRINCIPAL,
        allow_modules=[],
    )
    assert "ontology_module" in result.query
    assert result.params == {"_perm_allow_modules": []}
