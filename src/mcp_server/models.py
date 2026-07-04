"""Pydantic input bodies for tools that call POST routes.

The official MCP SDK generates each tool's input JSON Schema from the
tool function's signature annotations. This module holds the upstream
request-body shapes the tools construct to call FastAPI — mirroring
``src/retrieval/retrieval_models.py::RetrievalQuery`` and
``src/regeneration/regeneration_models.py::RegenerationQuery`` for
contract safety against silent dict drift.

Only fields the MCP adapter sets explicitly appear here. Upstream
defaults (``seed_entity_ids``, ``entity_types``, ``overrides``) are
populated by the FastAPI route when the field is omitted from the
request body.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PhaseStateLiteral = Literal[
    "prepare", "open", "structure", "clarify", "close", "none"
]


class RetrievalRequestBody(BaseModel):
    """POST body for ``/api/retrieval/query`` built by ``grace_search``."""

    query_text: str = Field(description="Natural language query")
    top_k: int = Field(default=10, description="Final number of results")


class RegenerationRequestBody(BaseModel):
    """POST body for ``/api/regeneration/query`` built by ``grace_answer``."""

    query_text: str = Field(description="Natural language query")
    phase_state: PhaseStateLiteral = Field(
        default="none",
        description=(
            "Elicitation phase name; shapes response style. One of "
            "prepare, open, structure, clarify, close, none."
        ),
    )
