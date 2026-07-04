"""Tests for seed provisioner (mocked HTTP, no real network calls)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.discovery.seed_models import (
    ProvisioningResult,
    SeedReference,
    SeedSource,
    SeedStatus,
)
from src.discovery.seed_provisioner import (
    check_seed_status,
    download_seeds,
    parse_and_cache_seeds,
    provision_seeds,
)


def _make_source(source_id: str = "test_src", local_path: str = "seeds/test.rdf") -> SeedSource:
    """Create a test SeedSource."""
    return SeedSource(
        id=source_id,
        name="Test Source",
        source_ontology="fibo",
        description="Test",
        download_url="https://example.com/test.rdf",
        local_path=local_path,
        file_format="rdf_xml",
        version="1.0",
    )


def test_check_seed_status_file_exists(tmp_path):
    """Reports is_downloaded=True when file exists."""
    seed_file = tmp_path / "test.rdf"
    seed_file.write_text("<rdf>test</rdf>")

    source = _make_source(local_path=str(seed_file))
    config = {"seed": {"parsed_cache_dir": str(tmp_path / "parsed")}}

    statuses = check_seed_status([source], config)
    assert len(statuses) == 1
    assert statuses[0].is_downloaded is True
    assert statuses[0].file_size_bytes > 0


def test_check_seed_status_file_missing(tmp_path):
    """Reports is_downloaded=False when file does not exist."""
    source = _make_source(local_path=str(tmp_path / "nonexistent.rdf"))
    config = {"seed": {"parsed_cache_dir": str(tmp_path / "parsed")}}

    statuses = check_seed_status([source], config)
    assert statuses[0].is_downloaded is False
    assert statuses[0].file_size_bytes is None


def test_check_seed_status_with_cache(tmp_path):
    """Reports is_parsed=True when cached JSON exists."""
    seed_file = tmp_path / "test.rdf"
    seed_file.write_text("<rdf>test</rdf>")

    cache_dir = tmp_path / "parsed"
    cache_dir.mkdir()
    cache_file = cache_dir / "test_src.json"
    cache_file.write_text(json.dumps({"entity_types": [{"name": "Foo"}], "relationships": []}))

    source = _make_source(source_id="test_src", local_path=str(seed_file))
    config = {"seed": {"parsed_cache_dir": str(cache_dir)}}

    statuses = check_seed_status([source], config)
    assert statuses[0].is_parsed is True
    assert statuses[0].entity_types_count == 1


@pytest.mark.asyncio
async def test_download_seeds_success(tmp_path):
    """Successful download via mocked httpx."""
    dest = tmp_path / "test.rdf"
    source = _make_source(local_path=str(dest))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"<rdf>content</rdf>"
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.discovery.seed_provisioner.httpx.AsyncClient", return_value=mock_client):
        result = await download_seeds([source])

    assert source.id in result.sources_downloaded
    assert dest.exists()


@pytest.mark.asyncio
async def test_download_seeds_skips_existing(tmp_path):
    """Skips download when file already exists."""
    dest = tmp_path / "test.rdf"
    dest.write_text("<rdf>existing</rdf>")

    source = _make_source(local_path=str(dest))
    result = await download_seeds([source])

    assert source.id in result.sources_already_present
    assert len(result.sources_downloaded) == 0


@pytest.mark.asyncio
async def test_download_seeds_connection_error(tmp_path):
    """Records failure on connection error after retry."""
    import httpx

    dest = tmp_path / "test.rdf"
    source = _make_source(local_path=str(dest))

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.discovery.seed_provisioner.httpx.AsyncClient", return_value=mock_client):
        result = await download_seeds([source])

    assert source.id in result.sources_failed
    assert len(result.errors) > 0


@pytest.mark.asyncio
async def test_download_seeds_timeout(tmp_path):
    """Records failure on timeout after retry."""
    import httpx

    dest = tmp_path / "test.rdf"
    source = _make_source(local_path=str(dest))

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.discovery.seed_provisioner.httpx.AsyncClient", return_value=mock_client):
        result = await download_seeds([source])

    assert source.id in result.sources_failed


def test_parse_and_cache_writes_cache(tmp_path):
    """parse_and_cache_seeds writes cache files."""
    # Create a minimal RDF file
    seed_file = tmp_path / "test.rdf"
    seed_file.write_text("""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Class rdf:about="http://example.com/TestClass">
    <rdfs:label>TestClass</rdfs:label>
  </owl:Class>
</rdf:RDF>""")

    cache_dir = tmp_path / "parsed"
    source = _make_source(source_id="test_src", local_path=str(seed_file))
    config = {"seed": {"parsed_cache_dir": str(cache_dir)}}

    ref = parse_and_cache_seeds([source], config, "general")
    assert isinstance(ref, SeedReference)
    assert (cache_dir / "test_src.json").exists()


def test_parse_and_cache_uses_fresh_cache(tmp_path):
    """parse_and_cache_seeds uses cache when it's newer than the seed file."""
    import time

    seed_file = tmp_path / "test.rdf"
    seed_file.write_text("<rdf></rdf>")

    cache_dir = tmp_path / "parsed"
    cache_dir.mkdir()
    time.sleep(0.05)  # Ensure cache is newer

    cache_file = cache_dir / "test_src.json"
    cache_data = {
        "entity_types": [
            {
                "name": "CachedType",
                "source_ontology": "fibo",
                "source_uri": "http://example.com/CachedType",
            }
        ],
        "relationships": [],
    }
    cache_file.write_text(json.dumps(cache_data))

    source = _make_source(source_id="test_src", local_path=str(seed_file))
    config = {"seed": {"parsed_cache_dir": str(cache_dir)}}

    ref = parse_and_cache_seeds([source], config, "general")
    # Should use cached data
    assert any(et.name == "CachedType" for et in ref.entity_types)


@pytest.mark.asyncio
async def test_provision_seeds_full_pipeline(tmp_path):
    """Full provision pipeline with mocked download."""
    # Create a seed file that already exists
    seed_file = tmp_path / "test.rdf"
    seed_file.write_text("""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Class rdf:about="http://example.com/Foo"/>
</rdf:RDF>""")

    mock_source = _make_source(local_path=str(seed_file))

    with (
        patch("src.discovery.seed_provisioner.resolve_sources_for_industry", return_value=[mock_source]),
        patch("src.discovery.seed_provisioner.load_discovery_config", return_value={
            "seed": {"parsed_cache_dir": str(tmp_path / "parsed")}
        }),
    ):
        result, seed_ref = await provision_seeds("financial_services")

    assert isinstance(result, ProvisioningResult)
    assert isinstance(seed_ref, SeedReference)
    assert result.industry_profile == "financial_services"
