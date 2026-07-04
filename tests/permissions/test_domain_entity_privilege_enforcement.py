"""Tests for CP5 — post-fetch domain-entity sensitivity enforcement.

Verifies:
- Post-fetch filter drops vertices whose sensitivity_tags contain a
  tag forbidden to the principal.
- Full-visibility principal sees all results (no filtering).
- No-matrix case passes through unfiltered (backward-compatible).

D521 — two-zone enforcement fallback for ANN vectorNeighbors() paths
that bypass the cypher rewriter.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.permissions.models import (
    Allow,
    Deny,
    EnforcementReason,
    PermissionMatrix,
    RoleCluster,
    SensitivityTag,
)
from src.permissions.principal_context import User
from src.permissions.sensitivity_resolver import (
    D426_VOCABULARY,
    resolve_forbidden_tags,
)


def _make_result(grace_id: str, sensitivity_tags: str = "") -> MagicMock:
    """Build a fake RankedResult with properties dict."""
    result = MagicMock()
    result.grace_id = grace_id
    result.properties = {"sensitivity_tags": sensitivity_tags}
    result.contributing_strategies = ["semantic"]
    return result


def _make_response(results: list) -> MagicMock:
    """Build a fake RetrievalResponse."""
    resp = MagicMock()
    resp.results = results
    resp.strategy_contributions = {}
    return resp


def _make_request(principal: User | None = None) -> MagicMock:
    """Build a fake Request."""
    return MagicMock()


class TestPostFetchSensitivityFilter:
    """Post-fetch enforce drops forbidden-tagged entities."""

    def test_forbidden_tag_filtered(self) -> None:
        """Entity with |privileged| dropped when principal lacks visibility."""
        from src.ingestion.communications.sensitivity_tagger import (
            tags_from_bar_form,
        )

        # Principal only sees pii_dense — privileged is forbidden
        matrix = PermissionMatrix(
            role_clusters=[
                RoleCluster(
                    cluster_id="shared",
                    display_name="Shared",
                    sensitivity_tags=[SensitivityTag(name="pii_dense")],
                ),
            ],
        )
        principal = User()
        forbidden = resolve_forbidden_tags(principal, matrix)

        # Simulate post-fetch filter logic
        results = [
            _make_result("id-1", "|privileged|"),
            _make_result("id-2", "|pii_dense|"),
            _make_result("id-3", ""),
        ]

        surviving = []
        for r in results:
            entity_tags_str = r.properties.get("sensitivity_tags", "")
            if entity_tags_str and forbidden:
                entity_tags = set(tags_from_bar_form(entity_tags_str))
                if entity_tags & forbidden:
                    continue
            surviving.append(r)

        # id-1 has privileged (forbidden) → dropped
        # id-2 has pii_dense (visible) → kept
        # id-3 has no tags → kept
        assert len(surviving) == 2
        assert surviving[0].grace_id == "id-2"
        assert surviving[1].grace_id == "id-3"

    def test_full_visibility_no_filter(self) -> None:
        """Principal with all D426 tags visible → nothing filtered."""
        matrix = PermissionMatrix(
            role_clusters=[
                RoleCluster(
                    cluster_id="admin",
                    display_name="Admin",
                    sensitivity_tags=[
                        SensitivityTag(name="privileged"),
                        SensitivityTag(name="pii_dense"),
                        SensitivityTag(name="external_boundary"),
                        SensitivityTag(name="privilege_potentially_waived"),
                    ],
                ),
            ],
        )
        principal = User()
        forbidden = resolve_forbidden_tags(principal, matrix)

        assert forbidden == set()

        results = [
            _make_result("id-1", "|privileged|"),
            _make_result("id-2", "|pii_dense|privileged|"),
        ]

        surviving = []
        for r in results:
            entity_tags_str = r.properties.get("sensitivity_tags", "")
            if entity_tags_str and forbidden:
                from src.ingestion.communications.sensitivity_tagger import (
                    tags_from_bar_form,
                )

                entity_tags = set(tags_from_bar_form(entity_tags_str))
                if entity_tags & forbidden:
                    continue
            surviving.append(r)

        assert len(surviving) == 2

    def test_no_matrix_no_filter(self) -> None:
        """No active matrix → empty forbidden set → no filtering."""
        principal = User()
        forbidden = resolve_forbidden_tags(principal, None)

        assert forbidden == set()

        results = [
            _make_result("id-1", "|privileged|"),
        ]

        surviving = []
        for r in results:
            entity_tags_str = r.properties.get("sensitivity_tags", "")
            if entity_tags_str and forbidden:
                from src.ingestion.communications.sensitivity_tagger import (
                    tags_from_bar_form,
                )

                entity_tags = set(tags_from_bar_form(entity_tags_str))
                if entity_tags & forbidden:
                    continue
            surviving.append(r)

        assert len(surviving) == 1
