"""Unit tests for `src.graph.graph_read_ops` (Chunk 28 D212 cursor helpers)."""

from __future__ import annotations

import pytest

from src.graph.graph_read_ops import (
    _compute_filter_fingerprint,
    _compute_relationship_fingerprint,
    _decode_cursor,
    _encode_cursor,
)


def test_cursor_encode_decode_round_trip():
    fp = _compute_filter_fingerprint("Legal_Entity", "legal_entity")
    cursor = _encode_cursor("25", fp)
    after_rid, fingerprint = _decode_cursor(cursor)
    assert after_rid == "25"
    assert fingerprint == fp


def test_filter_fingerprint_stable_for_same_inputs():
    a = _compute_filter_fingerprint("Legal_Entity", "legal_entity")
    b = _compute_filter_fingerprint("Legal_Entity", "legal_entity")
    assert a == b
    # Different inputs produce different fingerprints
    c = _compute_filter_fingerprint("Legal_Entity", None)
    assert c != a
    # Relationship fingerprints are independent of entity ones
    d = _compute_relationship_fingerprint("owns")
    assert d != a


def test_decode_cursor_malformed_raises():
    with pytest.raises(ValueError):
        _decode_cursor("not-base64-at-all!@#$")

    # Valid base64 but not JSON
    with pytest.raises(ValueError):
        _decode_cursor("bm90LWpzb24=")  # "not-json"

    # Valid JSON but missing required fields
    import base64

    missing = base64.urlsafe_b64encode(b'{"after_rid": "1"}').decode("ascii")
    with pytest.raises(ValueError):
        _decode_cursor(missing)

    # Wrong types
    bad_types = base64.urlsafe_b64encode(
        b'{"after_rid": 1, "filter_fingerprint": "abc"}'
    ).decode("ascii")
    with pytest.raises(ValueError):
        _decode_cursor(bad_types)
