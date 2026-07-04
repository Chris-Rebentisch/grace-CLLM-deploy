"""Graph module — ArcadeDB integration layer."""

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.config import ArcadeConfig
from src.graph.ddl_generator import generate_full_schema_ddl, generate_meta_entity_ddl
from src.graph.index_manager import (
    DEFAULT_VERTEX_INDEXES,
    apply_pending_indexes,
    create_static_indexes,
    generate_index_ddl,
)
from src.graph.kgcl_generator import generate_kgcl_commands
from src.graph.migration_types import (
    CORRECTION_EVENT_PROPERTIES,
    META_ENTITY_TYPES,
    MIGRATION_EVENT_PROPERTIES,
)
from src.graph.schema_migration import migrate_schema
from src.graph.schema_sync import get_sync_status, preview_sync, sync_schema_to_graph
from src.graph.schema_sync_models import DDLStatement, GraphIndexRequest, GraphSchemaSyncRecord
from src.graph.system_properties import EDGE_SYSTEM_PROPERTIES, VERTEX_SYSTEM_PROPERTIES
from src.graph.type_mapping import GRACE_TO_ARCADE_TYPES, map_data_type

__all__ = [
    "ArcadeClient",
    "ArcadeConfig",
    "ArcadeDBError",
    "CORRECTION_EVENT_PROPERTIES",
    "DDLStatement",
    "DEFAULT_VERTEX_INDEXES",
    "EDGE_SYSTEM_PROPERTIES",
    "GRACE_TO_ARCADE_TYPES",
    "GraphIndexRequest",
    "GraphSchemaSyncRecord",
    "META_ENTITY_TYPES",
    "MIGRATION_EVENT_PROPERTIES",
    "VERTEX_SYSTEM_PROPERTIES",
    "apply_pending_indexes",
    "create_static_indexes",
    "generate_full_schema_ddl",
    "generate_index_ddl",
    "generate_kgcl_commands",
    "generate_meta_entity_ddl",
    "get_sync_status",
    "map_data_type",
    "migrate_schema",
    "preview_sync",
    "sync_schema_to_graph",
]
