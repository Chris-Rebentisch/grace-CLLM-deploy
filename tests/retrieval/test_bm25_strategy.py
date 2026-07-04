"""Tests for BM25 keyword search strategy."""

from src.retrieval.bm25_strategy import BM25SearchIndex


def test_build_index_tokenizes():
    """Build index tokenizes all entities."""
    index = BM25SearchIndex()
    entities = [
        ("id-1", "Person: Alice Smith. age=30"),
        ("id-2", "Company: Acme Corp. industry=tech"),
    ]
    index.build_index(entities)
    assert index.retriever is not None
    assert len(index.grace_ids) == 2


def test_search_returns_results():
    """Search returns top-K by BM25 score."""
    index = BM25SearchIndex()
    entities = [
        ("id-1", "Person: Alice Smith. age=30"),
        ("id-2", "Company: Acme Corp. industry=tech"),
        ("id-3", "Person: Bob Jones. age=25"),
    ]
    index.build_index(entities)
    results = index.search("Alice Smith", top_k=2)

    assert len(results) >= 1
    assert results[0].strategy == "bm25"
    # Alice should score highest
    assert results[0].grace_id == "id-1"


def test_search_empty_index():
    """Search with empty index returns empty."""
    index = BM25SearchIndex()
    results = index.search("anything")
    assert results == []


def test_search_exact_keyword():
    """Search finds exact keyword match."""
    index = BM25SearchIndex()
    entities = [
        ("id-1", "Legal_Entity: Acme Capital. jurisdiction=BVI"),
        ("id-2", "Property: Cedar Cay. location=Exuma"),
    ]
    index.build_index(entities)
    results = index.search("Acme", top_k=5)

    assert len(results) >= 1
    assert results[0].grace_id == "id-1"
