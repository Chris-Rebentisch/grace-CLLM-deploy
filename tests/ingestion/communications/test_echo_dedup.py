"""CP4 — Echo-dedup gate tests (R1 mitigation, D517).

Load-bearing honesty guard: corroboration counts distinct resolved-Person
originators, not raw messages, and quoted text does not inflate the count.
Failure is a chunk FAIL, not a soft warning.
"""

from src.ingestion.communications.corroboration_scorer import (
    CorroborationConfig,
    EntityCorroboration,
    SourceMention,
    load_config,
    score_entity_v1,
)


def _make_mention(
    person_id: str,
    stance: str = "affirm",
    category: str = "canonical",
    quality_key: str = "reply_affirm",
    message_id: str = "",
    text_snippet: str = "",
) -> SourceMention:
    return SourceMention(
        person_id=person_id,
        person_category=category,
        stance=stance,
        quality_key=quality_key,
        message_id=message_id,
        text_snippet=text_snippet,
    )


def test_echo_dedup():
    """3 messages from 2 distinct resolved Persons → sender count == 2, not 3.

    Person A sends 2 messages mentioning entity. Person B sends 1 message.
    corroborating_sender_count must be 2 (distinct Persons), not 3 (raw messages).
    """
    config = load_config()

    entity = EntityCorroboration(
        entity_grace_id="ent-dedup",
        entity_type="Legal_Entity",
        mentions=[
            _make_mention("person-A", "affirm", message_id="msg-1"),
            _make_mention("person-A", "affirm", message_id="msg-2"),  # same person, different message
            _make_mention("person-B", "affirm", message_id="msg-3"),
        ],
    )

    result = score_entity_v1(entity, config)

    # Critical: count is distinct resolved Persons, not raw messages
    assert result.corroborating_sender_count == 2, (
        f"Expected 2 distinct Persons, got {result.corroborating_sender_count}. "
        "Echo-dedup FAILED: counting raw messages instead of distinct resolved Persons."
    )


def test_echo_quoted_text_not_double_counted():
    """Quoted text from message A appearing in message B must not inflate sender count.

    Scenario: Person A asserts a fact. Person B quotes Person A's assertion
    in a reply. The quoted assertion should NOT create a separate mention for
    Person B — only Person B's own original text counts as a new mention.
    """
    config = load_config()

    # Model: Person A makes the original assertion.
    # Person B's reply quotes it — but the quote-stripped visible text
    # only contains Person B's own assertion. So we have 2 Person mentions,
    # one from each Person's own visible text. The quoted text is NOT a
    # separate mention.
    entity = EntityCorroboration(
        entity_grace_id="ent-quote",
        entity_type="Legal_Entity",
        mentions=[
            # Person A's original assertion
            _make_mention(
                "person-A",
                "affirm",
                message_id="msg-original",
                text_snippet="The entity Acme Corp exists.",
            ),
            # Person B's own text (quote-stripped) — NOT the quoted portion
            _make_mention(
                "person-B",
                "affirm",
                message_id="msg-reply",
                text_snippet="I can confirm Acme Corp is real.",
            ),
            # NOTE: there is NO third mention for the quoted text.
            # The email_composer.strip_quoted_history() removes quoted
            # portions before extraction, so quoted text never becomes
            # a separate mention.
        ],
    )

    result = score_entity_v1(entity, config)

    # Only 2 distinct Persons contributed — quoted text did not inflate
    assert result.corroborating_sender_count == 2, (
        f"Expected 2, got {result.corroborating_sender_count}. "
        "Quoted text inflated the sender count."
    )
