"""Tests for ``SystemPrincipal`` sentinel (Chunk 43, CP4, D346)."""

from __future__ import annotations

from src.permissions.principal_context import (
    PrincipalContext,
    ScopeEntry,
    User,
)
from src.permissions.system_principal import (
    SYSTEM_PRINCIPAL,
    SystemPrincipal,
)


def test_sentinel_construction():
    """``SystemPrincipal()`` constructs without arguments and carries
    the ``is_system`` Literal-True flag."""
    sp = SystemPrincipal()
    assert sp.is_system is True
    assert sp.kind == "user"  # discriminator unchanged


def test_singleton_identity_across_imports():
    """Re-importing ``SYSTEM_PRINCIPAL`` returns the same object."""
    from src.permissions.system_principal import SYSTEM_PRINCIPAL as A
    from src.permissions.system_principal import SYSTEM_PRINCIPAL as B

    assert A is B is SYSTEM_PRINCIPAL


def test_is_system_attribute_default_true():
    """The exported singleton has ``is_system=True``."""
    assert SYSTEM_PRINCIPAL.is_system is True


def test_subclass_of_user():
    """``SystemPrincipal`` IS-A ``User`` so it satisfies the
    ``PrincipalContext`` discriminated union (kind=='user')."""
    assert isinstance(SYSTEM_PRINCIPAL, User)
    assert SYSTEM_PRINCIPAL.kind == "user"


def test_singleton_carries_default_user_fields():
    """Default ``User`` fields are inherited and start in their safe
    'no-data' state — no caller identity, no admin-key bypass, empty
    scope."""
    assert SYSTEM_PRINCIPAL.user_id is None
    assert SYSTEM_PRINCIPAL.display_name is None
    assert SYSTEM_PRINCIPAL.admin_key_present is False
    assert SYSTEM_PRINCIPAL.scope == []


def test_satisfies_principal_context_type():
    """A function annotated as ``PrincipalContext`` accepts the
    sentinel — the discriminated union resolves on ``kind=='user'``.
    Pure type-level check; no Pydantic validation required at the call
    site."""

    def accept(p: PrincipalContext) -> str:
        return p.kind

    assert accept(SYSTEM_PRINCIPAL) == "user"


def test_sentinel_can_carry_explicit_scope_entries():
    """A caller may construct a ``SystemPrincipal`` with non-empty
    ``scope`` for tests / explicit narrowing — the parent ``User``
    field is still available."""
    entry = ScopeEntry(
        resource_kind="ontology_module",
        resource_label="finance",
        action="view",
    )
    sp = SystemPrincipal(scope=[entry])
    assert sp.is_system is True
    assert sp.scope == [entry]
