"""Tests for entity-to-text conversion."""

from src.retrieval.text_representation import build_text_corpus, entity_to_text


def test_entity_to_text_all_properties():
    """entity_to_text includes entity type, name, and well-known + key properties."""
    props = {"name": "Acme Capital", "jurisdiction": "BVI", "registered_date": "2015"}
    result = entity_to_text("Legal_Entity", props)
    assert result.startswith("Legal_Entity: Acme Capital.")
    assert "jurisdiction: BVI" in result
    assert "registered_date=2015" in result


def test_entity_to_text_minimal():
    """entity_to_text with name only produces clean output."""
    props = {"name": "Alice"}
    result = entity_to_text("Person", props)
    assert result == "Person: Alice."


def test_entity_to_text_none_values_skipped():
    """entity_to_text skips None values in key properties."""
    props = {"name": "Bob", "age": 30, "email": None}
    result = entity_to_text("Person", props)
    assert "age=30" in result
    assert "email" not in result


def test_entity_to_text_no_name():
    """entity_to_text uses 'unknown' when name missing."""
    result = entity_to_text("Entity", {})
    assert "unknown" in result
    assert result == "Entity: unknown."


def test_build_text_corpus():
    """build_text_corpus produces correct (grace_id, text) pairs."""
    entities = [
        {"grace_id": "id-1", "@type": "Person", "name": "Alice", "age": 30},
        {"grace_id": "id-2", "@type": "Company", "name": "Acme"},
    ]
    corpus = build_text_corpus(entities)
    assert len(corpus) == 2
    assert corpus[0][0] == "id-1"
    assert "Person" in corpus[0][1]
    assert "Alice" in corpus[0][1]
    assert corpus[1][0] == "id-2"
    assert "Company" in corpus[1][1]


def test_entity_to_text_with_description():
    """entity_to_text includes description in the description fragment."""
    props = {"name": "Acme Corp", "description": "A multinational conglomerate"}
    result = entity_to_text("Legal_Entity", props)
    assert "A multinational conglomerate" in result
    assert result.startswith("Legal_Entity: Acme Corp.")


def test_entity_to_text_with_jurisdiction_and_status():
    """Well-known properties appear in description fragment."""
    props = {"name": "Acme", "jurisdiction": "BVI", "status": "active"}
    result = entity_to_text("Legal_Entity", props)
    assert "jurisdiction: BVI" in result
    assert "status: active" in result
    # These should be in the description fragment, not key properties
    assert "Key properties" not in result


def test_entity_to_text_with_type_property():
    """type property appears in description fragment, not duplicated in key properties."""
    props = {"name": "Widget", "type": "subsidiary", "revenue": 1000}
    result = entity_to_text("Legal_Entity", props)
    assert "type: subsidiary" in result
    assert "revenue=1000" in result
    # type should not appear in Key properties section
    key_props_section = result.split("Key properties: ")[-1] if "Key properties: " in result else ""
    assert "type=" not in key_props_section


def test_entity_to_text_system_fields_excluded():
    """System fields (@rid, @type, @cat) are excluded from output."""
    props = {
        "name": "Test",
        "@rid": "#1:0",
        "@type": "Entity",
        "@cat": "v",
        "real_prop": "value",
    }
    result = entity_to_text("Entity", props)
    assert "@rid" not in result
    assert "@cat" not in result
    assert "real_prop=value" in result


def test_entity_to_text_empty_properties():
    """entity_to_text with empty dict produces minimal output."""
    result = entity_to_text("Entity", {})
    assert result == "Entity: unknown."
