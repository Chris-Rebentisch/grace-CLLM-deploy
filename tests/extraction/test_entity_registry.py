"""Tests for entity resolution batch cache."""

from src.extraction.entity_registry import EntityRegistry


class _FakeResult:
    """Minimal stand-in for EntityResolutionResult."""

    def __init__(self, tier="exact"):
        self.resolution_tier = tier


def test_cache_key_normalizes_name():
    """cache_key matches normalize_entity_name + type."""
    reg = EntityRegistry()
    # "Acme LLC" normalizes to "acme" (LLC stripped), same as "acme llc"
    key1 = reg.cache_key("Acme LLC", "Legal_Entity")
    key2 = reg.cache_key("acme llc", "Legal_Entity")
    assert key1 == key2


def test_cache_key_deterministic():
    """cache_key is deterministic for same name+type."""
    reg = EntityRegistry()
    key1 = reg.cache_key("GlobalTech", "Legal_Entity")
    key2 = reg.cache_key("GlobalTech", "Legal_Entity")
    assert key1 == key2


def test_get_returns_none_on_miss():
    """get returns None when no cached result exists."""
    reg = EntityRegistry()
    assert reg.get("NoSuchEntity", "Person") is None


def test_put_then_get():
    """put then get returns the stored result."""
    reg = EntityRegistry()
    result = _FakeResult(tier="exact")
    reg.put("Acme Corp", "Legal_Entity", result)
    cached = reg.get("Acme Corp", "Legal_Entity")
    assert cached is result
    assert cached.resolution_tier == "exact"


def test_embedding_cache():
    """put_embedding then get_embedding returns the vector."""
    reg = EntityRegistry()
    vec = [0.1, 0.2, 0.3]
    reg.put_embedding("acme (Legal_Entity)", vec)
    cached = reg.get_embedding("acme (Legal_Entity)")
    assert cached == vec
    assert reg.get_embedding("nonexistent") is None


def test_clear_empties_both_caches():
    """clear empties both resolution and embedding caches."""
    reg = EntityRegistry()
    reg.put("Acme", "Legal_Entity", _FakeResult())
    reg.put_embedding("text", [1.0])
    reg.clear()
    assert reg.get("Acme", "Legal_Entity") is None
    assert reg.get_embedding("text") is None


def test_stats_reports_hits_misses():
    """stats reports correct hit/miss counts."""
    reg = EntityRegistry()
    result = _FakeResult()
    reg.put("Acme", "Legal_Entity", result)

    reg.get("Acme", "Legal_Entity")  # hit
    reg.get("NotHere", "Person")  # miss

    s = reg.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["cache_size"] == 1
