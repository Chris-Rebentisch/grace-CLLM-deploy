"""Contract tests for image-OCR ingestion (Chunk 77a, D499).

Verify no new OTel instruments and config includes all 7 image extensions.
"""

import yaml
from pathlib import Path


_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "discovery.yaml"


def test_no_new_otel_instruments():
    """GOLDEN_NAMES count is 152 (D535 adds the 6th correlation pattern counter)."""
    # Import the GOLDEN_NAMES set from the metric contract test module.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "test_metric_contract",
        Path(__file__).resolve().parent.parent / "analytics" / "test_metric_contract.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # D517: GOLDEN_NAMES 149→151 (Chunk 80b corroboration promotions counter);
    # D535: 151→152 (ontology_constraint_conflict diagnostic pattern counter).
    assert len(mod.GOLDEN_NAMES) == 152, (
        f"GOLDEN_NAMES count changed: expected 152, got {len(mod.GOLDEN_NAMES)}"
    )


def test_config_includes_image_extensions():
    """config/discovery.yaml supported_extensions includes all 7 image formats."""
    with open(_CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    extensions = config.get("supported_extensions", [])
    expected = [".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"]
    for ext in expected:
        assert ext in extensions, f"Missing image extension in config: {ext}"
