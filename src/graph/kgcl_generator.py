"""KGCL Controlled Natural Language command generation from OM4OV diffs.

Translates entity-level diff output (from diff_engine.compute_entity_level_diff)
into KGCL CNL strings following the syntax from Hegde et al.
(Database, Oxford, January 2025).

No external library — simple f-string formatting.
"""

import structlog

log = structlog.get_logger()


def generate_kgcl_commands(diff_summary: dict) -> list[str]:
    """Convert an entity-level diff summary to KGCL CNL commands.

    Args:
        diff_summary: Output from diff_engine.compute_entity_level_diff().
            Format: {
                "entity_types": {"added": [...], "removed": [...], "modified": [...], "unchanged": [...]},
                "relationships": {"added": [...], "removed": [...], "modified": [...], "unchanged": [...]},
                "properties": {"added": [...], "removed": [...], "modified": [...]},
            }

    Returns:
        List of KGCL CNL strings.
    """
    commands: list[str] = []

    entity_diff = diff_summary.get("entity_types", {})
    rel_diff = diff_summary.get("relationships", {})
    prop_diff = diff_summary.get("properties", {})

    # Entity types added
    for name in entity_diff.get("added", []):
        commands.append(f"create class '{name}'")

    # Entity types removed (obsoleted, never dropped)
    for name in entity_diff.get("removed", []):
        commands.append(f"obsolete class '{name}'")

    # Entity types modified — description changes
    for item in entity_diff.get("modified", []):
        name = item.get("name", "") if isinstance(item, dict) else item
        commands.append(f"change description of '{name}'")

    # Relationships added
    for name in rel_diff.get("added", []):
        commands.append(f"create relationship '{name}'")

    # Relationships removed
    for name in rel_diff.get("removed", []):
        commands.append(f"obsolete relationship '{name}'")

    # Relationships modified
    for item in rel_diff.get("modified", []):
        name = item.get("name", "") if isinstance(item, dict) else item
        commands.append(f"change relationship '{name}'")

    # Properties added
    for prop in prop_diff.get("added", []):
        entity = prop.get("entity", "")
        prop_name = prop.get("property", "")
        commands.append(f"add property '{prop_name}' to class '{entity}'")

    # Properties removed
    for prop in prop_diff.get("removed", []):
        entity = prop.get("entity", "")
        prop_name = prop.get("property", "")
        commands.append(f"remove property '{prop_name}' from class '{entity}'")

    # Properties modified
    for prop in prop_diff.get("modified", []):
        entity = prop.get("entity", "")
        prop_name = prop.get("property", "")
        commands.append(f"change property '{prop_name}' on class '{entity}'")

    log.info("kgcl_commands_generated", count=len(commands))
    return commands
