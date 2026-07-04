"""Tests for Chunk 42 CP7 — Cypher AST parser + rewriter.

Test corpus is shaped by the D335 N1 28-call-site inventory: one test
per feature exercised + parameter-bind enforcement + WHERE-clause
injection round-trip.
"""

from __future__ import annotations

import pytest

from src.permissions.cypher_rewriter import (
    CypherSyntaxError,
    rewrite as _rewrite_raw,
)
from src.permissions.system_principal import SYSTEM_PRINCIPAL


def rewrite(*args, **kwargs):
    """Test wrapper — defaults ``principal`` to ``SYSTEM_PRINCIPAL``.

    Chunk 43 D346 made ``principal=`` mandatory on ``rewrite()``. These
    legacy tests pre-date the flip and exercise non-principal-related
    behavior (parser / AST / WHERE-injection round-trips), so the
    wrapper injects the system sentinel. The mandatory-context contract
    itself is exercised by
    ``tests/permissions/test_cypher_rewriter_mandatory_context.py``.
    """
    kwargs.setdefault("principal", SYSTEM_PRINCIPAL)
    return _rewrite_raw(*args, **kwargs)


# --- Feature coverage --------------------------------------------------


def test_match_node_pattern_passthrough() -> None:
    """``MATCH (n:Document) RETURN n`` parses + emits."""
    result = rewrite("MATCH (n:Document) RETURN n")
    assert "MATCH" in result.query
    assert "RETURN" in result.query


def test_match_relationship_pattern_with_type() -> None:
    """``(a)-[r:TYPE]->(b)`` round-trip."""
    result = rewrite("MATCH (a:Person)-[r:HAS_ROLE]->(b:Role) RETURN a, b")
    assert "HAS_ROLE" in result.query
    assert "RETURN" in result.query


def test_where_property_equality_and_in_list() -> None:
    """WHERE with ``=`` and ``IN $list``."""
    result = rewrite(
        "MATCH (n:Doc) WHERE n.kind = $k AND n.flag IN $flags RETURN n"
    )
    assert "WHERE" in result.query


def test_return_with_alias() -> None:
    """``RETURN x AS y`` round-trip."""
    result = rewrite("MATCH (n) RETURN n.name AS display_name")
    assert "AS" in result.query


def test_create_vertex() -> None:
    """``CREATE (n:Type {prop: $param})`` round-trip; no MATCH so no
    rewrite injected."""
    result = rewrite(
        "CREATE (n:Person {grace_id: $gid})",
        allow_modules=["finance"],
    )
    assert "CREATE" in result.query
    # No WHERE injected for pure-CREATE.
    assert "WHERE" not in result.query


def test_set_clause() -> None:
    """``MATCH ... SET ...`` round-trip."""
    result = rewrite("MATCH (n {grace_id: $gid}) SET n.name = $name")
    assert "SET" in result.query


def test_skip_limit_order_by() -> None:
    """SKIP/LIMIT/ORDER BY clauses round-trip."""
    result = rewrite(
        "MATCH (n:Doc) RETURN n ORDER BY n.created_at DESC SKIP $s LIMIT $l"
    )
    assert "ORDER BY" in result.query
    assert "SKIP" in result.query
    assert "LIMIT" in result.query


def test_param_binding_preserved() -> None:
    """``$named_param`` tokens preserved verbatim."""
    result = rewrite("MATCH (n {id: $node_id}) RETURN n")
    assert "$node_id" in result.query


def test_variable_length_path() -> None:
    """``[*1..3]`` variable-length path round-trips."""
    result = rewrite("MATCH (a)-[*1..3]->(b) RETURN a, b")
    assert "*" in result.query


def test_string_literal_rejected() -> None:
    """Inlined string literal raises CypherSyntaxError."""
    with pytest.raises(CypherSyntaxError):
        rewrite('MATCH (n {name: "Acme"}) RETURN n')


def test_where_clause_injection_appends_with_and() -> None:
    """When the query already has a WHERE, the rewriter appends with
    ``AND`` and binds the new param."""
    result = rewrite(
        "MATCH (n:Doc) WHERE n.kind = $k RETURN n",
        allow_modules=["finance", "policy"],
    )
    assert "WHERE" in result.query
    assert "AND" in result.query
    assert "ontology_module" in result.query
    assert result.params == {"_perm_allow_modules": ["finance", "policy"]}


def test_where_clause_injection_inserts_fresh_when_missing() -> None:
    """When MATCH but no WHERE, a fresh WHERE is inserted after MATCH."""
    result = rewrite(
        "MATCH (n:Doc) RETURN n",
        allow_modules=["finance"],
    )
    assert "WHERE" in result.query
    assert "ontology_module" in result.query
    # WHERE must come before RETURN.
    assert result.query.index("WHERE") < result.query.index("RETURN")


