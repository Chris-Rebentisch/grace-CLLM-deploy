"""D471 multi-provider extraction router module.

Invariant: D246 — no network/DB/LLM I/O. Bounded filesystem reads only
(``Path.stat()`` for file sizes, ``Path.read_text()`` for text-file char
count, one-time cached ``config/sensitivity_rules.yaml`` load). Bounded
filesystem writes for shard staging symlinks via ``stage_shard_directory()``.

Authorization: D471 — Chunk 72b.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path

import yaml

from src.extraction.router_config import ExtractionShard, RouterConfig

# ---------------------------------------------------------------------------
# RouterStrategy enum (5 values; 2 implemented in v1, 3 deferred to 72c)
# ---------------------------------------------------------------------------


class RouterStrategy(str, Enum):
    """Extraction routing strategy selector."""

    SENSITIVITY = "sensitivity"
    SIZE_TIER = "size_tier"
    PARALLEL_SPLIT = "parallel_split"  # 72c
    COST_BUDGET = "cost_budget"  # 72c
    OPERATOR_TAG = "operator_tag"  # 72c


IMPLEMENTED_STRATEGIES: frozenset[RouterStrategy] = frozenset(
    {RouterStrategy.SENSITIVITY, RouterStrategy.SIZE_TIER}
)


def validate_strategy_implemented(strategy: RouterStrategy) -> None:
    """Raise ``NotImplementedError`` if the strategy is not yet implemented.

    Callers (API route, CLI) use this to pre-validate before dispatch.
    """
    if strategy not in IMPLEMENTED_STRATEGIES:
        raise NotImplementedError(
            f"Strategy '{strategy.value}' is not yet implemented (deferred to 72c)"
        )


# ---------------------------------------------------------------------------
# Sensitivity rules cache (one-time load)
# ---------------------------------------------------------------------------

_SENSITIVITY_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "sensitivity_rules.yaml"
_privilege_patterns: list[re.Pattern] | None = None


def _load_privilege_patterns() -> list[re.Pattern]:
    """Load privilege phrases from sensitivity_rules.yaml (cached)."""
    global _privilege_patterns
    if _privilege_patterns is not None:
        return _privilege_patterns

    phrases: list[str] = []
    if _SENSITIVITY_RULES_PATH.exists():
        with open(_SENSITIVITY_RULES_PATH) as fh:
            data = yaml.safe_load(fh) or {}
        phrases = data.get("privilege_phrases", [])

    _privilege_patterns = [re.compile(re.escape(p), re.IGNORECASE) for p in phrases]
    return _privilege_patterns


def _file_is_privileged(path: Path) -> bool:
    """Check if a file is likely privileged via filename + first-1KB content peek.

    Best-effort fallback when the full sensitivity tagger has not run.
    """
    patterns = _load_privilege_patterns()
    if not patterns:
        return False

    # Check filename
    name_lower = path.name.lower()
    for pat in patterns:
        if pat.search(name_lower):
            return True

    # Peek at first 1KB of content for text-like files
    text_suffixes = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm"}
    if path.suffix.lower() in text_suffixes:
        try:
            content = path.read_text(errors="replace")[:1024]
            for pat in patterns:
                if pat.search(content):
                    return True
        except OSError:
            pass

    return False


# ---------------------------------------------------------------------------
# Tiered token estimator
# ---------------------------------------------------------------------------

_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm"}
_BINARY_DOC_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx"}


def estimate_input_tokens(path: Path) -> tuple[int, str]:
    """Tiered token estimator with 1.3x safety multiplier.

    Returns ``(estimated_tokens, confidence)`` where confidence is one of
    ``"high"``, ``"medium"``, or ``"low"``.

    - Text/Markdown/CSV/plaintext: ``max(byte_size / 4, content_chars / 4) * 1.3``.
      Confidence: ``"high"``.
    - PDF/DOCX/XLSX/PPTX: ``page_estimate * 2500 * 1.3`` where
      ``page_estimate = byte_size / 50_000``. Confidence: ``"medium"``
      (Docling available) or ``"low"`` (fallback).
    """
    suffix = path.suffix.lower()
    byte_size = path.stat().st_size

    if suffix in _TEXT_SUFFIXES:
        try:
            content = path.read_text(errors="replace")
            char_count = len(content)
        except OSError:
            char_count = 0
        raw = max(byte_size / 4, char_count / 4)
        return int(raw * 1.3), "high"

    if suffix in _BINARY_DOC_SUFFIXES:
        # Page estimate from file size (rough heuristic)
        page_estimate = max(1, byte_size / 50_000)
        raw = page_estimate * 2500
        return int(raw * 1.3), "medium"

    # Unknown file type — fall back to byte-based estimate
    raw = byte_size / 4
    return int(raw * 1.3), "low"


# ---------------------------------------------------------------------------
# Routing strategies
# ---------------------------------------------------------------------------


def _find_airgap_provider(config: RouterConfig) -> tuple[str, str] | None:
    """Find the first airgap-eligible provider in the catalog."""
    for name, profile in config.profiles.items():
        if profile.airgap_eligible:
            return name, name  # provider = profile key, model = profile key
    return None


def _find_default_provider(config: RouterConfig) -> tuple[str, str]:
    """Find the default (first non-airgap or first) provider."""
    for name, profile in config.profiles.items():
        if not profile.airgap_eligible:
            return name, name
    # Fallback to first provider
    first_key = next(iter(config.profiles))
    return first_key, first_key


def route_by_sensitivity(
    paths: list[Path], config: RouterConfig
) -> list[ExtractionShard]:
    """Route documents by sensitivity: privileged files go to airgap provider.

    Files matching privilege patterns are hard-pinned to the first
    ``airgap_eligible=true`` provider. Non-privileged files go to the
    default (first non-airgap) provider.
    """
    airgap = _find_airgap_provider(config)
    default = _find_default_provider(config)

    privileged_paths: list[Path] = []
    normal_paths: list[Path] = []

    for p in paths:
        if _file_is_privileged(p):
            privileged_paths.append(p)
        else:
            normal_paths.append(p)

    shards: list[ExtractionShard] = []

    if privileged_paths and airgap:
        provider_name, model_name = airgap
        total_tokens = sum(estimate_input_tokens(p)[0] for p in privileged_paths)
        shards.append(
            ExtractionShard(
                source_paths=privileged_paths,
                provider=provider_name,
                model=model_name,
                estimated_input_tokens=total_tokens,
            )
        )

    if normal_paths:
        provider_name, model_name = default
        total_tokens = sum(estimate_input_tokens(p)[0] for p in normal_paths)
        shards.append(
            ExtractionShard(
                source_paths=normal_paths,
                provider=provider_name,
                model=model_name,
                estimated_input_tokens=total_tokens,
            )
        )

    return shards


def route_by_size_tier(
    paths: list[Path], config: RouterConfig
) -> list[ExtractionShard]:
    """Route documents by size tier: small (<50KB), medium (50KB-5MB), large (>5MB).

    Each tier maps to a provider via ``config.size_tier_mapping``.
    """
    SMALL_THRESHOLD = 50 * 1024  # 50KB
    LARGE_THRESHOLD = 5 * 1024 * 1024  # 5MB

    bins: dict[str, list[Path]] = {"small": [], "medium": [], "large": []}

    for p in paths:
        size = p.stat().st_size
        if size < SMALL_THRESHOLD:
            bins["small"].append(p)
        elif size > LARGE_THRESHOLD:
            bins["large"].append(p)
        else:
            bins["medium"].append(p)

    shards: list[ExtractionShard] = []
    default_provider = next(iter(config.profiles), "ollama_72b")

    for tier, tier_paths in bins.items():
        if not tier_paths:
            continue
        provider_name = config.size_tier_mapping.get(tier, default_provider)
        total_tokens = sum(estimate_input_tokens(p)[0] for p in tier_paths)
        shards.append(
            ExtractionShard(
                source_paths=tier_paths,
                provider=provider_name,
                model=provider_name,
                estimated_input_tokens=total_tokens,
            )
        )

    return shards


# ---------------------------------------------------------------------------
# D503 (Chunk 77b): Vision airgap gate — runtime enforcement
# ---------------------------------------------------------------------------
# D503 dual enforcement: D232 config-POST guard prevents saving cloud config
# while airgapped, but is necessary-not-sufficient — it only guards config
# writes, not job execution at runtime. This router-level blanket airgap
# gate is the load-bearing runtime check: when airgap_mode=true, reject ALL
# non-airgap_eligible vision profiles regardless of sensitivity classification.
# Authorization: D503.

_DISCOVERY_YAML = Path(__file__).resolve().parents[2] / "config" / "discovery.yaml"


def _read_airgap_mode() -> bool:
    """Read airgap_mode from discovery.yaml (default True)."""
    try:
        with open(_DISCOVERY_YAML) as fh:
            data = yaml.safe_load(fh) or {}
        return bool(data.get("airgap_mode", True))
    except FileNotFoundError:
        return True


def select_vision_provider(
    config: RouterConfig,
    *,
    sensitivity_tags: str = "",
) -> tuple[str, str] | None:
    """Select a vision-capable provider profile, enforcing D503 airgap gate.

    Returns (profile_name, vision_model) or None if no suitable provider.
    Raises RuntimeError if airgapped and no airgap-eligible vision provider.

    D503: when airgap_mode=true, reject all non-airgap_eligible vision profiles
    regardless of sensitivity classification. PII-dense/privileged images always
    route to local provider even when airgap is off.
    """
    airgap = _read_airgap_mode()

    # Collect vision-capable profiles
    vision_profiles = {
        name: profile for name, profile in config.profiles.items()
        if profile.vision_capable and profile.vision_model
    }

    if not vision_profiles:
        return None

    # D503: blanket airgap gate — reject ALL non-airgap_eligible when airgapped
    if airgap:
        eligible = {
            name: p for name, p in vision_profiles.items()
            if p.airgap_eligible
        }
        if not eligible:
            raise RuntimeError(
                "D503: airgap_mode=true but no airgap-eligible vision provider "
                "in config/extraction_router.yaml. Cannot process images."
            )
        # Return first airgap-eligible vision provider
        name = next(iter(eligible))
        return name, eligible[name].vision_model  # type: ignore[return-value]

    # PII/privileged sensitivity routing: force local even when airgap is off
    pii_tags = {"|pii_dense|", "|privileged|"}
    if any(tag in sensitivity_tags for tag in pii_tags):
        local = {
            name: p for name, p in vision_profiles.items()
            if p.airgap_eligible
        }
        if local:
            name = next(iter(local))
            return name, local[name].vision_model  # type: ignore[return-value]

    # Default: first non-airgap vision provider (cloud)
    for name, profile in vision_profiles.items():
        if not profile.airgap_eligible:
            return name, profile.vision_model  # type: ignore[return-value]

    # Fallback: any vision provider
    name = next(iter(vision_profiles))
    return name, vision_profiles[name].vision_model  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------


def route(
    paths: list[Path], config: RouterConfig, strategy: RouterStrategy
) -> list[ExtractionShard]:
    """Dispatch to the appropriate routing strategy.

    Calls ``validate_strategy_implemented()`` as defense-in-depth before
    dispatching. Unimplemented strategies raise ``NotImplementedError``.
    """
    validate_strategy_implemented(strategy)

    if strategy == RouterStrategy.SENSITIVITY:
        return route_by_sensitivity(paths, config)
    elif strategy == RouterStrategy.SIZE_TIER:
        return route_by_size_tier(paths, config)
    else:
        # Should not reach here after validation, but guard anyway
        raise NotImplementedError(
            f"Strategy '{strategy.value}' is not yet implemented (deferred to 72c)"
        )


# ---------------------------------------------------------------------------
# Shard staging directory
# ---------------------------------------------------------------------------

_shard_counter = 0


def stage_shard_directory(shard: ExtractionShard, parent_dir: Path) -> Path:
    """Create a subdirectory with symlinks to shard source files.

    Creates ``parent_dir/shard_{provider}_{index}/`` and symlinks each file
    from ``shard.source_paths`` into it. Name collisions are resolved by
    appending ``_N`` suffix before the extension.

    Returns the created subdirectory path.
    """
    global _shard_counter
    subdir = parent_dir / f"shard_{shard.provider}_{_shard_counter}"
    _shard_counter += 1
    subdir.mkdir(parents=True, exist_ok=True)

    used_names: set[str] = set()
    for src_path in shard.source_paths:
        stem = src_path.stem
        suffix = src_path.suffix
        candidate = f"{stem}{suffix}"

        if candidate in used_names:
            counter = 1
            while f"{stem}_{counter}{suffix}" in used_names:
                counter += 1
            candidate = f"{stem}_{counter}{suffix}"

        used_names.add(candidate)
        link_path = subdir / candidate
        link_path.symlink_to(src_path.resolve())

    return subdir
