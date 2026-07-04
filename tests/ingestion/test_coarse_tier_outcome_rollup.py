"""Unit tests for ``coarse_tier_outcome_for_metric`` (Chunk 61, CP1, D428)."""

import pytest

from src.analytics.metrics import coarse_tier_outcome_for_metric


class TestCoarseTierOutcomeForMetric:
    """Verify fine→coarse mapping and error semantics."""

    @pytest.mark.parametrize(
        "fine,expected_coarse",
        [
            ("passed_to_t4", "passed"),
            ("passed_to_extraction", "passed"),
            ("filtered_t1_duplicate_message_id", "filtered_t1"),
            ("filtered_t1_known_automated_sender", "filtered_t1"),
            ("filtered_t1_marketing_unsubscribe", "filtered_t1"),
            ("filtered_t1_auto_reply", "filtered_t1"),
            ("filtered_t1_ndr_bounce", "filtered_t1"),
            ("filtered_t1_empty_body", "filtered_t1"),
            ("filtered_t1_oversized", "filtered_t1"),
            ("filtered_t2_no_known_entity", "filtered_t2"),
            ("filtered_t3_below_threshold", "filtered_t3"),
            ("filtered_t4_not_organizationally_relevant", "filtered_t4"),
            ("filtered_t4_budget_exceeded", "filtered_t4"),
        ],
    )
    def test_fine_to_coarse_mappings(self, fine: str, expected_coarse: str) -> None:
        assert coarse_tier_outcome_for_metric(fine) == expected_coarse

    def test_pending_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="pending"):
            coarse_tier_outcome_for_metric("pending")

    def test_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            coarse_tier_outcome_for_metric("totally_invented_outcome")
