"""Diff engine for ontology schema comparison.

Uses DeepDiff for OM4OV-style structured diffs and python-json-patch for RFC 6902 patches.
"""

import json

import jsonpatch
import structlog
from deepdiff import DeepDiff

log = structlog.get_logger()


def compute_rfc6902_patch(old_schema: dict, new_schema: dict) -> list[dict]:
    """Compute RFC 6902 JSON Patch from old_schema to new_schema.

    Uses python-json-patch (jsonpatch.make_patch).
    Returns a list of patch operation dicts.
    Returns empty list if schemas are identical.
    """
    patch = jsonpatch.make_patch(old_schema, new_schema)
    return json.loads(patch.to_string())


def apply_rfc6902_patch(schema: dict, patch_ops: list[dict]) -> dict:
    """Apply an RFC 6902 JSON Patch to a schema dict.

    Uses python-json-patch (jsonpatch.JsonPatch).
    Returns the patched schema.
    Raises ValueError if patch cannot be applied.
    """
    try:
        patch = jsonpatch.JsonPatch(patch_ops)
        return patch.apply(schema)
    except (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException) as e:
        raise ValueError(f"Failed to apply patch: {e}") from e


def compute_om4ov_diff(old_schema: dict, new_schema: dict) -> dict:
    """Compute OM4OV-style structured diff between two schemas.

    Returns dict with remain/add/update/delete categories and summary counts.
    Operates at the top-level key level for entity-type granularity.
    """
    diff = DeepDiff(old_schema, new_schema, verbose_level=2)

    added = []
    deleted = []
    updated = []

    # dictionary_item_added → "add"
    for path in diff.get("dictionary_item_added", {}):
        added.append(str(path))

    # dictionary_item_removed → "delete"
    for path in diff.get("dictionary_item_removed", {}):
        deleted.append(str(path))

    # values_changed → "update"
    for path in diff.get("values_changed", {}):
        updated.append(str(path))

    # type_changes → "update"
    for path in diff.get("type_changes", {}):
        updated.append(str(path))

    # iterable_item_added → "update"
    for path in diff.get("iterable_item_added", {}):
        updated.append(str(path))

    # iterable_item_removed → "update"
    for path in diff.get("iterable_item_removed", {}):
        updated.append(str(path))

    # Compute "remain" — all old keys not in deleted or updated
    all_changed_roots = set()
    for path in added + deleted + updated:
        all_changed_roots.add(path)

    remain = []
    for key in old_schema:
        root_path = f"root['{key}']"
        if not any(p.startswith(root_path) for p in all_changed_roots):
            remain.append(root_path)

    return {
        "remain": remain,
        "add": added,
        "update": updated,
        "delete": deleted,
        "summary": {
            "remain_count": len(remain),
            "add_count": len(added),
            "update_count": len(updated),
            "delete_count": len(deleted),
        },
    }


def compute_schema_diff(old_schema: dict, new_schema: dict) -> tuple[list[dict], dict]:
    """Convenience function that computes both RFC 6902 patch and OM4OV diff.

    Returns: (rfc6902_patch, om4ov_diff)
    """
    rfc6902 = compute_rfc6902_patch(old_schema, new_schema)
    om4ov = compute_om4ov_diff(old_schema, new_schema)
    return rfc6902, om4ov


def _extract_types_dict(schema: dict) -> dict:
    """Extract entity types dict from either flat or $defs schema structure."""
    if "entity_types" in schema:
        return schema["entity_types"]
    if "$defs" in schema:
        return schema["$defs"]
    return {}


def _extract_relationships_dict(schema: dict) -> dict:
    """Extract relationships dict from flat schema structure."""
    if "relationships" in schema:
        return schema["relationships"]
    return {}


def compute_entity_level_diff(old_schema: dict, new_schema: dict) -> dict:
    """Higher-level diff focused on entity types and relationships.

    Handles both flat GrACE format and Pydantic $defs format.
    """
    old_types = _extract_types_dict(old_schema)
    new_types = _extract_types_dict(new_schema)

    old_rels = _extract_relationships_dict(old_schema)
    new_rels = _extract_relationships_dict(new_schema)

    entity_diff = _diff_named_items(old_types, new_types)
    rel_diff = _diff_named_items(old_rels, new_rels)
    prop_diff = _diff_properties(old_types, new_types)

    return {
        "entity_types": entity_diff,
        "relationships": rel_diff,
        "properties": prop_diff,
    }


def _diff_named_items(old_items: dict, new_items: dict) -> dict:
    """Diff two dicts of named items (entity types or relationships)."""
    old_keys = set(old_items.keys())
    new_keys = set(new_items.keys())

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    common = old_keys & new_keys

    modified = []
    unchanged = []
    for name in sorted(common):
        if old_items[name] != new_items[name]:
            diff = DeepDiff(old_items[name], new_items[name], verbose_level=2)
            changes = []
            for change_type, items in diff.items():
                if isinstance(items, dict):
                    for path, detail in items.items():
                        changes.append({"type": change_type, "path": str(path), "detail": str(detail)})
                else:
                    for path in items:
                        changes.append({"type": change_type, "path": str(path)})
            modified.append({"name": name, "changes": changes})
        else:
            unchanged.append(name)

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged": unchanged,
    }


def _diff_properties(old_types: dict, new_types: dict) -> dict:
    """Diff properties across entity types."""
    added = []
    removed = []
    modified = []

    common_types = set(old_types.keys()) & set(new_types.keys())
    for type_name in sorted(common_types):
        old_props = _get_properties(old_types[type_name])
        new_props = _get_properties(new_types[type_name])

        old_prop_keys = set(old_props.keys())
        new_prop_keys = set(new_props.keys())

        for prop in sorted(new_prop_keys - old_prop_keys):
            added.append({"entity": type_name, "property": prop})
        for prop in sorted(old_prop_keys - new_prop_keys):
            removed.append({"entity": type_name, "property": prop})
        for prop in sorted(old_prop_keys & new_prop_keys):
            if old_props[prop] != new_props[prop]:
                modified.append({"entity": type_name, "property": prop})

    return {"added": added, "removed": removed, "modified": modified}


def _get_properties(type_def: dict) -> dict:
    """Extract properties from a type definition, handling both schema structures."""
    if isinstance(type_def, dict):
        return type_def.get("properties", {})
    return {}
