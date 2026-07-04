"""Temporal hint parsing for extraction claims.

Parses temporal_hints dicts from extraction into (valid_from, valid_to)
datetime pairs. Supports standard dates, ISO dates, quarters (Q1-Q4),
halves (H1/H2), and fiscal years (FY).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import structlog
from dateutil import parser as dateutil_parser

log = structlog.get_logger()

# Quarter start months: Q1=Jan, Q2=Apr, Q3=Jul, Q4=Oct
_QUARTER_MAP = {"1": "January", "2": "April", "3": "July", "4": "October"}
# Half start months: H1=Jan, H2=Jul
_HALF_MAP = {"1": "January", "2": "July"}

_RE_QUARTER = re.compile(r"\bQ([1-4])\s*(\d{4})\b", re.IGNORECASE)
_RE_HALF = re.compile(r"\bH([12])\s*(\d{4})\b", re.IGNORECASE)
_RE_FY = re.compile(r"\bFY\s*(\d{4})\b", re.IGNORECASE)


def normalize_temporal_hint(hint: str) -> str:
    """Regex preprocessing for common financial notation.

    Converts Q1-Q4, H1/H2, FY patterns to month+year strings
    that dateutil can parse.
    """
    # Q3 2025 -> July 2025
    result = _RE_QUARTER.sub(
        lambda m: f"{_QUARTER_MAP[m.group(1)]} {m.group(2)}", hint
    )
    # H2 2024 -> July 2024
    result = _RE_HALF.sub(
        lambda m: f"{_HALF_MAP[m.group(1)]} {m.group(2)}", result
    )
    # FY2024 -> January 2024
    result = _RE_FY.sub(lambda m: f"January {m.group(1)}", result)
    return result


def parse_temporal_hint(hint: str) -> datetime | None:
    """Normalize and parse a temporal hint string into a UTC datetime.

    Returns None on failure (logs INFO, does not raise).
    """
    normalized = normalize_temporal_hint(hint)
    try:
        # Use default with day=1 so "January 2024" → Jan 1, not today's day
        default_dt = datetime(2000, 1, 1)
        dt = dateutil_parser.parse(normalized, fuzzy=True, default=default_dt)
        # Make timezone-aware UTC if naive
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, OverflowError):
        log.info("temporal_tagger.unparseable", hint=hint, normalized=normalized)
        return None


def tag_temporal(
    temporal_hints: dict[str, str] | None,
) -> tuple[datetime | None, datetime | None]:
    """Convert temporal_hints dict to (valid_from, valid_to) tuple.

    Expected keys: 'date', 'start', 'end', 'since'.
    """
    if not temporal_hints:
        return (None, None)

    valid_from: datetime | None = None
    valid_to: datetime | None = None

    if "date" in temporal_hints:
        valid_from = parse_temporal_hint(temporal_hints["date"])
        return (valid_from, None)

    if "start" in temporal_hints:
        valid_from = parse_temporal_hint(temporal_hints["start"])
    if "end" in temporal_hints:
        valid_to = parse_temporal_hint(temporal_hints["end"])

    if "since" in temporal_hints and valid_from is None:
        valid_from = parse_temporal_hint(temporal_hints["since"])

    return (valid_from, valid_to)
