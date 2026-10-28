"""Internal data-only schema worker. Not an extension runtime or public CLI."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _dependencies():
    # The host derives this argv from installed module specs. stdin is data only.
    if len(sys.argv) != 3 or sys.argv[1] != "--reviewed-roots" or len(sys.argv[2]) > 32768:
        raise ValueError("invalid_dependency_bootstrap")
    roots = json.loads(sys.argv[2])
    if not isinstance(roots, list) or not 1 <= len(roots) <= 8:
        raise ValueError("invalid_dependency_bootstrap")
    for root in roots:
        if not isinstance(root, str) or len(root) > 4096:
            raise ValueError("invalid_dependency_bootstrap")
        path = Path(root)
        if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
            raise ValueError("invalid_dependency_bootstrap")
    sys.path[:0] = roots


def _schema_nodes(schema):
    """Walk schema locations; enum/const/examples and map keys are instance data."""
    stack = [schema]
    maps = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
    arrays = ("allOf", "anyOf", "oneOf", "prefixItems")
    direct = ("items", "not", "if", "then", "else", "additionalProperties", "additionalItems",
              "contains", "unevaluatedItems", "unevaluatedProperties", "propertyNames",
              "contentSchema")
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        yield node
        for key in maps:
            children = node.get(key)
            if isinstance(children, dict):
                stack.extend(child for child in children.values()
                             if isinstance(child, (dict, bool)))
        for key in arrays:
            children = node.get(key)
            if isinstance(children, list):
                stack.extend(child for child in children if isinstance(child, (dict, bool)))
        for key in direct:
            child = node.get(key)
            if isinstance(child, (dict, bool)):
                stack.append(child)
        # Draft 7 tuple items and schema-valued dependencies.
        if isinstance(node.get("items"), list):
            stack.extend(child for child in node["items"] if isinstance(child, (dict, bool)))
        dependencies = node.get("dependencies")
        if isinstance(dependencies, dict):
            stack.extend(child for child in dependencies.values()
                         if isinstance(child, (dict, bool)))


def main():
    # No untrusted Python, callbacks, plugins, schema retrieval or format hooks.
    # RLIMIT_CPU kills pathological regex/combinatorics; the parent also owns
    # kill/wait on a four-second wall deadline. Same-UID, not an OS sandbox.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    except (ImportError, OSError, ValueError):
        return 5
    try:
        _dependencies()
    except (OSError, ValueError, TypeError):
        return 5
    from jsonschema import Draft7Validator, Draft202012Validator
    from jsonschema.exceptions import SchemaError, ValidationError
    from referencing import Registry
    from referencing.exceptions import NoSuchResource

    def deny(uri):
        raise NoSuchResource(ref=uri)

    try:
        raw = sys.stdin.buffer.read(262145)
        if len(raw) > 262144:
            return 2
        data = json.loads(raw)
        schema = data["schema"]
        if not isinstance(schema, (dict, bool)):
            return 2
        dialect = schema.get("$schema") if isinstance(schema, dict) else None
        if dialect not in {None, "https://json-schema.org/draft/2020-12/schema",
                           "http://json-schema.org/draft-07/schema#"}:
            return 4
        validator = Draft7Validator if dialect == "http://json-schema.org/draft-07/schema#" else (
            Draft202012Validator)
        # A finite structural pass, independent of the schema's own keywords.
        stack, nodes = [(schema, 0)], 0
        while stack:
            value, depth = stack.pop()
            nodes += 1
            if nodes > 10000 or depth > 24:
                return 4
            if isinstance(value, dict):
                stack.extend((v, depth + 1) for v in value.values())
            elif isinstance(value, list):
                stack.extend((v, depth + 1) for v in value)
        refs, ids = [], set()
        for node in _schema_nodes(schema):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in node:
                    if not isinstance(node[key], str):
                        return 2
                    refs.append(node[key])
            if isinstance(node.get("$id"), str):
                ids.add(node["$id"].split("#")[0])
        if any(not ref.startswith("#") and ref.split("#")[0] not in ids for ref in refs):
            return 4
        validator.check_schema(schema)
        instance = validator(schema, registry=Registry(retrieve=deny))
        if not data["check"]:
            instance.validate(data["value"])
        return 0
    except SchemaError:
        return 2
    except ValidationError:
        return 3
    except (Exception, RecursionError):
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