def test_banned_clause_optional_match_rejected() -> None:
    """``OPTIONAL MATCH`` is outside the supported subset."""
    with pytest.raises(CypherSyntaxError):
        rewrite("OPTIONAL MATCH (n) RETURN n")


def test_banned_clause_merge_rejected() -> None:
    """``MERGE`` is outside the supported subset."""
    with pytest.raises(CypherSyntaxError):
        rewrite("MERGE (n:Person {grace_id: $gid}) RETURN n")


def test_banned_clause_with_rejected() -> None:
    """``WITH`` is outside the supported subset."""
    with pytest.raises(CypherSyntaxError):
        rewrite("MATCH (n) WITH n RETURN n")


def test_empty_query_raises() -> None:
    """Empty/whitespace query raises CypherSyntaxError."""
    with pytest.raises(CypherSyntaxError):
        rewrite("   ")


def test_predicate_string_literal_rejected() -> None:
    """If the rewriter's own predicate had a string literal it would
    raise — guarded by parameter-bind enforcement on the predicate
    itself."""
    from src.permissions._cypher_ast import inject_where_clause, parse_cypher

    ast = parse_cypher("MATCH (n) RETURN n")
    with pytest.raises(CypherSyntaxError):
        inject_where_clause(ast, "n.name = 'Acme'")


def test_allow_modules_none_passes_through_unchanged() -> None:
    """No allow_modules means parse + emit + safety check only."""
    result = rewrite(
        "MATCH (n:Doc) WHERE n.kind = $k RETURN n",
        allow_modules=None,
    )
    assert "ontology_module" not in result.query
    assert result.params == {}


def test_allow_modules_empty_list_still_injects() -> None:
    """Empty allow-list still injects the predicate (deny-by-zero)."""
    result = rewrite("MATCH (n:Doc) RETURN n", allow_modules=[])
    assert "ontology_module" in result.query
    assert result.params == {"_perm_allow_modules": []}


# --- D521 sensitivity-tag predicate injection (Chunk 81) ------------------

def test_rewriter_injects_sensitivity_predicate() -> None:
    """Principal with restricted tags -> query gains NOT CONTAINS predicate.

    D521 — activates the v1-reserved principal hook for domain-entity
    sensitivity filtering.
    """
    from src.permissions.models import (
        AccessRule,
        PermissionMatrix,
        RoleCluster,
        SensitivityTag,
    )
    from src.permissions.principal_context import User

    # Matrix grants visibility to pii_dense only — privileged is forbidden
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="shared",
                display_name="Shared",
                sensitivity_tags=[SensitivityTag(name="pii_dense")],
            ),
        ],
    )
    principal = User()

    result = _rewrite_raw(
        "MATCH (n:Entity) RETURN n",
        principal=principal,
        allow_modules=["finance"],
        active_matrix=matrix,
    )
    # Should have NOT CONTAINS for privileged, external_boundary,
    # privilege_potentially_waived (all D426 tags except pii_dense)
    assert "NOT (n.sensitivity_tags CONTAINS '|privileged|')" in result.query
    assert "NOT (n.sensitivity_tags CONTAINS '|external_boundary|')" in result.query


def test_rewriter_no_predicate_when_full_visibility() -> None:
    """Principal with full visibility -> no sensitivity predicate injected.

    All D426 tags are visible — empty forbidden set.
    """
    from src.permissions.models import (
        PermissionMatrix,
        RoleCluster,
        SensitivityTag,
    )
    from src.permissions.principal_context import User

    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="admin",
                display_name="Admin",
                sensitivity_tags=[
                    SensitivityTag(name="privileged"),
                    SensitivityTag(name="pii_dense"),
                    SensitivityTag(name="external_boundary"),
                    SensitivityTag(name="privilege_potentially_waived"),
                ],
            ),
        ],
    )
    principal = User()

    result = _rewrite_raw(
        "MATCH (n:Entity) RETURN n",
        principal=principal,
        allow_modules=["finance"],
        active_matrix=matrix,
    )
    assert "CONTAINS" not in result.query


def test_rewriter_no_predicate_when_no_matrix() -> None:
    """No active matrix -> no predicate injected (backward-compatible)."""
    from src.permissions.principal_context import User

    principal = User()

    result = _rewrite_raw(
        "MATCH (n:Entity) RETURN n",
        principal=principal,
        allow_modules=["finance"],
        active_matrix=None,
    )
    assert "CONTAINS" not in result.query
