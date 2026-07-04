"""Tests for temporal hint parsing."""

from datetime import datetime, timezone

from src.extraction.temporal_tagger import parse_temporal_hint, tag_temporal


class TestParseTemporalHint:
    def test_parse_standard_date(self):
        result = parse_temporal_hint("January 2024")
        assert result == datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_parse_iso_date(self):
        result = parse_temporal_hint("2024-01-15")
        assert result == datetime(2024, 1, 15, tzinfo=timezone.utc)

    def test_parse_quarter(self):
        result = parse_temporal_hint("Q3 2025")
        assert result == datetime(2025, 7, 1, tzinfo=timezone.utc)

    def test_parse_half_year(self):
        result = parse_temporal_hint("H2 2024")
        assert result == datetime(2024, 7, 1, tzinfo=timezone.utc)

    def test_parse_fiscal_year(self):
        result = parse_temporal_hint("FY2024")
        assert result == datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_unparseable_returns_none(self):
        result = parse_temporal_hint("not a date at all xyz")
        assert result is None


class TestTagTemporal:
    def test_tag_temporal_since(self):
        valid_from, valid_to = tag_temporal({"since": "2019"})
        assert valid_from == datetime(2019, 1, 1, tzinfo=timezone.utc)
        assert valid_to is None

    def test_tag_temporal_start_end(self):
        valid_from, valid_to = tag_temporal({
            "start": "January 2024",
            "end": "March 2025",
        })
        assert valid_from == datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert valid_to == datetime(2025, 3, 1, tzinfo=timezone.utc)
