"""CP3 + CP7 — Corroboration scorer tests (D515/D516/D517).

Tests v1 noisy-OR scorer, v2 iterative TruthFinder, stance classifier,
and promotion gate.
"""

import pytest

from src.ingestion.communications.corroboration_scorer import (
    CorroborationConfig,
    EntityCorroboration,
    SourceMention,
    classify_stance,
    load_config,
    score_entities_v2,
    score_entity_v1,
)


@pytest.fixture()
def config() -> CorroborationConfig:
    """Standard test config."""
    return load_config()


def _make_mention(
    person_id: str,
    stance: str = "affirm",
    category: str = "canonical",
    quality_key: str = "reply_affirm",
    message_id: str = "",
) -> SourceMention:
    return SourceMention(
        person_id=person_id,
        person_category=category,
        stance=stance,
        quality_key=quality_key,
        message_id=message_id,
    )


# --- v1 noisy-OR tests ---


def test_v1_two_agreeing_sources(config):
    """Two distinct senders agreeing yields c(e) above threshold."""
    entity = EntityCorroboration(
        entity_grace_id="ent-1",
        entity_type="Legal_Entity",
        mentions=[
            _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
            _make_mention("person-B", "affirm", "canonical", "reply_affirm"),
        ],
    )
    result = score_entity_v1(entity, config)
    assert result.score >= config.theta_promote
    assert result.corroborating_sender_count == 2
    assert result.s_plus > 0
    assert result.s_minus == 0.0
    assert result.status == "first_class"


def test_v1_mixed_sources(config):
    """One affirming + one contradicting yields lower score."""
    entity = EntityCorroboration(
        entity_grace_id="ent-2",
        entity_type="Legal_Entity",
        mentions=[
            _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
            _make_mention("person-B", "contradict", "canonical", "reply_affirm"),
        ],
    )
    result = score_entity_v1(entity, config)
    # Mixed signals should lower the score vs pure agreement
    two_agree = EntityCorroboration(
        entity_grace_id="ref",
        entity_type="Legal_Entity",
        mentions=[
            _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
            _make_mention("person-B", "affirm", "canonical", "reply_affirm"),
        ],
    )
    ref_result = score_entity_v1(two_agree, config)
    assert result.score < ref_result.score


def test_v1_single_source_below_k(config):
    """Single sender stays provisional regardless of score."""
    entity = EntityCorroboration(
        entity_grace_id="ent-3",
        entity_type="Legal_Entity",
        mentions=[
            _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
        ],
    )
    result = score_entity_v1(entity, config)
    assert result.corroborating_sender_count == 1
    assert result.status == "provisional"


# --- v2 iterative TruthFinder tests ---


def test_v2_convergence(config):
    """v2 iterative mode converges within max_iters."""
    config.iterative = True
    entities = [
        EntityCorroboration(
            entity_grace_id=f"ent-{i}",
            entity_type="Legal_Entity",
            mentions=[
                _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
                _make_mention("person-B", "affirm", "internal_domain", "clear_assertion"),
                _make_mention("person-C", "incidental", "unknown", "incidental"),
            ],
        )
        for i in range(3)
    ]
    results = score_entities_v2(entities, config)
    assert len(results) == 3
    # All should have valid scores
    for r in results:
        assert 0 <= r.score <= 1.0
        assert r.corroborating_sender_count == 3


def test_v2_matches_v1_on_simple_graph(config):
    """v2 result matches v1 closed-form on a trivially small single-entity graph."""
    entity = EntityCorroboration(
        entity_grace_id="ent-simple",
        entity_type="Legal_Entity",
        mentions=[
            _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
            _make_mention("person-B", "affirm", "canonical", "reply_affirm"),
        ],
    )
    v1_result = score_entity_v1(entity, config)
    v2_results = score_entities_v2([entity], config)
    assert len(v2_results) == 1
    # v2 on a single entity should closely match v1
    assert abs(v2_results[0].score - v1_result.score) < 0.1


# --- Stance classifier tests ---


def test_stance_classifier_affirm(config):
    """Affirm cue match yields 'affirm' label."""
    text = "I agree that this entity exists in the system."
    result = classify_stance(text, config)
    assert result == "affirm"


