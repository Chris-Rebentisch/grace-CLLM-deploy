"""Hand-rolled recursive-descent KGCL parser (Chunk 48, D391).

Recognizes 14 KGCL command kinds (D390) and returns structured
``ProposedSchemaChange`` Pydantic models. No external parser library.

Public API: ``parse_kgcl(command: str) -> ProposedSchemaChange``.
"""

from __future__ import annotations

from collections.abc import Callable

from src.ontology.kgcl_models import KGCLCommandKind, KGCLParseError, ProposedSchemaChange


# ---------------------------------------------------------------------------
# Tokenizer — splits on whitespace, preserves single-quoted strings as atomic
# ---------------------------------------------------------------------------

def _tokenize(command: str) -> list[str]:
    """Split *command* into tokens, treating single-quoted strings as atomic."""
    tokens: list[str] = []
    i = 0
    n = len(command)
    while i < n:
        # Skip whitespace.
        if command[i].isspace():
            i += 1
            continue
        # Single-quoted string.
        if command[i] == "'":
            j = command.find("'", i + 1)
            if j == -1:
                raise KGCLParseError(
                    "Unterminated single-quoted string",
                    token=command[i:],
                    offset=i,
                )
            # Strip surrounding quotes from the token value.
            tokens.append(command[i + 1 : j])
            i = j + 1
            continue
        # Bare word.
        j = i
        while j < n and not command[j].isspace() and command[j] != "'":
            j += 1
        tokens.append(command[i:j])
        i = j
    return tokens


# ---------------------------------------------------------------------------
# Token-stream helpers
# ---------------------------------------------------------------------------

class _Stream:
    """Minimal token stream with peek / expect helpers."""

    def __init__(self, tokens: list[str], raw: str) -> None:
        self.tokens = tokens
        self.pos = 0
        self.raw = raw

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self) -> str:
        if self.pos >= len(self.tokens):
            raise KGCLParseError(
                "Unexpected end of command",
                offset=len(self.raw),
            )
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, value: str) -> str:
        tok = self.advance()
        if tok.lower() != value.lower():
            raise KGCLParseError(
                f"Expected '{value}', got '{tok}'",
                token=tok,
                offset=self.pos - 1,
            )
        return tok

    @property
    def remaining(self) -> int:
        return len(self.tokens) - self.pos


# ---------------------------------------------------------------------------
# Per-command parse functions
# ---------------------------------------------------------------------------

def _parse_create_class(s: _Stream) -> ProposedSchemaChange:
    # create class '<Name>'
    name = s.advance()
    return ProposedSchemaChange(command_kind=KGCLCommandKind.CREATE_CLASS, target_name=name)


def _parse_obsolete_class(s: _Stream) -> ProposedSchemaChange:
    # obsolete class '<Name>'
    name = s.advance()
    return ProposedSchemaChange(command_kind=KGCLCommandKind.OBSOLETE_CLASS, target_name=name)


def _parse_change_description(s: _Stream) -> ProposedSchemaChange:
    # change description of '<Name>'
    s.expect("of")
    name = s.advance()
    return ProposedSchemaChange(command_kind=KGCLCommandKind.CHANGE_DESCRIPTION, target_name=name)


def _parse_create_relationship(s: _Stream) -> ProposedSchemaChange:
    # create relationship '<Name>'
    name = s.advance()
    return ProposedSchemaChange(command_kind=KGCLCommandKind.CREATE_RELATIONSHIP, target_name=name)


def _parse_obsolete_relationship(s: _Stream) -> ProposedSchemaChange:
    # obsolete relationship '<Name>'
    name = s.advance()
    return ProposedSchemaChange(command_kind=KGCLCommandKind.OBSOLETE_RELATIONSHIP, target_name=name)


def _parse_change_relationship(s: _Stream) -> ProposedSchemaChange:
    # change relationship '<Name>'
    name = s.advance()
    return ProposedSchemaChange(command_kind=KGCLCommandKind.CHANGE_RELATIONSHIP, target_name=name)


def _parse_add_property(s: _Stream) -> ProposedSchemaChange:
    # add property '<prop>' to class '<entity>'
    prop_name = s.advance()
    s.expect("to")
    s.expect("class")
    entity = s.advance()
    return ProposedSchemaChange(
        command_kind=KGCLCommandKind.ADD_PROPERTY,
        target_name=prop_name,
        property_name=prop_name,
        entity_name=entity,
    )


