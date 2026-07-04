"""Thin async service layer for federation operations (Chunk 51).

This module is the sole import boundary between API routes and
stateful federation internals. Routes import ``service`` only —
never ``registry`` or ``namespace_federation`` directly.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from src.federation.models import (
    CanonicalEntity,
    FederationConfig,
    NamespaceRegistration,
)
from src.federation.namespace_federation import (
    register_federation_namespace,
    unregister_federation_namespace,
)
from src.federation.registry import CanonicalEntityRegistry
from src.federation.scope_validator import ValidationResult, validate_child_schema
from src.graph.arcade_client import ArcadeClient
from src.graph.management_models import GraphNamespace

logger = structlog.get_logger()


class FederationService:
    """Async service layer for federation operations.

    Args:
        config: Federation configuration loaded from ``config/federation.yaml``.
    """

    def __init__(self, config: FederationConfig) -> None:
        self._config = config

    def _make_registry(self, session: Session) -> CanonicalEntityRegistry:
        """Construct a registry scoped to the given session."""
        return CanonicalEntityRegistry(
            session=session,
            ollama_base_url=self._config.ollama_base_url,
            embedding_model=self._config.embedding_model,
            similarity_threshold=self._config.embedding_similarity_threshold,
        )

    async def resolve_entity(
        self,
        session: Session,
        name: str,
        entity_type: str,
        namespace: str | None = None,
    ) -> tuple[CanonicalEntity | None, str]:
        """Resolve an entity name to a canonical entity.

        Returns:
            Tuple of (entity_or_none, resolution_method).
        """
        registry = self._make_registry(session)
        return await registry.resolve(name, entity_type, namespace)

    async def list_canonical_entities(
        self,
        session: Session,
        type_filter: str | None = None,
    ) -> list[CanonicalEntity]:
        """List canonical entities, optionally filtered by type."""
        registry = self._make_registry(session)
        return await registry.list_canonicals(type_filter)

    async def register_namespace(
        self,
        db: Session,
        client: ArcadeClient,
        registration: NamespaceRegistration,
    ) -> GraphNamespace:
        """Register a federation namespace.

        Delegates to ``namespace_federation.register_federation_namespace``.
        """
        ns = GraphNamespace(
            database_name=registration.database_name,
            namespace_type=registration.namespace_type,
            label_prefix=registration.label_prefix,
            ontology_module=registration.ontology_module,
            parent_namespace_id=registration.parent_namespace_id,
            description=registration.description,
        )
        return await register_federation_namespace(db, client, ns)

    async def unregister_namespace(
        self,
        db: Session,
        client: ArcadeClient,
        namespace_name: str,
    ) -> bool:
        """Unregister a federation namespace.

        Delegates to ``namespace_federation.unregister_federation_namespace``.
        """
        return await unregister_federation_namespace(db, client, namespace_name)

    def validate_child(
        self,
        child_schema: dict,
        mother_schema: dict,
    ) -> ValidationResult:
        """Validate a child schema against the mother schema (D405)."""
        return validate_child_schema(child_schema, mother_schema)
