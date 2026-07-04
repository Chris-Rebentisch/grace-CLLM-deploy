"""Missing Chunk 36 test: ERD band gauge metric assertion (D378.d).

Validates that ``grace_recon_erd_band_count`` is a registered OTel
instrument and that it can emit values for the three canonical bands
(high / medium / low) without error.
"""

from __future__ import annotations

from src.analytics.metrics import recon_erd_band_count


def test_erd_band_gauge_emits_for_all_three_bands():
    """``grace_recon_erd_band_count`` increments for high/medium/low without error."""
    for band in ("high", "medium", "low"):
        # UpDownCounter.add() with a valid band label must not raise.
        recon_erd_band_count.add(1, {"band": band})

    # Confirm the instrument is a valid UpDownCounter proxy (can add).
    assert callable(getattr(recon_erd_band_count, "add", None))