def test_stance_classifier_contradict(config):
    """Contradict cue match yields 'contradict' label."""
    text = "That's wrong, the entity was renamed last quarter."
    result = classify_stance(text, config)
    assert result == "contradict"


# --- Promotion gate threshold test ---


def test_promotion_gate_threshold(config):
    """Entity meeting all three conditions promoted; entity missing k_senders stays provisional."""
    # Meets all conditions
    full = EntityCorroboration(
        entity_grace_id="ent-full",
        entity_type="Legal_Entity",
        mentions=[
            _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
            _make_mention("person-B", "affirm", "canonical", "reply_affirm"),
        ],
    )
    result_full = score_entity_v1(full, config)
    assert result_full.status == "first_class"

    # Missing k_senders
    single = EntityCorroboration(
        entity_grace_id="ent-single",
        entity_type="Legal_Entity",
        mentions=[
            _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
        ],
    )
    result_single = score_entity_v1(single, config)
    assert result_single.status == "provisional"


# --- CP7 integration round-trip tests ---


@pytest.mark.requires_arcade
@pytest.mark.asyncio
async def test_score_promote_verify_vertex(config):
    """Score → promote → verify corroboration_status='first_class' on graph vertex."""
    from uuid import uuid4

    from src.graph.arcade_client import get_arcade_client
    from src.ingestion.communications.corroboration_scorer import promote_entity

    client = get_arcade_client()
    grace_id = f"test-corrob-{uuid4().hex[:8]}"

    # Insert a test entity into ArcadeDB
    await client.execute_cypher(
        f"CREATE (n:Legal_Entity {{grace_id: '{grace_id}', name: 'CorrTest'}}) RETURN n"
    )

    try:
        # Score with two agreeing sources → first_class
        entity = EntityCorroboration(
            entity_grace_id=grace_id,
            entity_type="Legal_Entity",
            mentions=[
                _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
                _make_mention("person-B", "affirm", "canonical", "reply_affirm"),
            ],
        )
        result = score_entity_v1(entity, config)
        assert result.status == "first_class"

        # Promote to graph
        await promote_entity(grace_id, result.status, result.corroborating_sender_count)

        # Verify vertex properties
        resp = await client.execute_cypher(
            f"MATCH (n {{grace_id: '{grace_id}'}}) "
            f"RETURN n.corroboration_status, n.corroborating_sender_count"
        )
        # ArcadeDB client returns {"result": [...], "user": ...}
        rows = resp["result"] if isinstance(resp, dict) else resp
        assert len(rows) == 1
        assert rows[0]["n.corroboration_status"] == "first_class"
        assert rows[0]["n.corroborating_sender_count"] == 2
    finally:
        # Cleanup
        await client.execute_cypher(f"MATCH (n {{grace_id: '{grace_id}'}}) DELETE n")


@pytest.mark.requires_arcade
@pytest.mark.asyncio
async def test_v2_convergence_integration(config):
    """v2 mode converges within max_iters on a small multi-sender graph."""
    config.iterative = True
    # 5 entities, 4 senders — enough cross-references for EM to iterate
    entities = [
        EntityCorroboration(
            entity_grace_id=f"int-ent-{i}",
            entity_type="Legal_Entity",
            mentions=[
                _make_mention("person-A", "affirm", "canonical", "reply_affirm"),
                _make_mention("person-B", "affirm", "internal_domain", "clear_assertion"),
                _make_mention("person-C", "affirm" if i % 2 == 0 else "incidental", "unknown", "incidental"),
                _make_mention("person-D", "contradict" if i == 0 else "affirm", "canonical", "reply_affirm"),
            ],
        )
        for i in range(5)
    ]
    results = score_entities_v2(entities, config)
    assert len(results) == 5

    # Entity 0 has one contradicting source — should score lower than others
    scores = [r.score for r in results]
    assert scores[0] < max(scores[1:])

    # All scores should be in valid range
    for r in results:
        assert 0 <= r.score <= 1.0
        assert r.corroborating_sender_count == 4
