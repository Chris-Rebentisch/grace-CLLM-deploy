"""CP2 contract test: Image_Asset in META_ENTITY_TYPES (D501)."""

from src.graph.migration_types import META_ENTITY_TYPES


def test_image_asset_in_meta_entity_types():
    """Assert Image_Asset is present and META_ENTITY_TYPES count is 8."""
    assert "Image_Asset" in META_ENTITY_TYPES, "Image_Asset must be in META_ENTITY_TYPES"
    assert len(META_ENTITY_TYPES) == 8, f"Expected 8 META_ENTITY_TYPES, got {len(META_ENTITY_TYPES)}"

    # Verify key properties exist
    props = {p["name"] for p in META_ENTITY_TYPES["Image_Asset"]}
    assert "grace_id" in props
    assert "content_sha256" in props
    assert "vision_description_json" in props
    assert "sensitivity_tags" in props
    assert "_embedding" in props
