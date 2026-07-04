"""GrACE ontology property types to ArcadeDB SQL data types."""

import structlog

logger = structlog.get_logger()

GRACE_TO_ARCADE_TYPES: dict[str, str] = {
    "string": "STRING",
    "integer": "INTEGER",
    "long": "LONG",
    "float": "DOUBLE",       # GrACE float maps to DOUBLE for precision
    "double": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "DATETIME",
    "reference": "STRING",   # stored as entity name/ID, resolved at query time
    "list": "LIST",
    "text": "STRING",        # alias
}


def map_data_type(grace_type: str) -> str:
    """Convert GrACE ontology property type to ArcadeDB type.

    Returns STRING for unknown types with structlog warning.
    """
    arcade_type = GRACE_TO_ARCADE_TYPES.get(grace_type.lower())
    if arcade_type is None:
        logger.warning("type_mapping.unknown_type", grace_type=grace_type, fallback="STRING")
        return "STRING"
    return arcade_type
