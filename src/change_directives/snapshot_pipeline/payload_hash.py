"""Canonical-JSON SHA-256 for criterion query rows (Chunk 39, D302)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_payload_hash(query_result_rows: list[dict[str, Any]]) -> str:
    """SHA-256 over canonical JSON of full query result rows."""
    blob = json.dumps(query_result_rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
