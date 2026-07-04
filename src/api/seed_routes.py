"""FastAPI endpoints for seed registry, provisioning, and management."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.discovery.models import load_discovery_config
from src.discovery.seed_models import SeedSource
from src.discovery.seed_provisioner import (
    check_seed_status,
    parse_and_cache_seeds,
    provision_seeds,
)
from src.discovery.seed_registry import (
    list_industry_profiles,
    load_seed_registry,
    resolve_sources_for_industry,
)
from src.discovery.seed_suggester import suggest_additional_seeds
from src.shared.database import get_db

router = APIRouter(prefix="/api/discovery/seed", tags=["seed"])


class ProvisionRequest(BaseModel):
    """Request body for the provision endpoint."""

    industry_id: str = Field(description="Industry profile ID to provision for")
    confirmed: bool = Field(
        default=False,
        description="If false, return preview. If true, download and parse.",
    )


@router.get("/industries")
async def list_industries() -> list[dict]:
    """List available industry profiles from the seed registry."""
    profiles = list_industry_profiles()
    return [p.model_dump() for p in profiles]


@router.get("/sources")
async def list_sources(industry_id: str | None = None) -> list[dict]:
    """List seed sources, optionally filtered by industry profile."""
    if industry_id:
        sources = resolve_sources_for_industry(industry_id)
    else:
        registry = load_seed_registry()
        sources = registry.sources
    return [s.model_dump() for s in sources]


@router.post("/provision")
async def provision_endpoint(request: ProvisionRequest) -> dict:
    """Provision seeds for an industry profile.

    If confirmed=false: returns preview of what would be downloaded.
    If confirmed=true: downloads missing seeds, parses, caches, returns result.
    """
    sources = resolve_sources_for_industry(request.industry_id)
    if not sources:
        return {"error": f"Unknown industry profile: {request.industry_id}"}

    config = load_discovery_config()

    if not request.confirmed:
        # Preview mode
        statuses = check_seed_status(sources, config)
        return {
            "status": "preview",
            "industry_profile": request.industry_id,
            "sources": [s.model_dump() for s in statuses],
            "needs_download": [
                s.source_id for s in statuses if not s.is_downloaded
            ],
            "already_present": [
                s.source_id for s in statuses if s.is_downloaded
            ],
        }

    # Confirmed — provision
    result, seed_ref = await provision_seeds(request.industry_id)

    # Update industry_profile in discovery.yaml
    _update_industry_profile(request.industry_id)

    return {
        "status": "completed",
        "industry_profile": request.industry_id,
        "sources_downloaded": result.sources_downloaded,
        "sources_already_present": result.sources_already_present,
        "sources_failed": result.sources_failed,
        "total_entity_types": seed_ref.total_entity_types,
        "total_relationships": seed_ref.total_relationships,
        "errors": result.errors,
    }


@router.get("/status")
async def seed_status() -> dict:
    """Return which seeds are downloaded, parsed, and ready."""
    config = load_discovery_config()
    seed_config = config.get("seed", {})
    industry_id = seed_config.get("industry_profile", "")

    if not industry_id:
        # Show all sources if no industry selected
        registry = load_seed_registry()
        sources = registry.sources
    else:
        sources = resolve_sources_for_industry(industry_id)

    statuses = check_seed_status(sources, config)
    return {
        "industry_profile": industry_id,
        "sources": [s.model_dump() for s in statuses],
    }


@router.get("/reference")
async def get_reference() -> dict:
    """Return the full parsed SeedReference JSON for Chunk 7 consumption."""
    config = load_discovery_config()
    seed_config = config.get("seed", {})
    industry_id = seed_config.get("industry_profile", "")

    if not industry_id:
        return {"error": "No industry profile configured. Use POST /provision first."}

    sources = resolve_sources_for_industry(industry_id)
    available_sources = [s for s in sources if Path(s.local_path).exists()]

    if not available_sources:
        return {"error": "No seed files found on disk. Run provisioning first."}

    seed_ref = parse_and_cache_seeds(available_sources, config, industry_id)
    return seed_ref.model_dump(mode="json")


@router.post("/suggest")
async def suggest_seeds() -> dict:
    """Run the LLM-powered seed suggester."""
    config = load_discovery_config()

    try:
        db_gen = get_db()
        db = next(db_gen)
    except Exception:
        db = None
        db_gen = None

    try:
        suggestions = await suggest_additional_seeds(config, db)
        return {"suggestions": [s.model_dump() for s in suggestions]}
    finally:
        if db_gen is not None:
            try:
                next(db_gen)
            except StopIteration:
                pass


def _update_industry_profile(industry_id: str) -> None:
    """Update the seed.industry_profile in discovery.yaml."""
    import yaml
    from pathlib import Path

    yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "discovery.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    if "seed" not in data:
        data["seed"] = {}
    data["seed"]["industry_profile"] = industry_id

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# Need Path for the reference endpoint
from pathlib import Path
