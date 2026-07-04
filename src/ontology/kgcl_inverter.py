"""KGCL inverse command generator (Chunk 50, D399).

Self-contained module that maps three revertible additive KGCL commands to
their inverses.  Returns ``None`` for ``add synonym`` and all other
non-revertible operations.

Does NOT import or depend on ``kgcl_parser.py``.
"""

from __future__ import annotations

import re

# Three revertible patterns per D399.
_CREATE_CLASS_RE = re.compile(r"^create\s+class\s+'([^']+)'$", re.IGNORECASE)
_CREATE_REL_RE = re.compile(r"^create\s+relationship\s+'([^']+)'$", re.IGNORECASE)
_ADD_PROP_RE = re.compile(
    r"^add\s+property\s+'([^']+)'\s+to\s+class\s+'([^']+)'$", re.IGNORECASE
)


def invert(kgcl_command: str) -> str | None:
    """Return the inverse KGCL command, or ``None`` if not revertible.

    Revertible mappings (3 commands):
      - ``create class '<X>'``           -> ``obsolete class '<X>'``
      - ``create relationship '<X>'``    -> ``obsolete relationship '<X>'``
      - ``add property '<P>' to class '<E>'`` -> ``remove property '<P>' from class '<E>'``

    ``add synonym`` and all other operations return ``None``.
    """
    cmd = kgcl_command.strip()

    m = _CREATE_CLASS_RE.match(cmd)
    if m:
        return f"obsolete class '{m.group(1)}'"

    m = _CREATE_REL_RE.match(cmd)
    if m:
        return f"obsolete relationship '{m.group(1)}'"

    m = _ADD_PROP_RE.match(cmd)
    if m:
        return f"remove property '{m.group(1)}' from class '{m.group(2)}'"

    return None
