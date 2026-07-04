"""Adapter registry — import-time @register_adapter decorator + factory.

Mirrors ``src/connectors/registry.py``. Chunk 55, D419.

Usage::

    @register_adapter("mbox")
    class MboxAdapter(EmailAdapter):
        source_type = "mbox"
        ...

    adapter = get_adapter("mbox", config)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ingestion.adapter_base import EmailAdapter
    from src.ingestion.models import SourceConfig


_REGISTRY: dict[str, type[EmailAdapter]] = {}


def register_adapter(source_type: str):
    """Class decorator that registers an EmailAdapter subclass."""

    def decorator(cls: type[EmailAdapter]) -> type[EmailAdapter]:
        if source_type in _REGISTRY:
            raise ValueError(
                f"Duplicate adapter type '{source_type}': "
                f"already registered by {_REGISTRY[source_type].__name__}"
            )
        _REGISTRY[source_type] = cls
        return cls

    return decorator


def get_adapter(source_type: str, config: SourceConfig) -> EmailAdapter:
    """Look up a registered adapter by type string and return an instance.

    Raises:
        KeyError: if *source_type* is not registered.
    """
    if source_type not in _REGISTRY:
        raise KeyError(
            f"Unknown adapter type '{source_type}'. "
            f"Registered: {sorted(_REGISTRY.keys())}"
        )
    cls = _REGISTRY[source_type]
    return cls(config)


def list_registered() -> list[dict[str, str]]:
    """Return metadata for all registered adapter types."""
    return [
        {
            "source_type": st,
            "description": cls.__doc__ or "",
        }
        for st, cls in sorted(_REGISTRY.items())
    ]
