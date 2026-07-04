"""Connector registry — import-time @register_connector decorator + factory.

Usage::

    @register_connector("synthetic")
    class SyntheticConnector(BaseConnector):
        connector_type = "synthetic"
        ...

    connector = get_connector("synthetic", config)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.connectors.base import BaseConnector
    from src.connectors.models import ConnectorConfig


_REGISTRY: dict[str, type[BaseConnector]] = {}


def register_connector(connector_type: str):
    """Class decorator that registers a BaseConnector subclass."""

    def decorator(cls: type[BaseConnector]) -> type[BaseConnector]:
        if connector_type in _REGISTRY:
            raise ValueError(
                f"Duplicate connector type '{connector_type}': "
                f"already registered by {_REGISTRY[connector_type].__name__}"
            )
        _REGISTRY[connector_type] = cls
        return cls

    return decorator


def get_connector(connector_type: str, config: ConnectorConfig) -> BaseConnector:
    """Look up a registered connector by type string and return an instance.

    Raises:
        KeyError: if *connector_type* is not registered.
    """
    if connector_type not in _REGISTRY:
        raise KeyError(
            f"Unknown connector type '{connector_type}'. "
            f"Registered: {sorted(_REGISTRY.keys())}"
        )
    cls = _REGISTRY[connector_type]
    return cls(config)


def list_registered() -> list[dict[str, str]]:
    """Return metadata for all registered connector types."""
    return [
        {
            "connector_type": ct,
            "description": cls.__doc__ or "",
        }
        for ct, cls in sorted(_REGISTRY.items())
    ]
