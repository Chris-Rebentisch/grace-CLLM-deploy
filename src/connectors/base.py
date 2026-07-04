"""Abstract base class for all GrACE connectors (D409).

Every concrete connector must subclass ``BaseConnector`` and:
1. Set ``connector_type`` as a class attribute.
2. Implement all five abstract methods: ``discover_schema``,
   ``check_connectivity``, ``initial_load``, ``incremental_sync``,
   ``health_check``.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from datetime import datetime

from src.connectors.models import ConnectorConfig, ConnectorHealthStatus, ConnectorRecord


class BaseConnector(abc.ABC):
    """Abstract base class defining the five-method connector contract (D409)."""

    connector_type: str

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "connector_type", None):
            raise TypeError(
                f"Connector subclass {cls.__name__} must define a "
                f"'connector_type' class attribute."
            )

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @abc.abstractmethod
    def discover_schema(self) -> dict:
        """Return a JSON-Schema-like dict describing the source system's types."""
        ...

    @abc.abstractmethod
    def check_connectivity(self) -> bool:
        """Return True if the source system is reachable."""
        ...

    @abc.abstractmethod
    async def initial_load(self) -> AsyncIterator[ConnectorRecord]:
        """Yield all records from the source system for initial ingestion."""
        ...

    @abc.abstractmethod
    async def incremental_sync(self, since: datetime) -> AsyncIterator[ConnectorRecord]:
        """Yield records modified after *since* for incremental ingestion."""
        ...

    @abc.abstractmethod
    def health_check(self) -> ConnectorHealthStatus:
        """Return a health status for this connector."""
        ...
