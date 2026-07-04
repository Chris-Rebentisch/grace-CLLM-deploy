"""Tests for the conversational review assistant (D522 session)."""

import pytest

from src.ontology.review_assist import (
    AssistResponse,
    AssistSuggestedAction,
    AssistTurn,
    _build_user_prompt,
    run_review_assist,
)


class _FakeResponse:
    def __init__(self, parsed):
        self.parsed = parsed


class _FakeProvider:
    """Records the prompts and returns a canned structured response."""

    def __init__(self, parsed):
        self._parsed = parsed
        self.system_prompt = None
        self.user_prompt = None

    async def generate_structured(self, system_prompt, user_prompt, response_model, **kwargs):
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return _FakeResponse(self._parsed)


ELEMENT = {
    "name": "Legal_Entity",
    "display_label": "Companies & Organizations",
    "plain_description": "The businesses and trusts named in your documents.",
    "example_snippet": "Acme Capital Partners, LLC",
    "evidence_document_count": 12,
    "answerable_questions": ["Who are the parties to each agreement?"],
}


def test_user_prompt_includes_grounding():
    prompt = _build_user_prompt(
        ELEMENT,
        other_type_names=["Person", "Agreement"],
        history=[AssistTurn(role="user", content="hi")],
        message="What is this?",
    )
    assert "Companies & Organizations" in prompt
    assert "Acme Capital Partners, LLC" in prompt
    assert "Who are the parties to each agreement?" in prompt
    assert "Person" in prompt and "Agreement" in prompt
    assert "What is this?" in prompt


@pytest.mark.asyncio
async def test_run_assist_returns_reply_and_action():
    parsed = AssistResponse(
        reply="These are the companies named in your files.",
        suggested_action=AssistSuggestedAction(
            action="rename", button_label="Rename to 'Clients'", new_name="Clients"
        ),
    )
    provider = _FakeProvider(parsed)
    result = await run_review_assist(ELEMENT, ["Person"], [], "I call these clients", provider=provider)
    assert result.reply.startswith("These are the companies")
    assert result.suggested_action.action == "rename"
    assert result.suggested_action.new_name == "Clients"


@pytest.mark.asyncio
async def test_out_of_vocab_action_normalized_to_none():
    parsed = AssistResponse(
        reply="ok",
        suggested_action=AssistSuggestedAction(action="frobnicate"),
    )
    result = await run_review_assist(ELEMENT, [], [], "do something weird", provider=_FakeProvider(parsed))
    assert result.suggested_action is None