def _parse_remove_property(s: _Stream) -> ProposedSchemaChange:
    # remove property '<prop>' from class '<entity>'
    prop_name = s.advance()
    s.expect("from")
    s.expect("class")
    entity = s.advance()
    return ProposedSchemaChange(
        command_kind=KGCLCommandKind.REMOVE_PROPERTY,
        target_name=prop_name,
        property_name=prop_name,
        entity_name=entity,
    )


def _parse_change_property(s: _Stream) -> ProposedSchemaChange:
    # change property '<prop>' on class '<entity>'
    prop_name = s.advance()
    s.expect("on")
    s.expect("class")
    entity = s.advance()
    return ProposedSchemaChange(
        command_kind=KGCLCommandKind.CHANGE_PROPERTY,
        target_name=prop_name,
        property_name=prop_name,
        entity_name=entity,
    )


def _parse_add_synonym(
    s: _Stream,
    schema_lookup_fn: Callable[[str], list[str]] | None = None,
) -> ProposedSchemaChange:
    # add synonym '<synonym>' for class '<entity>'          — long form
    # add synonym '<synonym>' for '<entity>'                — short form (D461)
    synonym = s.advance()
    s.expect("for")
    nxt = s.peek()
    if nxt is not None and nxt.lower() == "class":
        # Long form — consume 'class' keyword and entity name.
        s.advance()  # consume 'class'
        entity = s.advance()
    elif schema_lookup_fn is not None:
        # Short form — next token is entity name; disambiguate via lookup.
        entity = s.advance()
        matches = schema_lookup_fn(entity)
        if len(matches) == 0:
            raise KGCLParseError(
                f"Entity '{entity}' not found in active schema",
                error_kind="ENTITY_NOT_FOUND",
            )
        if len(matches) > 1:
            raise KGCLParseError(
                f"Entity '{entity}' is ambiguous — matches: {matches}",
                error_kind="AMBIGUOUS",
                candidates=matches,
            )
        entity = matches[0]
    else:
        # No schema_lookup_fn — require long form (existing behavior).
        s.expect("class")
        entity = s.advance()
    return ProposedSchemaChange(
        command_kind=KGCLCommandKind.ADD_SYNONYM,
        target_name=entity,
        synonym=synonym,
    )


def _parse_rename_property(
    s: _Stream,
    schema_lookup_fn: Callable[[str], list[str]] | None = None,
) -> ProposedSchemaChange:
    # rename property '<old>' to '<new>' on class '<entity>'   — long form
    # rename property '<old>' to '<new>'                       — short form (D461)
    old_name = s.advance()
    s.expect("to")
    new_name = s.advance()
    nxt = s.peek()
    if nxt is not None and nxt.lower() == "on":
        # Long form — consume 'on class' and entity name.
        s.advance()  # consume 'on'
        s.expect("class")
        entity = s.advance()
    elif schema_lookup_fn is not None:
        # Short form — disambiguate property owner via lookup.
        matches = schema_lookup_fn(old_name)
        if len(matches) == 0:
            raise KGCLParseError(
                f"Property '{old_name}' not found in active schema",
                error_kind="ENTITY_NOT_FOUND",
            )
        if len(matches) > 1:
            raise KGCLParseError(
                f"Property '{old_name}' is ambiguous — found on classes: {matches}",
                error_kind="AMBIGUOUS",
                candidates=matches,
            )
        entity = matches[0]
    else:
        # No schema_lookup_fn — require long form (existing behavior).
        s.expect("on")
        s.expect("class")
        entity = s.advance()
    return ProposedSchemaChange(
        command_kind=KGCLCommandKind.RENAME_PROPERTY,
        target_name=old_name,
        property_name=old_name,
        new_name=new_name,
        entity_name=entity,
    )


def _parse_split_class(s: _Stream) -> ProposedSchemaChange:
    # split class '<Name>' into '<A>' '<B>'
    name = s.advance()
    s.expect("into")
    targets: list[str] = []
    while s.remaining > 0:
        targets.append(s.advance())
    if len(targets) < 2:
        raise KGCLParseError(
            "split class requires at least two target names after 'into'",
            offset=s.pos,
        )
    return ProposedSchemaChange(
        command_kind=KGCLCommandKind.SPLIT_CLASS,
        target_name=name,
        split_into=targets,
    )


