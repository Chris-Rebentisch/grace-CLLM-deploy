"""Layer-selective federation rules engine (Chunk 51, D402/D403).

Pure-function module — no I/O dependencies. No ArcadeDB, no SQLAlchemy,
no httpx imports. All functions are deterministic and stateless.
"""

from __future__ import annotations

from src.federation.models import FederationConfig


def filter_properties_for_federation(
    properties: dict,
    layer: str,
    config: FederationConfig,
) -> dict:
    """Filter entity/edge properties based on layer sharing rules.

    For domain/temporal layers (shared): passes all properties through.
    For provenance layer: passes only the curated D403 surface properties
    (``source_document_id``, ``extraction_date``, ``last_updated``,
    ``human_reviewed``). Missing properties handled gracefully — only
    present keys are included.
    For governance layer (siloed): passes nothing (empty dict).

    Args:
        properties: Full property dict from the entity or edge.
        layer: Graph layer name (domain, temporal, provenance, governance).
        config: Federation configuration with sharing rules.

    Returns:
        Filtered property dict.
    """
    if layer in config.shared_layers:
        return dict(properties)

    if layer == "provenance":
        # D403: curated four-property surface.
        return {
            k: v
            for k, v in properties.items()
            if k in config.provenance_surface_properties
        }

    # Governance and any unrecognised layer: siloed.
    return {}


def should_share_layer(layer: str, config: FederationConfig) -> bool:
    """Determine whether a graph layer crosses the federation boundary.

    Returns ``True`` for layers listed in ``shared_layers`` (default:
    domain, temporal). Returns ``False`` for layers in ``siloed_layers``
    (default: provenance, governance) or any unrecognised layer.

    Note: ``should_share_layer("provenance")`` returning ``False`` means
    the *full* provenance layer is not shared. However,
    ``filter_properties_for_federation()`` still exports the curated
    D403 four-property surface. The two functions compose.

    Args:
        layer: Graph layer name.
        config: Federation configuration.

    Returns:
        True if the layer should be shared across the federation boundary.
    """
    return layer in config.shared_layers


def resolve_namespace(
    type_name: str,
    registered_prefixes: list[str],
) -> str | None:
    """Reverse-lookup namespace from a type name via longest-prefix match.

    Type names follow the ``{Prefix}_Type`` convention (D402). This
    function finds the longest registered prefix that matches the start
    of ``type_name`` followed by ``_``.

    No-prefix types return ``None`` (mother-graph types by convention).

    Args:
        type_name: The entity/edge type name to resolve.
        registered_prefixes: List of registered label prefixes.

    Returns:
        The matching prefix string, or None if no prefix matches.
    """
    if not registered_prefixes:
        return None

    best: str | None = None
    best_len = 0

    for prefix in registered_prefixes:
        if type_name.startswith(prefix + "_") and len(prefix) > best_len:
            best = prefix
            best_len = len(prefix)

    return best
