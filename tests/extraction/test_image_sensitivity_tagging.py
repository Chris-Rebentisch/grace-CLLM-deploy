"""CP5 contract tests: image sensitivity tagging (D503).

Verifies:
1. EXIF GPS data triggers |pii_dense| tag.
2. Empty/no-match inputs produce empty sensitivity tags.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_sensitivity_tags_empty_for_clean_image(tmp_path):
    """No EXIF GPS, no OCR text, no privilege phrases -> empty tags."""
    from src.extraction.image_pipeline import _compute_sensitivity_tags

    clean_image = tmp_path / "clean.jpg"
    # Create a minimal file (not a real image, but _compute_sensitivity_tags
    # catches PIL errors gracefully)
    clean_image.write_bytes(b"\x00" * 100)

    tags = _compute_sensitivity_tags(clean_image, ocr_text=None, exif_data=None)
    assert tags == ""


def test_sensitivity_tags_pii_from_exif_gps(tmp_path):
    """EXIF GPS data in the exif_data dict -> |pii_dense| tag."""
    from src.extraction.image_pipeline import _compute_sensitivity_tags

    image_path = tmp_path / "gps_photo.jpg"
    image_path.write_bytes(b"\x00" * 100)

    exif_with_gps = {"GPSLatitude": "40.7128", "GPSLongitude": "-74.0060"}
    tags = _compute_sensitivity_tags(image_path, ocr_text=None, exif_data=exif_with_gps)
    assert "|pii_dense|" in tags


def test_pii_dense_routes_local_vision_provider():
    """AC14/D503: PII-dense sensitivity tags force local vision profile selection."""
    from unittest.mock import patch

    from src.extraction.router import select_vision_provider
    from src.extraction.router_config import ProviderProfile, RouterConfig

    profiles = {
        "ollama_vision": ProviderProfile(
            context_window=131072,
            max_output_tokens=32768,
            pricing_input_per_m=0.0,
            pricing_output_per_m=0.0,
            airgap_eligible=True,
            data_residency="local",
            vision_capable=True,
            vision_model="qwen2.5-vl:32b",
        ),
        "haiku_vision": ProviderProfile(
            context_window=200000,
            max_output_tokens=8192,
            pricing_input_per_m=1.0,
            pricing_output_per_m=5.0,
            airgap_eligible=False,
            data_residency="us",
            vision_capable=True,
            vision_model="claude-haiku-4-5-20251001",
        ),
    }
    config = RouterConfig(profiles=profiles)

    with patch("src.extraction.router._read_airgap_mode", return_value=False):
        result = select_vision_provider(config, sensitivity_tags="|pii_dense|")

    assert result is not None
    assert result[0] == "ollama_vision"