def _parse_move_class(s: _Stream) -> ProposedSchemaChange:
    # move class '<Name>' from '<old_parent>' to '<new_parent>'
    name = s.advance()
    s.expect("from")
    old_parent = s.advance()
    s.expect("to")
    new_parent = s.advance()
    return ProposedSchemaChange(
        command_kind=KGCLCommandKind.MOVE_CLASS,
        target_name=name,
        old_parent=old_parent,
        new_parent=new_parent,
    )


def _parse_change_domain_or_range(s: _Stream, change_target: str) -> ProposedSchemaChange:
    # change domain of '<rel>' to '<type>'
    # change range of '<rel>' to '<type>'
    s.expect("of")
    rel_name = s.advance()
    s.expect("to")
    to_type = s.advance()
    return ProposedSchemaChange(
        command_kind=KGCLCommandKind.CHANGE_DOMAIN_RANGE,
        target_name=rel_name,
        to_type=to_type,
        change_target=change_target,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def parse_kgcl(
    command: str,
    schema_lookup_fn: Callable[[str], list[str]] | None = None,
) -> ProposedSchemaChange:
    """Parse a KGCL command string into a ``ProposedSchemaChange``.

    Args:
        command: KGCL command text.
        schema_lookup_fn: Optional schema-aware lookup (D461). When provided,
            ``rename property`` and ``add synonym`` accept short forms without
            the ``on class`` / ``class`` qualifier. The callable receives a
            property name (for rename) or entity name (for add-synonym) and
            returns a list of owning class names. Zero matches raise
            ``ENTITY_NOT_FOUND``; multiple raise ``AMBIGUOUS`` with candidates.

    Raises ``KGCLParseError`` on unrecognized or malformed commands.
    """
    stripped = command.strip()
    if not stripped:
        raise KGCLParseError("Empty command", offset=0)

    tokens = _tokenize(stripped)
    s = _Stream(tokens, stripped)

    first = s.advance().lower()

    # --- Two-token dispatch ---
    if first == "create":
        second = s.advance().lower()
        if second == "class":
            return _parse_create_class(s)
        if second == "relationship":
            return _parse_create_relationship(s)
        raise KGCLParseError(
            f"Unknown create target '{second}'; expected 'class' or 'relationship'",
            token=second,
            offset=1,
        )

    if first == "obsolete":
        second = s.advance().lower()
        if second == "class":
            return _parse_obsolete_class(s)
        if second == "relationship":
            return _parse_obsolete_relationship(s)
        raise KGCLParseError(
            f"Unknown obsolete target '{second}'; expected 'class' or 'relationship'",
            token=second,
            offset=1,
        )

    if first == "change":
        second = s.advance().lower()
        if second == "description":
            return _parse_change_description(s)
        if second == "relationship":
            return _parse_change_relationship(s)
        if second == "property":
            return _parse_change_property(s)
        if second in ("domain", "range"):
            return _parse_change_domain_or_range(s, change_target=second)
        raise KGCLParseError(
            f"Unknown change target '{second}'; expected 'description', 'relationship', 'property', 'domain', or 'range'",
            token=second,
            offset=1,
        )

    if first == "add":
        second = s.advance().lower()
        if second == "property":
            return _parse_add_property(s)
        if second == "synonym":
            return _parse_add_synonym(s, schema_lookup_fn=schema_lookup_fn)
        raise KGCLParseError(
            f"Unknown add target '{second}'; expected 'property' or 'synonym'",
            token=second,
            offset=1,
        )

    if first == "remove":
        second = s.advance().lower()
        if second == "property":
            return _parse_remove_property(s)
        raise KGCLParseError(
            f"Unknown remove target '{second}'; expected 'property'",
            token=second,
            offset=1,
        )

    if first == "rename":
        second = s.advance().lower()
        if second == "property":
            return _parse_rename_property(s, schema_lookup_fn=schema_lookup_fn)
        raise KGCLParseError(
            f"Unknown rename target '{second}'; expected 'property'",
            token=second,
            offset=1,
        )

    if first == "split":
        s.expect("class")
        return _parse_split_class(s)

    if first == "move":
        s.expect("class")
        return _parse_move_class(s)

    # --- Special case: merge types (D390 — not supported in v1) ---
    if first == "merge":
        raise KGCLParseError(
            "merge types is not supported in v1 (D390)",
            token=first,
            offset=0,
        )

    raise KGCLParseError(
        f"Unknown command '{first}'",
        token=first,
        offset=0,
    )
