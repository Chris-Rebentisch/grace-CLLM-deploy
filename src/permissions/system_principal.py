"""SystemPrincipal sentinel for internal pipelines (Chunk 43, CP4, D346).

Chunk 43 flips ``cypher_rewriter.rewrite()`` from opt-in
``principal: PrincipalContext | None = None`` to mandatory keyword
``principal: PrincipalContext``. Production HTTP routes resolve a
principal via ``principal_context.from_admission_tree(request)``;
internal pipelines (signal pipeline, correlation engine, eval suite,
confidence-decay batch, etc.) have no Starlette ``Request`` and use the
module-level singleton ``SYSTEM_PRINCIPAL`` instead.

R12 invariant — ``src/api/*.py`` MUST NOT import ``SystemPrincipal`` /
``SYSTEM_PRINCIPAL``. The admission tree is the only legitimate
principal source for HTTP routes; smuggling the system sentinel into a
route handler bypasses the user-vs-agent scope intersection that the
admission tree guarantees. The CI lint at
``scripts/lint/check-cypher-principal-context.sh`` enforces this in
fail mode.

Default ``__repr__`` is sufficient (D346 Q7 resolution); no custom
formatting is needed because the sentinel is a stable singleton.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from src.permissions.principal_context import User


class SystemPrincipal(User):
    """Internal-pipeline principal sentinel.

    Subclasses ``User`` so it remains a structural ``PrincipalContext``
    member (the ``kind`` discriminator stays ``"user"``). The
    ``is_system`` literal-True flag distinguishes it for any downstream
    logic that needs to recognize an internal-pipeline caller.
    """

    model_config = ConfigDict(extra="forbid")

    is_system: Literal[True] = True


SYSTEM_PRINCIPAL: SystemPrincipal = SystemPrincipal()
"""Module-level singleton — import this; do not instantiate ``SystemPrincipal()``
elsewhere. Singleton identity is asserted by the CP4 test suite."""


__all__ = [
    "SYSTEM_PRINCIPAL",
    "SystemPrincipal",
]
