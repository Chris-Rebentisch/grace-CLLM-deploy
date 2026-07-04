"""KGCL Change Language Pydantic models (Chunk 48, D390/D391).

Defines the 14 KGCL command kinds recognized by the v1 parser and the
``ProposedSchemaChange`` discriminated-union model that carries the
parsed command payload.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KGCLCommandKind(str, Enum):
    """The 14 KGCL command kinds recognized by the v1 parser (D390)."""

    CREATE_CLASS = "create_class"
    OBSOLETE_CLASS = "obsolete_class"
    CHANGE_DESCRIPTION = "change_description"
    CREATE_RELATIONSHIP = "create_relationship"
    OBSOLETE_RELATIONSHIP = "obsolete_relationship"
    CHANGE_RELATIONSHIP = "change_relationship"
    ADD_PROPERTY = "add_property"
    REMOVE_PROPERTY = "remove_property"
    CHANGE_PROPERTY = "change_property"
    ADD_SYNONYM = "add_synonym"
    RENAME_PROPERTY = "rename_property"
    SPLIT_CLASS = "split_class"
    MOVE_CLASS = "move_class"
    CHANGE_DOMAIN_RANGE = "change_domain_range"


class ProposedSchemaChange(BaseModel):
    """Parsed KGCL command payload.

    Discriminated union over the 14 command kinds. ``target_name`` is
    always present. Kind-specific optional fields carry additional data
    (e.g. ``property_name`` for property commands, ``to_type`` for
    domain/range changes).
    """

    model_config = ConfigDict(extra="forbid")

    command_kind: KGCLCommandKind = Field(description="Which KGCL command was parsed")
    target_name: str = Field(description="Primary entity / relationship / property target")

    # Kind-specific optional fields.
    property_name: str | None = Field(default=None, description="Property name (add/remove/change/rename property)")
    entity_name: str | None = Field(default=None, description="Owning entity class (property commands)")
    new_name: str | None = Field(default=None, description="New name (rename property)")
    old_parent: str | None = Field(default=None, description="Source parent (move class)")
    new_parent: str | None = Field(default=None, description="Destination parent (move class)")
    synonym: str | None = Field(default=None, description="Synonym string (add synonym)")
    to_type: str | None = Field(default=None, description="Target type name (change domain/range)")
    change_target: Literal["domain", "range"] | None = Field(
        default=None,
        description="Whether domain or range is being changed (CHANGE_DOMAIN_RANGE only)",
    )
    split_into: list[str] | None = Field(default=None, description="Target class names for split")


class KGCLParseError(Exception):
    """Raised when a KGCL command string cannot be parsed.

    Attributes:
        token: The token that caused the error (None for empty input).
        offset: Character offset where the error occurred.
        message: Human-readable description.
        error_kind: Structured error classification (D461). ``'AMBIGUOUS'`` when
            multiple schema entities match a short-form command, ``'ENTITY_NOT_FOUND'``
            when zero entities match. ``None`` for generic parse errors.
        candidates: Entity names that matched when ``error_kind='AMBIGUOUS'`` (D461).
    """

    def __init__(
        self,
        message: str,
        *,
        token: str | None = None,
        offset: int = 0,
        error_kind: str | None = None,
        candidates: list[str] | None = None,
    ) -> None:
        self.token = token
        self.offset = offset
        self.message = message
        self.error_kind = error_kind
        self.candidates = candidates
        super().__init__(message)
