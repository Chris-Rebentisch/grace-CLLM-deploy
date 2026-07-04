"""Seed provisioning: download, validate, parse, and cache seed files."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog

from src.discovery.models import load_discovery_config
from src.discovery.seed_models import (
    ProvisioningResult,
    SeedReference,
    SeedSource,
    SeedStatus,
)
from src.discovery.seed_parser import parse_seed_file
from src.discovery.seed_registry import load_seed_registry, resolve_sources_for_industry

logger = structlog.get_logger()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_seeds_dir(config: dict) -> Path:
    """Get the seeds directory from config."""
    seed_config = config.get("seed", {})
    return _PROJECT_ROOT / seed_config.get("seeds_dir", "seeds")


def _get_parsed_cache_dir(config: dict) -> Path:
    """Get the parsed cache directory from config."""
    seed_config = config.get("seed", {})
    return _PROJECT_ROOT / seed_config.get("parsed_cache_dir", "seeds/parsed")


def check_seed_status(sources: list[SeedSource], config: dict | None = None) -> list[SeedStatus]:
    """Check download and parse status for each source.

    Args:
        sources: List of seed sources to check.
        config: Discovery config dict. If None, loads from discovery.yaml.

    Returns:
        List of SeedStatus objects.
    """
    if config is None:
        config = load_discovery_config()
    cache_dir = _get_parsed_cache_dir(config)

    statuses = []
    for source in sources:
        local = Path(source.local_path)
        is_downloaded = local.exists()
        cached_path = cache_dir / f"{source.id}.json"
        is_parsed = cached_path.exists()

        file_size = None
        entity_count = None
        rel_count = None

        if is_downloaded:
            file_size = local.stat().st_size

        if is_parsed:
            try:
                cached_data = json.loads(cached_path.read_text())
                entity_count = len(cached_data.get("entity_types", []))
                rel_count = len(cached_data.get("relationships", []))
            except (json.JSONDecodeError, OSError):
                pass

        statuses.append(
            SeedStatus(
                source_id=source.id,
                name=source.name,
                is_downloaded=is_downloaded,
                is_parsed=is_parsed,
                local_path=source.local_path,
                file_size_bytes=file_size,
                entity_types_count=entity_count,
                relationships_count=rel_count,
            )
        )

    return statuses


async def download_seeds(sources: list[SeedSource]) -> ProvisioningResult:
    """Download missing seed files via httpx.

    Skips sources that already exist locally. Retries once on connection error.
    Timeout: 30 seconds per file.

    Args:
        sources: List of seed sources to download.

    Returns:
        ProvisioningResult with counts and errors.
    """
    downloaded = []
    already_present = []
    failed = []
    errors = []
    industry = ""

    for source in sources:
        local = Path(source.local_path)
        if local.exists():
            already_present.append(source.id)
            logger.info("seed_already_present", source_id=source.id, path=str(local))
            continue

        # Create parent directories
        local.parent.mkdir(parents=True, exist_ok=True)

        # Attempt download with 1 retry
        success = False
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(source.download_url)
                    resp.raise_for_status()
                    local.write_bytes(resp.content)

                    if local.stat().st_size == 0:
                        local.unlink()
                        raise ValueError("Downloaded file is empty")

                    downloaded.append(source.id)
                    success = True
                    logger.info(
                        "seed_downloaded",
                        source_id=source.id,
                        size_bytes=local.stat().st_size,
                    )
                    break
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt == 0:
                    logger.warning(
                        "seed_download_retry",
                        source_id=source.id,
                        error=str(e),
                    )
                    continue
                failed.append(source.id)
                errors.append(f"{source.id}: {e}")
                logger.error(
                    "seed_download_failed",
                    source_id=source.id,
                    error=str(e),
                )
            except Exception as e:
                failed.append(source.id)
                errors.append(f"{source.id}: {e}")
                logger.error(
                    "seed_download_failed",
                    source_id=source.id,
                    error=str(e),
                )
                break

    return ProvisioningResult(
        industry_profile=industry,
        sources_downloaded=downloaded,
        sources_already_present=already_present,
        sources_failed=failed,
        total_files=len(downloaded) + len(already_present),
        errors=errors,
    )


def parse_and_cache_seeds(
    sources: list[SeedSource], config: dict, industry_id: str = ""
) -> SeedReference:
    """Parse seed files and cache results. Uses cache when fresh.

    Args:
        sources: List of seed sources to parse.
        config: Discovery config dict.
        industry_id: The selected industry profile ID.

    Returns:
        Combined SeedReference from all parsed sources.
    """
    cache_dir = _get_parsed_cache_dir(config)
    cache_dir.mkdir(parents=True, exist_ok=True)

    all_entity_types = []
    all_relationships = []
    source_files = []
    registry = load_seed_registry()

    for source in sources:
        local = Path(source.local_path)
        if not local.exists():
            logger.warning("seed_file_missing_for_parse", source_id=source.id)
            continue

        cached_path = cache_dir / f"{source.id}.json"

        # Use cache if it exists and is newer than the seed file
        if cached_path.exists():
            seed_mtime = local.stat().st_mtime
            cache_mtime = cached_path.stat().st_mtime
            if cache_mtime > seed_mtime:
                try:
                    cached_data = json.loads(cached_path.read_text())
                    # Validate industry_profile matches (Issue 7)
                    cached_industry = cached_data.get("industry_profile", "")
                    if cached_industry and cached_industry != industry_id:
                        logger.info(
                            "seed_cache_industry_mismatch",
                            source_id=source.id,
                            cached=cached_industry,
                            current=industry_id,
                        )
                    else:
                        logger.info("seed_cache_hit", source_id=source.id)
                        from src.discovery.seed_models import SeedEntityType, SeedRelationship

                        for et_data in cached_data.get("entity_types", []):
                            all_entity_types.append(SeedEntityType.model_validate(et_data))
                        for rel_data in cached_data.get("relationships", []):
                            all_relationships.append(SeedRelationship.model_validate(rel_data))
                        source_files.append(source.local_path)
                        continue
                except (json.JSONDecodeError, Exception) as e:
                    logger.warning("seed_cache_invalid", source_id=source.id, error=str(e))

        # Parse fresh
        entity_types, relationships = parse_seed_file(source, config)
        all_entity_types.extend(entity_types)
        all_relationships.extend(relationships)
        source_files.append(source.local_path)

        # Write cache (include industry_profile for validation on load)
        cache_data = {
            "industry_profile": industry_id,
            "entity_types": [et.model_dump() for et in entity_types],
            "relationships": [r.model_dump() for r in relationships],
        }
        cached_path.write_text(json.dumps(cache_data, indent=2, default=str))
        logger.info("seed_cache_written", source_id=source.id, path=str(cached_path))

    return SeedReference(
        entity_types=all_entity_types,
        relationships=all_relationships,
        source_files=source_files,
        industry_profile=industry_id,
        registry_version=registry.version,
        total_entity_types=len(all_entity_types),
        total_relationships=len(all_relationships),
    )


async def provision_seeds(
    industry_id: str,
) -> tuple[ProvisioningResult, SeedReference]:
    """Full provisioning pipeline: resolve, check, download, parse, cache.

    Args:
        industry_id: The industry profile to provision for.

    Returns:
        Tuple of (ProvisioningResult, SeedReference).
    """
    config = load_discovery_config()
    sources = resolve_sources_for_industry(industry_id)

    if not sources:
        return (
            ProvisioningResult(
                industry_profile=industry_id,
                sources_downloaded=[],
                sources_already_present=[],
                sources_failed=[],
                total_files=0,
                errors=[f"No sources found for industry: {industry_id}"],
            ),
            SeedReference(
                entity_types=[],
                relationships=[],
                source_files=[],
                industry_profile=industry_id,
                registry_version=load_seed_registry().version,
            ),
        )

    # Download missing seeds
    result = await download_seeds(sources)
    result.industry_profile = industry_id

    # Parse and cache all seeds
    seed_ref = parse_and_cache_seeds(sources, config, industry_id)

    return result, seed_ref
