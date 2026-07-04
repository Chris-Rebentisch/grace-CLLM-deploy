"""D471 router module tests (Chunk 72b CP2)."""

import re
from pathlib import Path

import pytest

from src.extraction.router import (
    IMPLEMENTED_STRATEGIES,
    RouterStrategy,
    estimate_input_tokens,
    route,
    route_by_sensitivity,
    route_by_size_tier,
    stage_shard_directory,
    validate_strategy_implemented,
)
from src.extraction.router_config import ExtractionShard, ProviderProfile, RouterConfig


def _make_config(**overrides) -> RouterConfig:
    """Build a minimal RouterConfig for testing."""
    profiles = {
        "local_ollama": ProviderProfile(
            context_window=131072,
            max_output_tokens=32768,
            pricing_input_per_m=0.0,
            pricing_output_per_m=0.0,
            airgap_eligible=True,
        ),
        "cloud_haiku": ProviderProfile(
            context_window=200000,
            max_output_tokens=8192,
            pricing_input_per_m=1.0,
            pricing_output_per_m=5.0,
            airgap_eligible=False,
        ),
    }
    defaults = {
        "profiles": profiles,
        "size_tier_mapping": {"small": "local_ollama", "medium": "cloud_haiku", "large": "local_ollama"},
    }
    defaults.update(overrides)
    return RouterConfig(**defaults)


def test_route_by_sensitivity_pins_privileged_to_airgap(tmp_path):
    """Privileged file routes to airgap-eligible provider."""
    privileged = tmp_path / "privileged_and_confidential_memo.txt"
    privileged.write_text("This is privileged and confidential content.")

    config = _make_config()
    shards = route_by_sensitivity([privileged], config)

    assert len(shards) >= 1
    # The privileged file should be routed to the airgap provider
    airgap_shard = [s for s in shards if s.provider == "local_ollama"]
    assert len(airgap_shard) == 1
    assert privileged in airgap_shard[0].source_paths


def test_route_by_sensitivity_non_privileged_passes_through(tmp_path):
    """Non-privileged file routes to default (non-airgap) provider."""
    normal = tmp_path / "invoice_2024.txt"
    normal.write_text("Standard invoice for services rendered.")

    config = _make_config()
    shards = route_by_sensitivity([normal], config)

    assert len(shards) >= 1
    # The normal file should go to the cloud provider (first non-airgap)
    cloud_shard = [s for s in shards if s.provider == "cloud_haiku"]
    assert len(cloud_shard) == 1
    assert normal in cloud_shard[0].source_paths


def test_route_by_size_tier_three_bins(tmp_path):
    """Small/medium/large files map to correct providers per size_tier_mapping."""
    small = tmp_path / "tiny.txt"
    small.write_text("x" * 100)  # well under 50KB

    medium = tmp_path / "medium.txt"
    medium.write_text("y" * 100_000)  # ~100KB, between 50KB and 5MB

    large = tmp_path / "huge.txt"
    large.write_text("z" * 6_000_000)  # ~6MB, over 5MB

    config = _make_config()
    shards = route_by_size_tier([small, medium, large], config)

    providers = {s.provider for s in shards}
    # small and large map to local_ollama, medium maps to cloud_haiku
    assert "local_ollama" in providers
    assert "cloud_haiku" in providers

    for shard in shards:
        if shard.provider == "cloud_haiku":
            assert medium in shard.source_paths
        if shard.provider == "local_ollama":
            # Should contain small and/or large
            shard_files = set(shard.source_paths)
            assert small in shard_files or large in shard_files


def test_route_dispatcher_known_strategy(tmp_path):
    """route() with SENSITIVITY dispatches to route_by_sensitivity."""
    normal = tmp_path / "test.txt"
    normal.write_text("Normal content here.")

    config = _make_config()
    shards = route([normal], config, RouterStrategy.SENSITIVITY)

    assert len(shards) >= 1
    assert all(isinstance(s, ExtractionShard) for s in shards)


def test_route_dispatcher_deferred_strategy_raises():
    """route() with PARALLEL_SPLIT raises NotImplementedError with '72c' in message."""
    config = _make_config()
    with pytest.raises(NotImplementedError, match="72c"):
        route([], config, RouterStrategy.PARALLEL_SPLIT)


def test_estimate_input_tokens_text_vs_pdf(tmp_path):
    """Text returns 'high' confidence; PDF returns 'medium' or 'low'."""
    text_file = tmp_path / "sample.txt"
    text_file.write_text("Hello world " * 1000)

    tokens, confidence = estimate_input_tokens(text_file)
    assert confidence == "high"
    assert tokens > 0

    pdf_file = tmp_path / "sample.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 " + b"\x00" * 100_000)

    tokens_pdf, confidence_pdf = estimate_input_tokens(pdf_file)
    assert confidence_pdf in ("medium", "low")
    assert tokens_pdf > 0


def test_stage_shard_directory_creates_symlinks(tmp_path):
    """Staging creates directory with symlinks, collision resolution works."""
    # Create source files
    file_a = tmp_path / "dir_a" / "report.txt"
    file_a.parent.mkdir()
    file_a.write_text("Report A")

    file_b = tmp_path / "dir_b" / "report.txt"
    file_b.parent.mkdir()
    file_b.write_text("Report B")

    shard = ExtractionShard(
        source_paths=[file_a, file_b],
        provider="test_provider",
        model="test_model",
        estimated_input_tokens=1000,
    )

    parent_dir = tmp_path / "staging"
    parent_dir.mkdir()

    result = stage_shard_directory(shard, parent_dir)

    assert result.is_dir()
    # Should have 2 files (collision resolved)
    staged_files = list(result.iterdir())
    assert len(staged_files) == 2

    # Both should be symlinks
    for f in staged_files:
        assert f.is_symlink()

    # One should be report.txt and the other report_1.txt
    names = {f.name for f in staged_files}
    assert "report.txt" in names
    assert "report_1.txt" in names
