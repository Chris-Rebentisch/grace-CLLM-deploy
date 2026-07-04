"""Conversational review assistant (D522 session).

Backs the inline "Something's off? — ask" drawer on the /review screen. A
non-technical reviewer (e.g. a CFO) can talk to the system in plain English about
one proposed type instead of decoding the nine modeling verbs. The assistant
explains the type, answers questions, and — when the user clearly wants a change —
proposes ONE concrete action mapped to the underlying decision vocabulary
(approve / rename / merge / reject). The user still confirms in the UI before any
decision is written; this module never mutates the review session.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.shared.llm_provider import get_provider

# Plain-language actions we let the assistant propose. These map onto the
# ReviewDecisionType verbs at confirmation time, but the reviewer never sees the
# raw verb. "keep" -> approved, "skip" -> rejected.
SUGGESTED_ACTIONS = ("keep", "rename", "merge", "skip", "none")


class AssistSuggestedAction(BaseModel):
    """A single concrete action the assistant proposes the reviewer confirm."""

    action: str = Field(
        description="One of: keep, rename, merge, skip, none. 'none' means no action yet."
    )
    button_label: str = Field(
        default="",
        description="Short plain-English label for the confirm button, e.g. \"Rename to 'Clients'\".",
    )
    rationale: str = Field(
        default="",
        description="One plain sentence explaining what this action does and why.",
    )
    new_name: str | None = Field(
        default=None,
        description="For 'rename': the new business-friendly name the user wants.",
    )
    merge_with: str | None = Field(
        default=None,
        description="For 'merge': the technical name of the other type to merge into.",
    )


class AssistResponse(BaseModel):
    """The assistant's reply plus an optional proposed action."""

    reply: str = Field(
        description="Plain-English answer to the reviewer. No graph/ontology jargon."
    )
    suggested_action: AssistSuggestedAction | None = Field(
        default=None,
        description="A concrete action to offer, or null when just explaining.",
    )


class AssistTurn(BaseModel):
    """One prior message in the drawer conversation."""

    role: str  # "user" | "assistant"
    content: str


_SYSTEM_PROMPT = """You are a friendly guide helping a busy, non-technical business owner (think: a CFO) \
review a proposed list of the kinds of things their knowledge system will track. They do NOT know what \
an ontology, entity type, or knowledge graph is, and you must never use those words.

You are talking about ONE proposed "kind of thing" at a time. Your job:
- Explain, in plain business language, what this kind of thing is and why it was suggested.
- Answer their questions simply and briefly (2-4 sentences). Use their words, not technical terms.
- If they clearly want a change, propose exactly ONE concrete action and stop. Otherwise propose none.

The only actions you may propose (use the plain "action" code):
- "keep": track this as-is. Use when they're satisfied or you're confirming it's useful.
- "rename": they call it something else. Put their preferred name in new_name.
- "merge": this is really the same as another item on their list. Put the other item's technical
  name (from the list provided) in merge_with.
- "skip": they don't want to track this at all.
- "none": no action yet — you're still explaining or asking a clarifying question.

Never invent facts about their documents. If you don't know, say so. Keep replies short and calm."""


def _build_user_prompt(
    element: dict,
    other_type_names: list[str],
    history: list[AssistTurn],
    message: str,
) -> str:
    label = element.get("display_label") or element.get("name", "this item")
    parts = [
        f'The item under review is "{label}" (internal name: {element.get("name", "")}).',
    ]
    if element.get("plain_description"):
        parts.append(f"Plain description: {element['plain_description']}")
    elif element.get("description"):
        parts.append(f"Description: {element['description']}")
    if element.get("example_snippet"):
        parts.append(f'Example found in their documents: "{element["example_snippet"]}"')
    questions = element.get("answerable_questions") or []
    if questions:
        joined = "; ".join(questions)
        parts.append(f"It helps answer questions they care about: {joined}")
    count = element.get("evidence_document_count") or 0
    if count:
        parts.append(f"It appeared in about {count} of their documents.")
    if other_type_names:
        parts.append(
            "Other items on their list (for possible merges): "
            + ", ".join(other_type_names)
        )
    if history:
        convo = "\n".join(f"{t.role}: {t.content}" for t in history[-8:])
        parts.append("Conversation so far:\n" + convo)
    parts.append(f"\nThe reviewer just said: {message}")
    return "\n".join(parts)


async def run_review_assist(
    element: dict,
    other_type_names: list[str],
    history: list[AssistTurn],
    message: str,
    provider=None,
) -> AssistResponse:
    """Produce a plain-language reply (+ optional proposed action) for one element."""
    provider = provider or get_provider()
    user_prompt = _build_user_prompt(element, other_type_names, history, message)
    response = await provider.generate_structured(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_model=AssistResponse,
        temperature=0.2,
        max_tokens=600,
    )
    result: AssistResponse = response.parsed
    # Defensive: normalize an out-of-vocabulary action to "none".
    if result.suggested_action and result.suggested_action.action not in SUGGESTED_ACTIONS:
        result.suggested_action = None
    return result
