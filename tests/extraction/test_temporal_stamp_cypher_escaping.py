"""Cypher-escaping tests for the D511 temporal-stamp builder.

The extraction bridge previously interpolated grace_ids and the isoformat
timestamp into Cypher via raw f-strings. `_build_temporal_stamp_cypher`
now routes every value through `escape_cypher_string` (the established
`src/graph/entity_ops.py` pattern) so quotes/backslashes cannot break out
of the string literal.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.extraction.extraction_bridge import _build_temporal_stamp_cypher


def test_plain_ids_produce_expected_cypher() -> None:
    ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    cypher = _build_temporal_stamp_cypher(["id-1", "id-2"], ts)
    assert "v.grace_id IN ['id-1', 'id-2']" in cypher
    assert f"SET v.valid_from = '{ts.isoformat()}'" in cypher
    assert cypher.endswith("RETURN count(v)")
    assert "v.valid_from IS NULL" in cypher


def test_single_quote_in_grace_id_is_escaped() -> None:
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    cypher = _build_temporal_stamp_cypher(["o'brien-id"], ts)
    # Escaped form must be present; raw un-escaped quote must not appear.
    assert "o\\'brien-id" in cypher
    assert "'o'brien-id'" not in cypher


def test_backslash_in_grace_id_is_escaped() -> None:
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    cypher = _build_temporal_stamp_cypher(["a\\b"], ts)
    assert "'a\\\\b'" in cypher


def test_injection_attempt_cannot_close_the_literal() -> None:
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    malicious = "x'] DETACH DELETE v //"
    cypher = _build_temporal_stamp_cypher([malicious], ts)
    # The closing quote in the payload is escaped, so the bracket stays
    # inside the string literal.
    assert "x\\'] DETACH DELETE v //" in cypher
    assert "'x']" not in cypher
