"""Pydantic v2 JSON Schema → strict-dialect transform for grammar-constrained providers.

# D444 — to_strict_json_schema() is the shared transform helper for OpenAI-compatible
# and Anthropic providers' grammar-constrained decoding path. Authorization: D444.
"""

from __future__ import annotations

from pydantic import BaseModel


def to_strict_json_schema(model: type[BaseModel]) -> dict:
    """Transform a Pydantic v2 model's JSON Schema into OpenAI/Anthropic strict dialect.

    Steps:
    1. model.model_json_schema() → native schema.
    2. Inline $defs references: resolve all {"$ref": "#/$defs/Foo"} in place. Remove $defs.
    3. Recursively add "additionalProperties": false to every object node that declares
       properties; leave property-less object nodes (untyped dict) permissive.
    4. Promote optional fields (anyOf [{...}, {"type": "null"}]) to required-and-nullable.
    5. Return transformed dict.
    """
    schema = model.model_json_schema()
    # Inline $defs first so downstream transforms see resolved schemas
    defs = schema.pop("$defs", {})
    schema = _resolve_refs(schema, defs)
    # Apply strict transforms
    schema = _make_strict(schema)
    return schema


def _resolve_refs(node: dict | list | str | int | float | bool | None, defs: dict) -> dict | list | str | int | float | bool | None:
    """Recursively resolve all $ref pointers against the defs dict."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref_path = node["$ref"]  # e.g. "#/$defs/Foo"
            ref_name = ref_path.rsplit("/", 1)[-1]
            if ref_name in defs:
                # Resolve the referenced def (which may itself contain refs)
                resolved = _resolve_refs(dict(defs[ref_name]), defs)
                # Merge any sibling keys (e.g. "default", "description") from the ref node
                extra = {k: v for k, v in node.items() if k != "$ref"}
                if extra and isinstance(resolved, dict):
                    resolved.update(extra)
                return resolved
            return node  # unresolved ref, leave as-is
        return {k: _resolve_refs(v, defs) for k, v in node.items()}
    elif isinstance(node, list):
        return [_resolve_refs(item, defs) for item in node]
    return node


def _make_strict(node: dict | list | str | int | float | bool | None) -> dict | list | str | int | float | bool | None:
    """Recursively apply strict-mode transforms to a JSON Schema node."""
    if isinstance(node, dict):
        # First, recurse into children
        result = {k: _make_strict(v) for k, v in node.items()}

        # Promote anyOf optional patterns to required+nullable
        if "anyOf" in result:
            result = _promote_optional(result)

        # For object nodes with properties: stamp additionalProperties: false
        # and ensure all fields are required
        if result.get("type") == "object" and "properties" in result:
            result["additionalProperties"] = False
            # Ensure all property names are in required
            all_prop_names = list(result["properties"].keys())
            result["required"] = all_prop_names

        # Phase-6 fix: Anthropic strict-mode rejects ``minItems`` /
        # ``maxItems`` values other than 0/1 on array nodes
        # (``output_config.format.schema``). The Pydantic v2 validator
        # still enforces these bounds post-LLM, so dropping them from
        # the wire schema does not weaken correctness — it just lets
        # Anthropic accept the grammar. Apply the same treatment to
        # OpenAI strict-mode for parity (OpenAI silently ignores them
        # anyway).
        if result.get("type") == "array":
            min_items = result.get("minItems")
            max_items = result.get("maxItems")
            if isinstance(min_items, int) and min_items not in (0, 1):
                result.pop("minItems", None)
            if isinstance(max_items, int) and max_items not in (0, 1):
                result.pop("maxItems", None)

        # Handle items in arrays
        return result
    elif isinstance(node, list):
        return [_make_strict(item) for item in node]
    return node


def _promote_optional(node: dict) -> dict:
    """Promote anyOf [{...}, {"type": "null"}] to a single schema with nullable type.

    This handles Pydantic's representation of Optional[T] fields.
    """
    any_of = node.get("anyOf", [])
    if not isinstance(any_of, list) or len(any_of) != 2:
        return node

    null_branch = None
    type_branch = None
    for branch in any_of:
        if isinstance(branch, dict) and branch.get("type") == "null":
            null_branch = branch
        else:
            type_branch = branch

    if null_branch is None or type_branch is None:
        return node

    # Merge: take the type branch and make it nullable
    promoted = {k: v for k, v in node.items() if k != "anyOf"}

    if isinstance(type_branch, dict):
        promoted.update(type_branch)
        # Make the type nullable
        if "type" in promoted:
            promoted["type"] = [promoted["type"], "null"]
        else:
            # Complex type (e.g. object) — wrap in anyOf with null
            promoted["anyOf"] = [type_branch, {"type": "null"}]
    else:
        promoted["anyOf"] = any_of  # can't simplify, keep as-is

    return promoted
